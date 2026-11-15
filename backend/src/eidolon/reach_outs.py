"""Reach-outs (GLOSSARY.md「搭话」): what she says first in her current conversation with the
user, the user not talking to her, once her time comes; whether or not the user is there to
see it.

Only where the deployer turns them on (`LiveConfig.reach_outs`). Every `TICK` the backend
runs, each user's current conversation with each character (GLOSSARY.md「当前对话」) is looked
at, if they were last in touch in it within `WINDOW`: when her last turn was recorded, or a
call that connected ended, whichever was later; gifts are not being in touch. Her time is
then the last contact and a time drawn from her tier's range (`INTERVALS`), at a place in it
drawn once for each contact (`PLACES`), drawn anew as the contact changes; trust's where
affinity is off. A conversation she is not to reach out in is passed over, its time left as
it is, to be looked at again at the next tick: one she has ended; one without a turn yet;
one she has already reached out in since the last contact as many times as her tier's
`SILENCE` allows; one she is in a cooldown in, or cold or distant; one with a text turn under way or a
call; one of a user banned, or one an admin lets take no turns on the deployer's keys; one of
a user who has switched their own switch off (`switch`), on by default.

Her time goes by the producer's day, in the profile's time zone (or `Asia/Shanghai`): one
that falls in the `NIGHT`, or comes while it is night, is put off to the `MORNING` after,
at a place in it drawn once for each contact. One past `WINDOW` after the last contact is
given up till they are in touch again. Those whose time has come at a tick come in the order
of their times, but no two to the same user within `MIN_GAP`, and no character more than
twice in the producer's day; either is put off, not dropped. One whose time came
before the backend started is not made up for, nor one that fails, the model failing or
saying nothing she could say: each is put off from now by a time drawn anew from her tier's
range.

She may say more than one thing into a silence, up to `SILENCE` by her tier, each a sixth
of her interval after the one before (`FOLLOW_UP`). If the user has left a question of hers
unanswered within `NUDGE_WITHIN`, she comes back sooner — at least `NUDGE_FLOOR` after it,
and at a sixth of her interval — and is told what she asked, so she may ask again, answer
it herself, or let it go. The producer's `MIN_GAP`, the character's two a day, and the
night still hold.

She reaches out on the deployer's chat model as text turns use it, whoever's LLM the user
brings, held only while she does (`ConversationEngine.reach_out`): nothing counts towards the
user's limits. Each reach-out has an activity record of its own (`reach_out`), on the
deployer's keys, its service calls under it, so that the dashboard's usage and costs count
them; one that fails is recorded failed; one the user and she were in touch meanwhile,
looked at again as it is recorded (`Store.add_reach_out`), is not recorded, refused.
"""

import asyncio
import logging
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog
from eidolon.affinity import Affinities, Status, Tier
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.engine import ConversationEngine
from eidolon.live_config import LiveConfig
from eidolon.producer import ProducerProfile, effective_timezone
from eidolon.randomness import Random
from eidolon.store import (
    Conversation,
    CurrentConversation,
    Message,
    ReachOut,
    Segment,
    ReachOutRefused,
    Store,
    read_profile,
)
from eidolon.usage import UserLimits, read_user_limits

log = logging.getLogger(__name__)

TICK = timedelta(minutes=1)
"""How often it is looked whose time has come."""

WINDOW = timedelta(days=7)
"""How long after the last contact she may still reach out."""

INTERVALS: dict[Tier, tuple[timedelta, timedelta]] = {
    "intimate": (timedelta(hours=3), timedelta(hours=6)),
    "close": (timedelta(hours=8), timedelta(hours=16)),
    "trust": (timedelta(hours=20), timedelta(hours=36)),
}
"""How long after the last contact she reaches out, by her tier: the closer, the sooner. None
in the tiers not here."""

PLACES = 1000
"""How many places in a tier's range her time may be drawn at, evenly apart; and in the
`MORNING`."""

NIGHT = (time(22), time(8))
"""When in the producer's day she does not reach out: from the first to the second."""

MORNING = timedelta(hours=2)
"""How long after the night ends her time put off to the morning may be."""

CALL_BACK_AFTER = timedelta(minutes=10)
"""How long after a call she did not answer in the bath she comes back at the soonest."""

CALL_BACK_WITHIN = timedelta(hours=2)
"""How long after a call she did not answer she still comes back for it."""

MIN_GAP = timedelta(hours=3)
"""How long after any reach-out to a user another may come, whichever character's."""

SILENCE: dict[Tier, int] = {"intimate": 3, "close": 2, "trust": 2}
"""How many words she may say into one silence — the user not answering — by her tier."""

FOLLOW_UP = 6
"""How much sooner each word into a silence comes than the first: a sixth of the tier's
interval, so a follow-up arrives while the user may still be there."""

NUDGE_FLOOR = timedelta(minutes=45)
"""How soon at the least she picks an unanswered question back up."""

NUDGE_WITHIN = timedelta(hours=12)
"""How long after her question it is still picked back up."""

_OPENING_CUE = re.compile(r"^\[[^]]+\]\s*")
_ASKS = re.compile(r"[？?]$|(かい?|かな|かしら|かなぁ)$")

_SCHEMA = """
-- When she is to reach out in a conversation (GLOSSARY.md「搭话」): the last contact it was
-- drawn for (`contact_at`), and where in her tier's range (`place`, from 0 to 1). The other
-- two are for times put off: to the morning, where in its hours; and a time set instead.
CREATE TABLE IF NOT EXISTS reach_out_schedules (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
    contact_at TEXT NOT NULL,
    place REAL NOT NULL,
    morning_place REAL,
    due_at TEXT
);

-- The users who have turned reach-outs off, by their own switch; on for all the others.
CREATE TABLE IF NOT EXISTS reach_outs_off (
    user_id TEXT PRIMARY KEY REFERENCES users(id)
);
"""


class ReachOutsOff(Exception):
    """The deployer has reach-outs off: no user's own switch may be set."""


@dataclass(frozen=True)
class _Due:
    """A conversation whose time has come, for the last contact in it: her tier, her time,
    and the producer's time zone."""

    conversation: Conversation
    contact: datetime
    tier: Tier
    at: datetime
    zone: ZoneInfo
    topic: str | None = None
    """Her last question the user has not answered, if one is to be picked back up."""


@dataclass(frozen=True)
class _Schedule:
    """When she is to reach out in a conversation, as `reach_out_schedules` keeps it."""

    contact_at: str
    """The last contact it was drawn for, as an ISO timestamp."""
    place: float
    morning: float | None
    due_at: str | None


@dataclass(frozen=True)
class _Standing:
    """What is read, at one moment, of a conversation and its user to tell whether and when
    she reaches out in it."""

    limits: UserLimits | None
    switched_on: bool
    status: Status | None
    """Her affinity, None where affinity is off."""
    schedule: _Schedule | None
    profile: ProducerProfile


class ReachOuts:
    """Her reach-outs, in `db` beside the conversations of `store`, on the deployer's models
    of `config`, each recorded in `activity`, not to users whose own limit on turns is 0;
    `in_call` says whether there is a call in a conversation, and `random` draws when. A time
    that came before `clock` says it is as they begin is missed."""

    def __init__(
        self,
        db: Database,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        affinities: Affinities,
        activity: ActivityLog,
        in_call: Callable[[str], bool],
        clock: Clock,
        random: Random,
    ) -> None:
        self._db = db
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._affinities = affinities
        self._activity = activity
        self._in_call = in_call
        self._clock = clock
        self._random = random
        self._started = clock()
        db.prepare(_open)

    async def keep_reaching_out(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("reaching out failed")

    async def run_due(self) -> None:
        """Reach out in every conversation whose time has come, one after another in the order
        of their times; one going wrong keeps none of the others from it."""
        if not self._config.reach_outs.enabled:
            return
        now = self._clock()
        due: list[_Due] = []
        for current in await self._store.current_conversations():
            try:
                if (found := await self._due(current, now)) is not None:
                    due.append(found)
            except Exception:
                log.exception("reaching out in conversation %s failed", current.conversation.id)
        for found in sorted(due, key=lambda d: d.at):
            try:
                if await self._within_the_day(found, now):
                    await self._reach_out(found, now)
            except Exception:
                log.exception("reaching out in conversation %s failed", found.conversation.id)

    async def keepsake_given(
        self, user_id: str, character_id: str, name: str, at: datetime,
        said: Sequence[Segment],
    ) -> bool:
        """Have her say `said` first in the current conversation, where a keepsake was just
        given: a reach-out of its own, not a scheduled one, so it still respects the user's
        switch and never lands in a turn or a call. False if it could not be said."""
        if not self._config.reach_outs.enabled:
            return False
        if not await self._db.read(_switched_on, user_id):
            return False
        current = next(
            (c for c in await self._store.current_conversations()
             if c.conversation.user_id == user_id
             and c.conversation.character_id == character_id),
            None,
        )
        if current is None or current.ended or not current.has_turns:
            return False
        if current.last_contact is None or self._clock() - current.last_contact > WINDOW:
            return False
        last = await self._db.read(_last_reached_out, user_id)
        if last is not None and self._clock() - last < MIN_GAP:
            return False
        if self._talking(current.conversation.id):
            return False
        recording = self._activity.begin(
            "reach_out", user_id, character_id, own_llm=False, exempt=False,
        )
        try:
            reach_out = await self._store.add_reach_out(
                current.conversation.id, list(said), current.last_contact, False,
                at.astimezone(UTC).isoformat(),
            )
        except ReachOutRefused:
            await recording.finish("rejected")
            return False
        except Exception:
            log.exception("announcing keepsake %s failed", name)
            await recording.finish("failed")
            return False
        await recording.finish("success")
        await self._store.reach_out_recorded_as(reach_out.id, recording.row_id)
        return True

    async def switch_of(self, user_id: str) -> bool | None:
        """Whether the user has their own switch on, as it is by default; None where the
        deployer has reach-outs off."""
        if not self._config.reach_outs.enabled:
            return None
        return await self._db.read(_switched_on, user_id)

    async def switch(self, user_id: str, on: bool) -> None:
        """Switch the user's own switch on or off, for all the characters, from the next tick;
        raise `ReachOutsOff` where the deployer has reach-outs off."""
        if not self._config.reach_outs.enabled:
            raise ReachOutsOff
        await self._db.write(_switch, user_id, on)

    async def _due(self, current: CurrentConversation, now: datetime) -> _Due | None:
        """Her time to reach out in the conversation, if it has come and she is to; one that
        came before the backend started is put off instead."""
        conversation, contact = current.conversation, current.last_contact
        if conversation.character_id not in self._characters or current.ended:
            return None
        if not current.has_turns or contact is None:
            return None
        if now - contact > WINDOW:
            return None
        standing = await self._db.read(self._standing, conversation, self._affinities.on)
        if standing.limits is not None and standing.limits.turns_per_hour == 0:
            return None
        if not standing.switched_on:
            return None
        tier = _tier(standing.status)
        if tier is None:
            return None
        into_silence = await self._db.read(
            _reach_out_count_since, conversation.id, contact
        )
        if into_silence >= SILENCE[tier]:
            return None
        zone = ZoneInfo(effective_timezone(standing.profile, None))
        # A call she did not answer in the bath has her come back for it, once it is over.
        missed = await self._store.missed_call_after(conversation.id, contact)
        if missed is not None and now - missed <= CALL_BACK_WITHIN:
            if await self._engine.bath_ends(conversation) is not None:  # still in the bath
                return None
            if now - missed >= CALL_BACK_AFTER and not self._talking(conversation.id):
                return _Due(conversation, contact, tier, now, zone)
            return None
        place, morning, due = await self._schedule(conversation.id, contact, standing.schedule)
        if due is None:
            due = contact + self._interval(tier, place)
        topic: str | None = None
        if into_silence == 0:
            if now - contact <= NUDGE_WITHIN:
                topic = await self._open_topic(conversation.id)
            if topic is not None:
                due = max(
                    contact + NUDGE_FLOOR,
                    contact + self._interval(tier, place) / FOLLOW_UP,
                )
        elif current.reached_out_at is not None:
            due = (
                current.reached_out_at
                + self._interval(tier, place) / FOLLOW_UP
            )
            if now - current.reached_out_at <= NUDGE_WITHIN:
                topic = await self._open_topic(conversation.id)
        if _at_night(due, zone):
            night = due
        elif due <= now and _at_night(now, zone):
            night = now
        else:
            night = None
        if night is not None:
            if morning is None:
                morning = self._draw()
                await self._db.write(_set_morning, conversation.id, contact.isoformat(), morning)
            due = _morning_after(night, zone) + MORNING * morning
        if now < due:  # one past `WINDOW` never comes, as none is looked at then
            return None
        if self._talking(conversation.id):
            return None
        if due < self._started:  # missed, the backend not running
            await self._put_off(conversation.id, contact, now + self._interval(tier))
            return None
        return _Due(conversation, contact, tier, due, zone, topic)

    def _standing(
        self, db: sqlite3.Connection, conversation: Conversation, affinity_on: bool
    ) -> _Standing:
        """What is read of the conversation and its user, through `db`; her affinity only
        while it is `affinity_on`."""
        user_id = conversation.user_id
        return _Standing(
            read_user_limits(db, user_id),
            _switched_on(db, user_id),
            self._affinities.read_status(db, user_id, conversation.character_id)
            if affinity_on else None,
            _read_schedule(db, conversation.id),
            read_profile(db, user_id),
        )

    async def _within_the_day(self, due: _Due, now: datetime) -> bool:
        """Whether this reach-out may come now: at least `MIN_GAP` after any character's
        last one, and the character's first two today. Otherwise it is put off, not dropped."""
        today = now.astimezone(due.zone).date()
        began = datetime.combine(today, time(), due.zone)
        last = await self._db.read(_last_reached_out, due.conversation.user_id)
        if last is not None:
            gap = now - last
            if gap < MIN_GAP:
                await self._put_off(due.conversation.id, due.contact, last + MIN_GAP)
                return False
        already = await self._db.read(
            _character_reached_out_count_since, due.conversation.user_id,
            due.conversation.character_id, began.astimezone(UTC),
        )
        if already >= 2:
            tomorrow = datetime.combine(today + timedelta(days=1), NIGHT[1], due.zone)
            await self._put_off(due.conversation.id, due.contact,
                                tomorrow + MORNING * self._draw())
            return False
        return True

    async def _open_topic(self, conversation_id: str) -> str | None:
        """Her last line, if it was a question the user has not answered."""
        record = await self._store.record(conversation_id)
        if not record:
            return None
        last = record[-1]
        if isinstance(last, Message):
            if last.role != "character" or not last.segments:
                return None
        elif isinstance(last, ReachOut):
            if not last.segments:
                return None
        else:
            return None
        line = _OPENING_CUE.sub("", last.segments[-1].ja).strip().splitlines()
        if not line:
            return None
        said = line[-1].strip()
        return said[:120] if _ASKS.search(said) else None

    def _talking(self, conversation_id: str) -> bool:
        """Whether a text turn is under way in the conversation, or a call."""
        return self._store.is_taking_turn(conversation_id) or self._in_call(conversation_id)

    def _interval(self, tier: Tier, place: float | None = None) -> timedelta:
        """How long after a contact she reaches out in `tier`, at `place` in its range, or at
        one drawn."""
        low, high = INTERVALS[tier]
        return low + (high - low) * (self._draw() if place is None else place)

    def _draw(self) -> float:
        return self._random(PLACES) / PLACES

    async def _schedule(
        self,
        conversation_id: str,
        contact: datetime,
        schedule: _Schedule | None,
    ) -> tuple[float, float | None, datetime | None]:
        """Where in her tier's range her time is, drawn for the last `contact`; where in the
        morning, if drawn yet; and the time set instead, if one is: as read in `schedule`,
        or, if that was drawn for another contact, drawn now."""
        at = contact.isoformat()
        if schedule is None or schedule.contact_at != at:
            schedule = await self._db.write(_schedule_for, conversation_id, at, self._draw())
        due = schedule.due_at
        return schedule.place, schedule.morning, datetime.fromisoformat(due) if due else None

    async def _put_off(self, conversation_id: str, contact: datetime, due: datetime) -> None:
        """Set her time for `contact` to `due` instead."""
        await self._db.write(_set_due, conversation_id, contact.isoformat(), due.isoformat())

    async def _reach_out(self, due: _Due, now: datetime) -> None:
        """Reach out, unless the user and she have begun talking since her time was looked
        at, as those before took their time."""
        conversation, contact = due.conversation, due.contact
        if self._talking(conversation.id):
            return
        recording = self._activity.begin(
            "reach_out", conversation.user_id, conversation.character_id,
            own_llm=False, exempt=False,
        )
        lease = self._config.lease()
        try:
            with recording.current():
                # Written for the deployer's model to read aloud: the user's is not known here.
                said = await self._engine.reach_out(
                    conversation, lease.keys.chat_llm, lease.keys.read_aloud_model,
                    topic=due.topic,
                )
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("reaching out in conversation %s failed: %s", conversation.id,
                        type(e).__name__)
            await recording.finish("failed")
            await self._put_off(conversation.id, contact, now + self._interval(due.tier))
            return
        finally:
            lease.release()
        try:
            reach_out = await self._store.add_reach_out(
                conversation.id, said, contact, self._in_call(conversation.id),
                now.astimezone(UTC).isoformat(),
            )
        except ReachOutRefused:  # they were in touch meanwhile
            await recording.finish("rejected")
            return
        await recording.finish("success")
        await self._store.reach_out_recorded_as(reach_out.id, recording.row_id)


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _tier(status: Status | None) -> Tier | None:
    """The tier she reaches out by, trust where affinity is off (`status` None); None if she
    is not to, in a cooldown or in a tier without a range."""
    if status is None:
        return "trust"
    if status.cooling or status.tier not in INTERVALS:
        return None
    return status.tier


def _reach_out_count_since(
    db: sqlite3.Connection, conversation_id: str, since: datetime
) -> int:
    """How many reach-outs the conversation has had since `since`."""
    [count] = db.execute(
        "SELECT COUNT(*) FROM reach_outs WHERE conversation_id = ? AND created_at >= ?",
        (conversation_id, since.isoformat()),
    ).fetchone()
    return count


def _last_reached_out(db: sqlite3.Connection, user_id: str) -> datetime | None:
    """When any character last reached out to the user, if any has."""
    row = db.execute(
        "SELECT MAX(r.created_at) FROM reach_outs r"
        " JOIN conversations c ON c.id = r.conversation_id WHERE c.user_id = ?",
        (user_id,),
    ).fetchone()
    return datetime.fromisoformat(row[0]) if row[0] is not None else None


def _character_reached_out_count_since(
    db: sqlite3.Connection, user_id: str, character_id: str, since: datetime
) -> int:
    """How many times the character has reached out to the user since `since`."""
    [count] = db.execute(
        "SELECT COUNT(*) FROM reach_outs r JOIN conversations c ON c.id = r.conversation_id"
        " WHERE c.user_id = ? AND c.character_id = ? AND r.created_at >= ?",
        (user_id, character_id, since.isoformat()),
    ).fetchone()
    return count


def _switched_on(db: sqlite3.Connection, user_id: str) -> bool:
    row = db.execute("SELECT 1 FROM reach_outs_off WHERE user_id = ?", (user_id,)).fetchone()
    return row is None


def _switch(db: sqlite3.Connection, user_id: str, on: bool) -> None:
    if on:
        db.execute("DELETE FROM reach_outs_off WHERE user_id = ?", (user_id,))
    else:
        db.execute("INSERT OR IGNORE INTO reach_outs_off (user_id) VALUES (?)", (user_id,))


def _read_schedule(db: sqlite3.Connection, conversation_id: str) -> _Schedule | None:
    row = db.execute(
        "SELECT contact_at, place, morning_place, due_at FROM reach_out_schedules"
        " WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return _Schedule(*row) if row is not None else None


def _schedule_for(
    db: sqlite3.Connection, conversation_id: str, contact_at: str, place: float
) -> _Schedule:
    """The schedule of the conversation for the contact at `contact_at`: as it is, if it is
    for that contact already, else drawn anew at `place`."""
    schedule = _read_schedule(db, conversation_id)
    if schedule is not None and schedule.contact_at == contact_at:
        return schedule
    db.execute(
        "INSERT OR REPLACE INTO reach_out_schedules (conversation_id, contact_at, place)"
        " VALUES (?, ?, ?)",
        (conversation_id, contact_at, place),
    )
    return _Schedule(contact_at, place, None, None)


def _set_morning(
    db: sqlite3.Connection, conversation_id: str, contact_at: str, morning: float
) -> None:
    """Set where in the morning her time for the contact at `contact_at` is, put off from
    the night."""
    db.execute(
        "UPDATE reach_out_schedules SET morning_place = ?"
        " WHERE conversation_id = ? AND contact_at = ?",
        (morning, conversation_id, contact_at),
    )


def _set_due(db: sqlite3.Connection, conversation_id: str, contact_at: str, due_at: str) -> None:
    db.execute(
        "UPDATE reach_out_schedules SET due_at = ? WHERE conversation_id = ? AND contact_at = ?",
        (due_at, conversation_id, contact_at),
    )


def _at_night(at: datetime, zone: ZoneInfo) -> bool:
    local = at.astimezone(zone).time()
    return local >= NIGHT[0] or local < NIGHT[1]


def _morning_after(at: datetime, zone: ZoneInfo) -> datetime:
    """When the night `at` is in ends."""
    local = at.astimezone(zone)
    day = local.date() if local.time() < NIGHT[1] else local.date() + timedelta(days=1)
    return datetime.combine(day, NIGHT[1], zone)
