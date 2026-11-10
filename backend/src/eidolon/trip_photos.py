"""Trip photos (GLOSSARY.md「外景照片」): the photo she sends the producer from a trip away
from Tokyo her schedule has her on (GLOSSARY.md「行程」), without being asked, with a few words.

Only where the deployer has schedules on and image generation, and the user has not switched
them off (GLOSSARY.md「玩法开关」, off with her schedule too); for one who has her photo tags.
Every `TICK`, each user's current conversation with each character is looked at, if it has a
turn, she has not ended it, and they were last in touch in it within `WINDOW`: a day her
schedule has her on a trip, a time is drawn for it once, between the trip's start and
`UNTIL` (or her bedtime, wherever that is earlier) of the producer's day, by five minutes.
Once it has come she sends one, one trip one photo: not
while she is cold or distant to the user, or in a cooldown, nor while a text turn is under
way or a call, when it is looked at again at the next tick; one the model fails on is tried
again `RETRY_AFTER` later, `TRIES` times in all, then let go.

She writes it as a reach-out (`ConversationEngine.send_trip_photo`), on the deployer's chat
model, with a photo tag as a turn's photo has; with others of her unit on the trip with her
(`Schedule.trip_with`), she may take one of them all (`group: yes`), drawn with each one a
character prompt of her own (`photo_prompt.group_prompt`). It is kept as a reach-out with a
photo (`Store.add_photo_reach_out`), being generated, then generated on the deployer's image
generation, counted towards no limit of the user's, and kept sent or not as a reply's photo
is. Each has an activity record of its own (`trip_photo`).
"""

import asyncio
import logging
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog, image_origin, timed
from eidolon.affinity import Affinities
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.database import by_name
from eidolon.engine import ConversationEngine
from eidolon.features import AllOn, Switches
from eidolon.image_generation import TIME_LIMIT, ImageFailed
from eidolon.live_config import LiveConfig
from eidolon.photo_prompt import ProducerLook, group_prompt, read_tag
from eidolon.producer import effective_timezone
from eidolon.randomness import Random
from eidolon.schedules import Schedule, Schedules
from eidolon.store import Conversation, NewPhoto, PhotoFailure, Store

log = logging.getLogger(__name__)

TICK = timedelta(minutes=5)
WINDOW = timedelta(days=14)
"""How long after they were last in touch she still sends one."""
UNTIL = time(22)
"""The latest, in the producer's day, she sends one: earlier if she goes to bed before it,
and never before the trip's shoot starts."""
TRIES = 3
RETRY_AFTER = timedelta(hours=1)
NOT_NOW = frozenset({"cold", "distant"})
"""The tiers she sends none in."""
_GROUP = re.compile(r"^\s*group\s*:\s*yes\b", re.IGNORECASE | re.MULTILINE)
_SHOOT = re.compile(r"外景|拍攝|拍摄|摄影|撮影|ロケ")
_NOT_SHOOT = re.compile(
    r"短会|会议|集合|确认|前往|出发|移動|移动|机场|空港|新干线|新幹線|飞机|飛行機|返回|回东京|回東京"
)


def _trip_start(schedule: Schedule) -> time | None:
    """When the day's trip's shooting begins, from the slot that reads as it: a work or live
    slot mentioning 外景/拍摄 rather than the meeting or the travel to it; a slot naming the
    trip's place where none reads so. None where no slot says it, so no photo is sent before
    a trip whose time is not known."""
    shoot = [
        slot for slot in schedule.slots
        if slot.kind in ("work", "live")
        and _SHOOT.search(f"{slot.what} {slot.where}")
        and not _NOT_SHOOT.search(slot.what)
    ]
    if not shoot:
        near = [
            slot for slot in schedule.slots
            if slot.kind in ("work", "live")
            and schedule.trip is not None
            and schedule.trip in f"{slot.what} {slot.where}"
        ]
        shoot = [slot for slot in near if not _NOT_SHOOT.search(slot.what)] or near
    return min((slot.start for slot in shoot), default=None)


_SCHEMA = """
-- When she sends the producer a photo from her trip on a day (GLOSSARY.md「外景照片」), once
-- a day: when, by the producer's day; whether she has (`sent`); how often it was tried.
CREATE TABLE IF NOT EXISTS trip_photo_due (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    due_at TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    tries INTEGER NOT NULL DEFAULT 0,
    last_at TEXT,
    PRIMARY KEY (user_id, character_id, day)
);
"""


class TripPhotos:
    def __init__(
        self,
        db: Database,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        activity: ActivityLog,
        clock: Clock,
        random: Random,
        enabled: bool,
        schedules: Schedules,
        affinities: Affinities,
        in_call: Callable[[str], bool] = lambda _: False,
        features: Switches = AllOn(),
    ) -> None:
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._activity = activity
        self._clock = clock
        self._random = random
        self._enabled = enabled
        self._schedules = schedules
        self._affinities = affinities
        self._in_call = in_call
        self._features = features
        self._drawing: set[asyncio.Task] = set()
        self._db = db
        db.prepare(_open)

    def available(self) -> bool:
        """Whether the deployer has them: schedules, and image generation now."""
        return self._enabled and self._config.image_generator() is not None

    async def _on(self, user_id: str, character: Character) -> bool:
        return (
            self.available() and await self._features.on(user_id, "trip_photos")
            and character.photo_tag is not None and character.photo_appearance is not None
        )

    async def keep_sending(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("sending trip photos failed")

    async def run_due(self) -> None:
        """Plan the day's, and send those whose time has come; one going wrong keeps none of
        the others from it."""
        if not self.available():
            return
        now = self._clock()
        for conversation, day in await self._plan(now):
            try:
                await self._send(conversation, day, now)
            except Exception:
                log.exception("sending a trip photo in conversation %s failed", conversation.id)

    async def send_now(
        self, user_id: str, character_id: str, place: str = "京都", trip_with: tuple[str, ...] = (),
        all_together: bool = False,
    ) -> bool:
        """Have her send the user a photo from a trip to `place` now, with `trip_with` there
        too, whatever her schedule says: for trying it out under the development sign-in.
        False if they have no conversation, or image generation is off."""
        conversation = await self._current(user_id, character_id)
        if conversation is None or not self.available():
            return False
        await self._send(conversation, None, self._clock(), place, trip_with, all_together)
        return True

    async def _plan(self, now: datetime) -> list[tuple[Conversation, date]]:
        """Draw a time for each day's trip not drawn for yet; those whose time has come."""
        due: list[tuple[Conversation, date]] = []
        for current in await self._store.current_conversations():
            conversation, contact = current.conversation, current.last_contact
            character = self._characters.get(conversation.character_id)
            if character is None or current.ended or not current.has_turns:
                continue
            if contact is None or now - contact > WINDOW:
                continue
            if not await self._on(conversation.user_id, character):
                continue
            zone = await self._zone(conversation.user_id)
            day = now.astimezone(zone).date()
            schedule = await self._schedules.of(conversation.user_id, character.id, day)
            if schedule is None or schedule.trip is None:
                continue
            trip_start = _trip_start(schedule)
            if trip_start is None:
                continue
            first = datetime.combine(day, trip_start, zone)
            end = UNTIL
            if schedule.sleep is not None and not schedule.sleeps_after_midnight:
                end = min(end, schedule.sleep)
            last = datetime.combine(day, end, zone)
            if last < first:
                continue
            row = await self._db.read(_row, conversation.user_id, character.id, day)
            if row is None:
                at = first
                minutes = int((last - first).total_seconds() // 60)
                if minutes >= 5:
                    at += timedelta(minutes=5 * self._random(minutes // 5 + 1))
                row = await self._db.write(_planned, conversation.user_id, character.id, day, at)
            elif not row["sent"] and datetime.fromisoformat(row["due_at"]) < first:
                # Planned before this deploy, or before the trip's time was known: send no
                # earlier than the trip begins.
                row = await self._db.write(
                    _rescheduled, conversation.user_id, character.id, day, first
                )
            if row["sent"] or row["tries"] >= TRIES:
                continue
            if datetime.fromisoformat(row["due_at"]) > now:
                continue
            if row["last_at"] and now - datetime.fromisoformat(row["last_at"]) < RETRY_AFTER:
                continue
            due.append((conversation, day))
        return due

    async def _send(
        self, conversation: Conversation, day: date | None, now: datetime, place: str | None = None,
        trip_with: tuple[str, ...] = (), all_together: bool = False,
    ) -> None:
        user_id, character_id = conversation.user_id, conversation.character_id
        status = await self._affinities.status(user_id, character_id)
        if status is not None and (status.tier in NOT_NOW or status.cooling):
            return
        if self._store.is_taking_turn(conversation.id) or self._in_call(conversation.id):
            return
        if day is not None:
            schedule = await self._schedules.of(user_id, character_id, day)
            if schedule is None or schedule.trip is None:
                return
            place, trip_with = schedule.trip, schedule.trip_with
        assert place is not None
        others = [
            self._characters[c] for c in trip_with
            if c in self._characters and self._characters[c].photo_tag is not None
            and self._characters[c].photo_appearance is not None
        ]
        generator = self._config.image_generator()
        if generator is None:
            return
        recording = self._activity.begin(
            "trip_photo", user_id, character_id, own_llm=False, exempt=False
        )
        lease = self._config.lease()
        try:
            with recording.current():
                said, tag = await self._engine.send_trip_photo(
                    conversation, lease.keys.chat_llm, place, [f"{c.name}（{c.id}）" for c in others],
                    all_together and bool(others),
                )
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("a trip photo in conversation %s failed: %s", conversation.id, type(e).__name__)
            await recording.finish("failed")
            if day is not None:
                await self._db.write(_tried, user_id, character_id, day, now)
            return
        finally:
            lease.release()
        if day is not None:
            await self._db.write(_sent, user_id, character_id, day)
        await self._sent_with_photo(conversation, said, tag, others, recording)

    async def _sent_with_photo(
        self, conversation: Conversation, said: list, tag: str, others: list[Character],
        recording,
    ) -> None:
        """Keep what she said with the photo `tag` says, being generated, and draw it, of her
        and of `others` if her tag says so."""
        character = self._characters[conversation.character_id]
        photo_id = uuid.uuid4().hex
        reach_out = await self._store.add_photo_reach_out(
            conversation.id, said, NewPhoto(photo_id, tag)
        )
        generator = self._config.image_generator()
        if reach_out is None or generator is None:
            if reach_out is not None:
                await self._store.finish_photo(photo_id, None, "service_error")
            await recording.finish("failed")
            return
        settings = generator.settings
        read = replace(read_tag(tag), you=None)
        look: ProducerLook = (
            "female" if (await self._store.get_profile(conversation.user_id)).gender == "female"
            else "male"
        )
        together = others if _GROUP.search(tag) else []
        prompt = group_prompt([character, *together], read, settings.style, look)
        await self._draw(photo_id, generator, prompt, read.stripped, recording)

    async def _draw(self, photo_id: str, generator, prompt, stripped, recording) -> None:
        settings = generator.settings
        image: bytes | None = None
        reason: PhotoFailure | None = None
        until = asyncio.get_running_loop().time() + TIME_LIMIT
        try:
            with recording.current():
                image = (await timed(
                    self._activity, image_origin(settings), generator.generate(prompt, until),
                    stripped,
                )).webp
        except ImageFailed as e:
            log.warning("trip photo %s was not generated: %s", photo_id, e)
            reason = e.reason
        except Exception:
            log.exception("generating trip photo %s failed", photo_id)
            reason = "service_error"
        await self._store.finish_photo(photo_id, image, reason)
        await recording.finish("success" if image is not None else "failed")

    async def _current(self, user_id: str, character_id: str) -> Conversation | None:
        return next(
            (
                c for c, _, _ in await self._store.latest_conversations(user_id)
                if c.character_id == character_id
            ),
            None,
        )

    async def _zone(self, user_id: str) -> ZoneInfo:
        return ZoneInfo(effective_timezone(await self._store.get_profile(user_id), None))

    def close(self) -> None:
        for task in self._drawing:
            task.cancel()


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _row(db: sqlite3.Connection, user_id: str, character_id: str, day: date) -> sqlite3.Row | None:
    return by_name(db).execute(
        "SELECT * FROM trip_photo_due WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day.isoformat()),
    ).fetchone()


def _planned(
    db: sqlite3.Connection, user_id: str, character_id: str, day: date, at: datetime
) -> sqlite3.Row:
    db.execute(
        "INSERT OR IGNORE INTO trip_photo_due (user_id, character_id, day, due_at)"
        " VALUES (?, ?, ?, ?)",
        (user_id, character_id, day.isoformat(), at.astimezone(UTC).isoformat()),
    )
    row = _row(db, user_id, character_id, day)
    assert row is not None
    return row


def _rescheduled(
    db: sqlite3.Connection, user_id: str, character_id: str, day: date, at: datetime
) -> sqlite3.Row:
    db.execute(
        "UPDATE trip_photo_due SET due_at = ?, tries = 0, last_at = NULL"
        " WHERE user_id = ? AND character_id = ? AND day = ?",
        (at.astimezone(UTC).isoformat(), user_id, character_id, day.isoformat()),
    )
    row = _row(db, user_id, character_id, day)
    assert row is not None
    return row


def _sent(db: sqlite3.Connection, user_id: str, character_id: str, day: date) -> None:
    db.execute(
        "UPDATE trip_photo_due SET sent = 1 WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day.isoformat()),
    )


def _tried(
    db: sqlite3.Connection, user_id: str, character_id: str, day: date, now: datetime
) -> None:
    db.execute(
        "UPDATE trip_photo_due SET tries = tries + 1, last_at = ?"
        " WHERE user_id = ? AND character_id = ? AND day = ?",
        (now.astimezone(UTC).isoformat(), user_id, character_id, day.isoformat()),
    )
