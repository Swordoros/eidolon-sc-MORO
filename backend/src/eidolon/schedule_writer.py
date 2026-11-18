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
in the tub unless her heart says she showers (GLOSSARY.md「洗澡」). A day's units are planned
together: each has a trip on one day in `UNIT_TRIP_ODDS`, drawn once for the whole unit, and
one member is picked to send its content and keeps the day's `trip`/`trip_with`. When two or
more units trip, one pair may go together on one day in `JOINT_TRIP_ODDS`; they share a
place and one owner, so the pair sends one photo and brings one souvenir. A unit with no such
trip has a solo trip for one member on one day in `SOLO_TRIP_ODDS`. A live away from Tokyo is
left to the world setting's live, not counted as a trip by itself (GLOSSARY.md「纪念品」
「外景照片」); a day the producer invited her out on (GLOSSARY.md「邀约」), that too, a twist of
its own. A group may also draw a live of its own in `LIVE_ODDS` on a day the world setting has
none, but waits `LIVE_COOLDOWN_DAYS` after one; the world setting's live always wins that day.
A staff member alone is written last, after the units, and on `ESCORT_ODDS` may draw one
lesson, trip or live they have that day (`ESCORT_WEIGHTS`); her own day then carries the
escort, and every member's kept slot is marked with her name.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
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
    JOINT_TRIP_ODDS,
    MAX_SLOTS,
    MAX_WHAT,
    SOLO_TRIP_ODDS,
    TRIP_PLACES,
    TRIP_TWIST,
    TRIP_WITH_TWIST,
    DATE_TWIST,
    UNIT_TRIP_ODDS,
    TWISTS,
    Schedule,
    Slot,
    Schedules,
    draw_bath,
    parse_rest,
    parse_schedule,
    parse_schedules_batched,
    reconciled,
)
from eidolon.schedules import said as schedule_said
from eidolon.store import Store

log = logging.getLogger(__name__)

ESCORT_ODDS = 30
"""One day in so many a staff member is given an idol unit to accompany."""

ESCORT_WEIGHTS: dict[str, int] = {"live": 5, "trip": 3, "lesson": 2}
"""How the kinds of escort are weighted among those the day actually has."""

EscortKind = Literal["lesson", "trip", "live"]

TICK = timedelta(minutes=30)
"""How often it is looked whose schedule is to be written, and the units that could not be
are retried."""

WRITE_FROM = time(4)
"""From when in the producer's day their day's schedules are written."""

MAX_TWISTS = 2
"""How many twists a day has at most."""

LIVE_ODDS = 3
"""Three days in a hundred a group is drawn a live of its own, out of the world setting's."""
LIVE_COOLDOWN_DAYS = 3
"""Days after a drawn live before its group may be drawn another."""
def _live_slot(slot: Slot) -> bool:
    """Whether a slot is a live, by its kind or what it says (the model does not always
    use the live kind); used to keep one the group was not granted."""
    said = f"{slot.what} {slot.where}".casefold()
    return (
        slot.kind == "live"
        or "live" in said
        or "演唱会" in slot.what
        or "公演" in slot.what
        or "ライブ" in slot.what
    )


def granted_live_slots(slots: Sequence[Slot], granted: bool) -> list[Slot]:
    """The slots left when a group was not granted a live: a live of any kind, or one
    written as a live under another kind, is dropped."""
    return list(slots) if granted else [slot for slot in slots if not _live_slot(slot)]


def _overlaps(slot: Slot, start: time, end: time) -> bool:
    return slot.start < end and start < slot.end


async def _no_date(*_: object) -> str | None:
    return None


async def _no_favors(*_: object) -> list[str]:
    return []


TripPlan = tuple[
    dict[str, list[str]], dict[str, tuple[str | None, tuple[str, ...]]]
]


@dataclass(frozen=True)
class EscortOffer:
    """One thing an idol unit is doing that the staff member could accompany."""

    unit: str
    kind: EscortKind
    start: time
    end: time
    what: str
    where: str
    members: tuple[str, ...]
    """The character ids whose kept schedule has this slot, all to be marked."""

    def task(self) -> str:
        """What her own schedule says she does."""
        if self.kind == "trip":
            return f"陪同{self.unit}前往{self.where}进行外景拍摄"[:MAX_WHAT]
        return f"陪同{self.unit}进行{self.what}"[:MAX_WHAT]


def with_escort(slots: Sequence[Slot], offer: EscortOffer) -> list[Slot]:
    """The day's slots with `offer` as one work slot, any ordinary slot it overlaps
    dropped; the offer itself is what she does, so it is not left to the model's wording."""
    kept = [slot for slot in slots if not _overlaps(slot, offer.start, offer.end)]
    kept.append(Slot(offer.start, offer.end, offer.task(), offer.where, "work"))
    kept.sort(key=lambda slot: slot.start)
    return kept[:MAX_SLOTS]


def staff_slots(slots: Sequence[Slot], escort: EscortOffer | None) -> list[Slot]:
    """A staff member's day: her own slots, with every escort the model wrote on its own
    removed, and only the escort actually drawn put back in."""
    kept = [slot for slot in slots if "陪同" not in slot.what]
    return with_escort(kept, escort) if escort is not None else kept


def _plan_group(
    members: list[str], names: Mapping[str, str], random: Random
) -> tuple[str, TripPlan]:
    """One trip for `members`, all of them away together: a place, a chosen member to hold
    the trip's content, and the group twist for everyone else. Only the owner has `trip`;
    the others' schedules still say they went, but they send no content."""
    place = TRIP_PLACES[random(len(TRIP_PLACES))]
    owner = members[0] if len(members) == 1 else members[random(len(members))]
    others = tuple(cid for cid in members if cid != owner)
    twists: dict[str, list[str]] = {}
    if others:
        for cid in members:
            going = [c for c in members if c != cid]
            twists.setdefault(cid, []).append(TRIP_WITH_TWIST.format(
                names="、".join(names.get(c, c) for c in going), place=place
            ))
    else:
        twists.setdefault(owner, []).append(TRIP_TWIST.format(place=place))
    trips: dict[str, tuple[str | None, tuple[str, ...]]] = {
        cid: (None, ()) for cid in members
    }
    trips[owner] = (place, others)
    return place, (twists, trips)


def _plan_day_trips(
    units: list[list[str]], names: Mapping[str, str], random: Random
) -> tuple[list[TripPlan], tuple[int, int] | None, TripPlan | None]:
    """Draw one day's trips for all units due together. Every unit draws a unit trip; when
    at least two different units of two or more have one, two of them are picked and one
    `JOINT_TRIP_ODDS` roll decides whether they go together. Only one pair can join; any
    other unit trips stay separate. A unit without a unit trip may instead get one solo
    trip. A joint pair is returned as one plan for the combined members, with one owner, so
    it produces one photo and one souvenir."""
    group = [random(UNIT_TRIP_ODDS) == 0 for _ in units]
    group_units = [
        i for i, (is_group, unit) in enumerate(zip(group, units))
        if is_group and len(unit) >= 2
    ]
    joint: tuple[int, int] | None = None
    if len(group_units) >= 2:
        first = group_units[random(len(group_units))]
        rest = [i for i in group_units if i != first]
        second = rest[random(len(rest))]
        if random(JOINT_TRIP_ODDS) == 0:
            joint = (first, second)
    plans: list[TripPlan] = [
        ({}, {cid: (None, ()) for cid in unit}) for unit in units
    ]
    joint_plan: TripPlan | None = None
    if joint is not None:
        members = [*units[joint[0]], *units[joint[1]]]
        _, joint_plan = _plan_group(members, names, random)
    for index, unit in enumerate(units):
        if joint is not None and index in joint:
            continue
        if group[index]:
            _, plans[index] = _plan_group(unit, names, random)
        elif random(SOLO_TRIP_ODDS) == 0:
            owner = unit[0] if len(unit) == 1 else unit[random(len(unit))]
            _, solo = _plan_group([owner], names, random)
            plans[index][0].setdefault(owner, []).extend(solo[0].get(owner, []))
            plans[index][1][owner] = solo[1][owner]
    return plans, joint, joint_plan


def _plan_trips(unit: list[str], names: Mapping[str, str], random: Random) -> TripPlan:
    """One unit's trip plan, the same draw a day of one due unit gets."""
    plans, _, _ = _plan_day_trips([unit], names, random)
    return plans[0]


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
        names = {c.id: c.name for c in self._characters.values()}
        by_day: dict[tuple[str, date], list[list[str]]] = {}
        for user_id, unit, day in due:
            by_day.setdefault((user_id, day), []).append(unit)
        for (user_id, day), units in by_day.items():
            # Staff read the day the idols have already written: their escorts are drawn
            # from the units' kept schedules, so they must be written last.
            units.sort(
                key=lambda unit: len(unit) == 1 and self._engine.is_staff(unit[0])
            )
            plans, joint, joint_plan = _plan_day_trips(units, names, self._random)
            lives = [await self._live_for(user_id, unit, day) for unit in units]
            if joint is not None:
                members = [*units[joint[0]], *units[joint[1]]]
                assert joint_plan is not None
                live_by = {
                    cid: lives[index]
                    for index in joint for cid in units[index]
                }
                try:
                    await self._write_unit(
                        user_id, members, day, joint_plan, all_or_nothing=True, live=live_by,
                    )
                except Exception:
                    log.exception("writing the joint schedules of %s for %s failed", members, user_id)
            for index, unit in enumerate(units):
                if joint is not None and index in joint:
                    continue
                escort = None
                if len(unit) == 1 and self._engine.is_staff(unit[0]):
                    escort = await self._escort_for(user_id, day, unit[0])
                try:
                    await self._write_unit(
                        user_id, unit, day, plans[index], live=lives[index], escort=escort
                    )
                except Exception:
                    log.exception("writing the schedules of %s for %s failed", unit, user_id)
        await self._schedules.sweep(now.date())

    async def write_now(self, user_id: str, character_id: str) -> bool:
        """Have her unit's schedules for today written now, whatever the hour and whether it
        was: for trying it out under the development sign-in. False if she is no character."""
        if character_id not in self._characters:
            return False
        group = next(g for g in self._engine.schedule_groups() if character_id in g)
        day = await self._today(user_id, self._clock())
        live = await self._live_for(user_id, group, day)
        escort = None
        if len(group) == 1 and self._engine.is_staff(group[0]):
            escort = await self._escort_for(user_id, day, group[0])
        await self._write_unit(user_id, group, day, live=live, escort=escort)
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

    async def _live_for(self, user_id: str, unit: list[str], day: date) -> str | None:
        """The live drawn for this group on `day`, if any; world-setting lives always win,
        and a group waits `LIVE_COOLDOWN_DAYS` after one of its own."""
        if len(unit) < 2:
            return None
        key = ",".join(sorted(unit))
        drawn, name = await self._schedules.live_plan(user_id, key, day)
        if drawn:
            return name
        if any(self._engine.world_live_on(cid, day) for cid in unit):
            await self._schedules.keep_live(user_id, key, day, None)
            return None
        last = await self._schedules.last_live_day(user_id, key)
        if last is not None and (day - last).days < LIVE_COOLDOWN_DAYS:
            await self._schedules.keep_live(user_id, key, day, None)
            return None
        if self._random(100) >= LIVE_ODDS:
            await self._schedules.keep_live(user_id, key, day, None)
            return None
        unit_name = self._engine.unit_name(unit[0]) or "组合"
        name = f"{unit_name} 临时LIVE"
        await self._schedules.keep_live(user_id, key, day, name)
        return name

    async def _escort_offers(
        self, user_id: str, day: date, staff_id: str
    ) -> list[EscortOffer]:
        """The kept slots of the day's idol units that `staff_id` could accompany: lessons,
        trips and lives, each once however many of the unit's members have it."""
        date_away = await self._date_on(user_id, staff_id, day)
        favors = await self._favors_on(user_id, staff_id, day)
        found: dict[tuple[str, EscortKind, time, time, str, str], set[str]] = {}
        schedules: dict[str, tuple[str, Schedule | None]] = {}
        trip_places: dict[str, set[str]] = {}
        for cid in self._characters:
            if cid == staff_id or self._engine.is_staff(cid):
                continue
            unit = self._engine.unit_name(cid)
            if unit is None:
                continue
            schedule = await self._schedules.of(user_id, cid, day)
            schedules[cid] = (unit, schedule)
            if schedule is not None and schedule.trip is not None:
                trip_places.setdefault(unit, set()).add(schedule.trip)

        def blocked(start: time, end: time) -> bool:
            if date_away is not None and _overlaps(
                Slot(start, end, "", "", "work"), time(14), time(17)
            ):
                return True
            for favor in favors:
                for word, low, high in (
                    ("上午", time(6), time(12)),
                    ("中午", time(11), time(14)),
                    ("下午", time(13), time(18)),
                    ("晚上", time(18), time(23, 59)),
                ):
                    if word in favor and _overlaps(
                        Slot(start, end, "", "", "work"), low, high
                    ):
                        return True
            return False

        for cid, (unit, schedule) in schedules.items():
            if schedule is None:
                continue
            for slot in schedule.slots:
                if blocked(slot.start, slot.end):
                    continue
                if slot.kind == "lesson":
                    kind: EscortKind = "lesson"
                elif _live_slot(slot):
                    kind = "live"
                else:
                    kind = None
                if kind is not None:
                    key = (unit, kind, slot.start, slot.end, slot.what, slot.where)
                    found.setdefault(key, set()).add(cid)
            for place in trip_places.get(unit, ()):
                for slot in schedule.slots:
                    if place not in slot.what and place not in slot.where:
                        continue
                    if blocked(slot.start, slot.end):
                        continue
                    key = (unit, "trip", slot.start, slot.end, place, slot.where)
                    found.setdefault(key, set()).add(cid)
                    break
        return [
            EscortOffer(unit, kind, start, end, what, where, tuple(sorted(members)))
            for (unit, kind, start, end, what, where), members in found.items()
        ]

    def _pick_escort(self, offers: Sequence[EscortOffer]) -> EscortOffer | None:
        """One offer to accompany, or none: first whether the day has one at all
        (`ESCORT_ODDS`), then a kind among those the day has, by `ESCORT_WEIGHTS`."""
        if not offers or self._random(100) >= ESCORT_ODDS:
            return None
        by_kind: dict[EscortKind, list[EscortOffer]] = {}
        for offer in offers:
            by_kind.setdefault(offer.kind, []).append(offer)
        total = sum(ESCORT_WEIGHTS[kind] for kind in by_kind)
        drawn = self._random(total)
        for kind in ("live", "trip", "lesson"):
            if kind not in by_kind:
                continue
            weight = ESCORT_WEIGHTS[kind]
            if drawn < weight:
                options = by_kind[kind]  # type: ignore[arg-type]
                return options[self._random(len(options))]
            drawn -= weight
        return None

    async def _escort_for(self, user_id: str, day: date, staff_id: str) -> EscortOffer | None:
        return self._pick_escort(await self._escort_offers(user_id, day, staff_id))

    def _escort_twist(self, offer: EscortOffer) -> str:
        return (
            f"今天 {offer.start.strftime('%H:%M')}–{offer.end.strftime('%H:%M')}，"
            f"你要陪同{offer.unit}在{offer.where}进行{offer.what}。"
            f"把这段时间排成 work，what 写「{offer.task()}」，where 写「{offer.where}」。"
        )

    async def _write_unit(
        self, user_id: str, unit: list[str], day: date,
        plan: TripPlan | None = None, all_or_nothing: bool = False,
        live: str | Mapping[str, str | None] | None = None,
        escort: EscortOffer | None = None,
    ) -> None:
        """Write the day's schedules of the unit's members together, in one call, so that
        they do not conflict; a member whose part cannot be read is left to be retried.
        `plan` is a day's trip draw when several units were planned together; a joint pair
        passes all its members as one unit and writes none unless all their parts are read.
        A staff member alone may be given one `escort` to write into her day."""
        staff = len(unit) == 1 and self._engine.is_staff(unit[0])
        names = {c.id: c.name for c in self._characters.values()}
        live_by = (
            {cid: live for cid in unit} if isinstance(live, str) or live is None
            else dict(live)
        )
        twists: dict[str, list[str]] = {cid: self._twists() for cid in unit}
        if staff and escort is not None:
            twists[unit[0]].append(self._escort_twist(escort))
        if staff:
            plan = ({}, {cid: (None, ()) for cid in unit})
        elif plan is None:
            plan = _plan_trips(unit, names, self._random)
        trip_twists, trips = plan
        for cid, additions in trip_twists.items():
            twists[cid].extend(additions)
        for cid in unit:
            if (what := await self._date_on(user_id, cid, day)) is not None:
                twists[cid].append(DATE_TWIST.format(what=what))
            twists[cid].extend(await self._favors_on(user_id, cid, day))
        yesterday: dict[str, str | None] = {}
        for cid in unit:
            before = await self._schedules.of(user_id, cid, day - timedelta(days=1))
            yesterday[cid] = schedule_said(before, names)
        recording = self._activity.begin("schedule", user_id, unit[0], own_llm=False, exempt=False)
        lease = self._config.lease()
        try:
            with recording.current():
                if staff:
                    answer = await self._engine.write_staff_schedule(
                        unit[0], lease.keys.chat_llm, day, twists[unit[0]],
                        yesterday[unit[0]],
                    )
                else:
                    answer = await self._engine.write_schedules(
                        unit, lease.keys.chat_llm, day, twists, yesterday, live_by
                    )
            if staff:
                slots = parse_schedule(answer, self._characters)
                wake, sleep = parse_rest(answer)
                parsed = {unit[0]: (slots, wake, sleep)}
            else:
                parsed = parse_schedules_batched(answer, self._characters)
            if all_or_nothing and any(cid not in parsed for cid in unit):
                await recording.finish("failed")
                return
            for cid in unit:
                if cid not in parsed:
                    continue
                slots, wake, sleep = parsed[cid]
                if staff and escort is not None and cid == unit[0]:
                    slots = staff_slots(slots, escort)
                elif staff:
                    slots = staff_slots(slots, None)
                if (
                    not live_by.get(cid)
                    and not self._engine.world_live_on(cid, day)
                    and not self._engine.is_staff(cid)
                ):
                    slots = granted_live_slots(slots, False)
                slots, wake, sleep, _ = reconciled(slots, wake, sleep, None)
                bath = None
                if sleep is not None:
                    heart = self._characters[cid].heart
                    bath = draw_bath(sleep, heart.bath if heart else "tub", self._random)
                slots, wake, sleep, bath = reconciled(slots, wake, sleep, bath)
                trip, trip_with = trips[cid]
                await self._schedules.forget(user_id, cid, day)
                await self._schedules.keep(
                    user_id, cid, day, slots, twists[cid], wake, sleep, bath,
                    trip if slots else None, trip_with,
                )
                if staff and escort is not None and cid == unit[0]:
                    character = self._characters[cid]
                    note = f"（{character.name_zh or character.name}陪同）"
                    for member in escort.members:
                        if not await self._schedules.annotate(
                            user_id, member, day, escort.start, escort.end, note
                        ):
                            log.warning(
                                "could not mark %s's schedule as escorted by %s on %s",
                                member, cid, day,
                            )
            await recording.finish("success" if parsed else "failed")
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("writing schedules of %s for %s failed: %s", unit, user_id,
                        type(e).__name__)
            await recording.finish("failed")
        finally:
            lease.release()
