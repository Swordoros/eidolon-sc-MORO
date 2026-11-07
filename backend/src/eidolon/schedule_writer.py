"""Writing her schedule for each day (GLOSSARY.md「行程」), ahead of the turns that read it.

Only where the deployer turns it on (`Settings.schedules`). Every `TICK`, each user's current
conversation with each character (GLOSSARY.md「当前对话」) is looked at, if it has a turn, she
has not ended it, and they were last in touch in it within `WINDOW`: from `WRITE_FROM` of the
producer's day, in the profile's time zone, her schedule for it is written if it has not been
tried, `PER_TICK` at most at a tick, the oldest contacts first. The day's twists are drawn on
`random` (`schedules.TWISTS`, none to `MAX_TWISTS`), and she is told the day before's to go
on from. One the model fails on, or writes nothing usable in, is kept as none and not tried
again that day. Written on the deployer's chat model as letters are, each with an activity
record of its own (`schedule`). A turn never waits for one: without it, she has none that day.
A night she goes to bed, her bath before it is drawn on `random` too (`schedules.draw_bath`),
in the tub unless her heart says she showers (GLOSSARY.md「洗澡」). A day she has a live away
from Tokyo, or one day in `TRIP_ODDS` otherwise (a twist of its own), she is away on a trip,
kept with the day, for the souvenir she brings the day after (GLOSSARY.md「纪念品」); a day
the producer invited her out on (GLOSSARY.md「邀约」), that too, a twist of its own. One trip
in `TOGETHER_ODDS`, others of her unit whose day is not written yet go with her; their days,
written after, have them go on it too (GLOSSARY.md「外景照片」).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.engine import ConversationEngine
from eidolon.features import AllOn, Switches
from eidolon.live_config import LiveConfig
from eidolon.producer import effective_timezone
from eidolon.randomness import Random
from eidolon.schedules import (
    TRIP_ODDS,
    TRIP_PLACES,
    TRIP_TWIST,
    TRIP_WITH_TWIST,
    DATE_TWIST,
    TOGETHER_ODDS,
    TWISTS,
    Schedules,
    draw_bath,
    parse_schedules_batched,
)
from eidolon.schedules import said as schedule_said
from eidolon.store import Store

log = logging.getLogger(__name__)

TICK = timedelta(minutes=30)
"""How often it is looked whose schedule is to be written, and the units that could not be
are retried."""

WRITE_FROM = time(4)
"""From when in the producer's day their day's schedules are written."""

MAX_TWISTS = 2
"""How many twists a day has at most."""


async def _no_date(*_: object) -> str | None:
    return None


async def _no_favors(*_: object) -> list[str]:
    return []


class ScheduleWriter:
    def __init__(
        self,
        schedules: Schedules,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        activity: ActivityLog,
        clock: Clock,
        random: Random,
        enabled: bool,
        features: Switches = AllOn(),
        date_on: Callable[[str, str, date], Awaitable[str | None]] = _no_date,
        favors_on: Callable[[str, str, date], Awaitable[list[str]]] = _no_favors,
    ) -> None:
        """`date_on` says what she is to do with the producer on a day, if anything
        (GLOSSARY.md「邀约」); `favors_on`, what the producer's favors have her do that day
        (GLOSSARY.md「拜托」), as twists of their own."""
        self._date_on = date_on
        self._favors_on = favors_on
        self._features = features
        self._schedules = schedules
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._activity = activity
        self._clock = clock
        self._random = random
        self._enabled = enabled

    async def keep_writing(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("writing schedules failed")

    async def run_due(self) -> None:
        """Write the schedules due: each unit's members together, in one call; one going
        wrong keeps none of the others from it. The days long gone are forgotten."""
        if not self._enabled:
            return
        now = self._clock()
        due = await self._due(now)
        for user_id, unit, day in due:
            try:
                await self._write_unit(user_id, unit, day)
            except Exception:
                log.exception("writing the schedules of %s for %s failed", unit, user_id)
        await self._schedules.sweep(now.date())

    async def write_now(self, user_id: str, character_id: str) -> bool:
        """Have her unit's schedules for today written now, whatever the hour and whether it
        was: for trying it out under the development sign-in. False if she is no character."""
        if character_id not in self._characters:
            return False
        group = next(g for g in self._engine.schedule_groups() if character_id in g)
        await self._write_unit(user_id, group, await self._today(user_id, self._clock()))
        return True

    async def _today(self, user_id: str, now: datetime) -> date:
        zone = ZoneInfo(effective_timezone(await self._store.get_profile(user_id), None))
        return now.astimezone(zone).date()

    async def _due(self, now: datetime) -> list[tuple[str, list[str], date]]:
        """The units whose day's schedule is not written yet, for each user with the feature
        on, after `WRITE_FROM` in their time zone: the user, the unit's members, the day. A
        unit is due while any of its members is missing, so that they are written together."""
        groups = self._engine.schedule_groups()
        found: list[tuple[str, list[str], date]] = []
        for user in await self._store.list_users():
            if not await self._features.on(user.id, "schedules"):
                continue
            profile = await self._store.get_profile(user.id)
            zone = ZoneInfo(effective_timezone(profile, None))
            local = now.astimezone(zone)
            if local.time() < WRITE_FROM:
                continue
            day = local.date()
            for group in groups:
                missing = False
                for cid in group:
                    if not await self._schedules.tried(user.id, cid, day):
                        missing = True
                        break
                if missing:
                    found.append((user.id, group, day))
        return found

    def _twists(self) -> list[str]:
        left = list(TWISTS)
        drawn = []
        for _ in range(self._random(MAX_TWISTS + 1)):
            drawn.append(left.pop(self._random(len(left))))
        return drawn

    async def _write_unit(self, user_id: str, unit: list[str], day: date) -> None:
        """Write the day's schedules of the unit's members together, in one call, so that
        they do not conflict; a member whose part cannot be read is left to be retried."""
        names = {c.id: c.name for c in self._characters.values()}
        twists: dict[str, list[str]] = {cid: self._twists() for cid in unit}
        trips: dict[str, tuple[str | None, tuple[str, ...]]] = {}
        for cid in unit:
            if (what := await self._date_on(user_id, cid, day)) is not None:
                twists[cid].append(DATE_TWIST.format(what=what))
            twists[cid].extend(await self._favors_on(user_id, cid, day))
            trip_with: tuple[str, ...] = ()
            joining = await self._schedules.joining(user_id, cid, day)
            if joining is not None:
                trip, trip_with = joining
                twists[cid].append(TRIP_WITH_TWIST.format(
                    names="、".join(names.get(c, c) for c in trip_with), place=trip
                ))
            else:
                trip = self._engine.live_away(cid, day)
            if trip is None and self._random(TRIP_ODDS) == 0:
                trip = TRIP_PLACES[self._random(len(TRIP_PLACES))]
                others = [c for c in unit if c != cid]
                if others and self._random(TOGETHER_ODDS) == 0:
                    trip_with = tuple(others)
                    twists[cid].append(TRIP_WITH_TWIST.format(
                        names="、".join(names.get(c, c) for c in trip_with), place=trip
                    ))
                else:
                    twists[cid].append(TRIP_TWIST.format(place=trip))
            trips[cid] = (trip, trip_with)
        yesterday: dict[str, str | None] = {}
        for cid in unit:
            before = await self._schedules.of(user_id, cid, day - timedelta(days=1))
            yesterday[cid] = schedule_said(before, names)
        recording = self._activity.begin("schedule", user_id, unit[0], own_llm=False, exempt=False)
        lease = self._config.lease()
        try:
            with recording.current():
                answer = await self._engine.write_schedules(
                    unit, lease.keys.chat_llm, day, twists, yesterday
                )
            parsed = parse_schedules_batched(answer, self._characters)
            for cid in unit:
                if cid not in parsed:
                    continue
                slots, wake, sleep = parsed[cid]
                bath = None
                if sleep is not None:
                    heart = self._characters[cid].heart
                    bath = draw_bath(sleep, heart.bath if heart else "tub", self._random)
                trip, trip_with = trips[cid]
                await self._schedules.forget(user_id, cid, day)
                await self._schedules.keep(
                    user_id, cid, day, slots, twists[cid], wake, sleep, bath,
                    trip if slots else None, trip_with,
                )
            await recording.finish("success" if parsed else "failed")
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("writing schedules of %s for %s failed: %s", unit, user_id,
                        type(e).__name__)
            await recording.finish("failed")
        finally:
            lease.release()
