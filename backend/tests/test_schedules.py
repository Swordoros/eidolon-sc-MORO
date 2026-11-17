"""Her schedule (GLOSSARY.md「行程」): written anew each day for the idols the producer has been
in touch with lately, from the early morning of the producer's day, with a twist or two drawn
for it; what the model writes checked, a day it fails kept as none; read in her turns as the
day in a sentence and where she is now, the prompt as it was without one; and hers to read over
HTTP. The producer's time zone is UTC (`Etc/GMT`), the clock set to times of today's date, as
the store writes when turns were recorded at the time it is."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import httpx
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
from pipecat.tests.utils import SleepFrame

from eidolon.config import Settings
from eidolon.engine import (
    ANSWERING,
    BATCH_SCHEDULE_RULES,
    NO_LIVE,
    SCHEDULE_RULES,
    STAFF_SCHEDULE_RULES,
    WANTS_TO_PLAN,
    _student_line,
)
from eidolon.llm import LLMError
from eidolon.schedule_writer import (
    ESCORT_ODDS,
    EscortOffer,
    ScheduleWriter,
    _plan_day_trips,
    _plan_trips,
    granted_live_slots,
    with_escort,
)
from eidolon.schedules import (
    SOLO_TRIP_ODDS,
    TRIP_PLACES,
    TRIP_TWIST,
    TRIP_WITH_TWIST,
    TWISTS,
    UNIT_TRIP_ODDS,
    Bath,
    Schedule,
    Schedules,
    Slot,
    _SCHEMA,
    lines,
    parse_rest,
    parse_schedule,
    reconciled,
    rest,
)
from tests.conftest import CharacterConfig, ClientFactory, database_in
from tests.fakes import FakeClock, FakeLLM, FakeRandom, context_of
from tests.test_call_turns import CallUnderTest
from tests.test_call_turns import mano_call  # noqa: F401  # the fixture
from tests.test_delete_account import user_id
from tests.test_reach_outs import backend_of, last_contact
from tests.test_text_turn import send_turn, start_conversation
from tests.test_world_setting import write_world

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
CHARACTERS = {"mano": object(), "kogane": object()}
PLAN = (
    '{"slots": ['
    '{"start": "09:00", "end": "12:00", "what": "舞蹈课", "where": "事务所", "kind": "lesson", "with": []},'
    '{"start": "14:00", "end": "17:00", "what": "逛文具店", "where": "车站前", "kind": "private",'
    ' "with": ["kogane", "nobody"]}'
    "]}"
)
MANO_PLAN = '{"mano": ' + PLAN + "}"
KOGANE_NONE = '{"kogane": {"slots": []}}'


def today_at(hour: int, minute: int = 0) -> datetime:
    return datetime.now(UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_what_the_model_writes_is_checked_slot_by_slot():
    good = {"start": "09:00", "end": "10:00", "what": "练习", "where": "", "kind": "lesson"}
    answer = {"slots": [
        {**good, "start": "13:00", "end": "15:00", "what": "拍摄", "kind": "work"},
        good,
        {**good, "start": "09:30", "end": "11:00"},  # overlaps the one before it
        {**good, "start": "05:00", "end": "06:30"},  # before the day's hours
        {**good, "start": "16:00", "end": "15:00"},  # ends before it begins
        {**good, "start": "17:00", "end": "18:00", "kind": "dating"},  # no such kind
        {**good, "start": "22:00", "end": "24:00", "kind": "rest", "with": ["kogane", "x"]},
        "not a slot",
    ]}

    slots = parse_schedule(json.dumps(answer), CHARACTERS)

    assert [(s.start, s.what) for s in slots] == [
        (time(9), "练习"), (time(13), "拍摄"), (time(22), "练习"),
    ]
    assert slots[-1].end == time(23, 59) and slots[-1].with_ == ("kogane",)
    assert parse_schedule("没有 JSON", CHARACTERS) == []
    assert parse_schedule('{"slots": "x"}', CHARACTERS) == []


def test_where_she_is_now_says_how_far_through_the_slot_she_is():
    day = date(2026, 10, 22)
    schedule = Schedule(day, (
        Slot(time(19), time(20, 30), "在宿舍准备晚饭", "宿舍的公共厨房", "private",
             ("sakuya", "kiriko")),
    ), ())
    names = {"sakuya": "白瀬咲耶", "kiriko": "幽谷霧子"}

    def now(hour: int, minute: int) -> str:
        return lines(schedule, datetime.combine(day, time(hour, minute)), names)[1]

    assert now(19, 10).endswith("（刚刚开始），在宿舍的公共厨房。")
    assert now(19, 30).endswith("（进行中），在宿舍的公共厨房。")
    assert now(20, 25) == (
        "现在（20:25）你大概在：在宿舍准备晚饭（和白瀬咲耶、幽谷霧子）"
        "（快结束了），在宿舍的公共厨房。"
    )


def test_she_is_told_the_day_in_a_sentence_and_where_she_is_now():
    schedule = Schedule(date(2026, 10, 22), (
        Slot(time(9), time(12), "舞蹈课", "事务所", "lesson"),
        Slot(time(14), time(17), "逛文具店", "车站前", "private", ("kogane",)),
    ), ())
    names = {"kogane": "月岡恋鐘"}
    on = date(2026, 10, 22)

    during = lines(schedule, datetime.combine(on, time(15, 5)), names)
    between = lines(schedule, datetime.combine(on, time(12, 30)), names)
    early = lines(schedule, datetime.combine(on, time(7)), names)

    assert during[0] == (
        "你今天大概的安排：09:00–12:00 舞蹈课，在事务所；14:00–17:00 逛文具店（和月岡恋鐘），在车站前。"
    )
    assert during[1] == "现在（15:05）你大概在：逛文具店（和月岡恋鐘）（进行中），在车站前。"
    assert between[1] == "现在（12:30）你没什么安排。"
    assert early[1] == "现在（07:00）今天的安排还没开始。"
    assert "不要像念行程表" in during[2]
    assert "已经结束、现在不在做的安排" in during[3]
    assert "现在正在做的只有" in during[3]
    assert lines(None, datetime.combine(on, time(9)), names) == []


async def talked(
    make_client: ClientFactory, settings: Settings, *replies,
    numbers=(1, 1, 1, 1, 1, 0, 1, 0),
) -> tuple[httpx.AsyncClient, FakeLLM, FakeClock]:
    llm = FakeLLM(REPLY, *replies, REPLY)
    clock = FakeClock(today_at(3))
    client = make_client(
        llm, clock=clock, random=FakeRandom(*numbers), settings=replace(settings, schedules=True)
    )
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")
    client.conversation_id = conversation  # type: ignore[attr-defined]
    return client, llm, clock


async def test_before_three_the_panel_shows_yesterdays_schedule(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()
    yesterday = today_at(6).date() - timedelta(days=1)
    async with database_in(settings.database_path) as db:
        await Schedules(db, lambda: datetime.now(UTC)).keep(
            user_id(settings.database_path, "producer"), "mano", yesterday,
            [Slot(time(9), time(10), "昨日日程", "", "lesson")], [],
        )

    clock.now = today_at(1)
    found = await schedule(client)

    assert found is not None
    assert found["day"] == yesterday.isoformat()
    assert any(slot["what"] == "昨日日程" for slot in found["slots"])


async def schedule(client: httpx.AsyncClient, character: str = "mano") -> dict | None:
    res = await client.get(f"/api/characters/{character}/schedule")
    assert res.status_code == 200, res.text
    return res.json()


def test_schedule_prompts_write_what_and_where_in_simplified_chinese():
    for prompt in (SCHEDULE_RULES, BATCH_SCHEDULE_RULES, WANTS_TO_PLAN):
        assert "简体中文" in prompt
    assert "東京駅" in BATCH_SCHEDULE_RULES and "东京站" in BATCH_SCHEDULE_RULES


def test_student_line_says_what_she_is_without_her_age():
    def character(character_id: str, age: int, sheet: str = "") -> SimpleNamespace:
        return SimpleNamespace(id=character_id, age=age, sheet=sheet)

    assert _student_line(character("kaho", 12)) == "你是小学生。"
    assert _student_line(character("asahi", 14)) == "你是中学生。"
    assert _student_line(character("mano", 16)) == "你是高中生。"
    assert _student_line(character("sakuya", 18)) == "你是高中生。"
    assert _student_line(character("mamimi", 18)) == "你是高中生。"
    assert _student_line(character("fuyuko", 19)) == "你是专科生。"
    assert _student_line(character("yuika", 19)) == "你是大学生。"
    assert _student_line(character("natsuha", 20)) == "你是大学生。"
    assert _student_line(character("hana", 18)) == "你是成年人。"
    assert _student_line(character("kogane", 19)) == "你是成年人。"


def test_schedule_prompts_have_students_go_to_school_on_weekdays():
    for prompt in (SCHEDULE_RULES, BATCH_SCHEDULE_RULES):
        assert "学生" in prompt
        assert "学校" in prompt
        assert "周一到周五" in prompt


def test_staff_schedule_prompt_is_not_an_idol_prompt():
    assert "事务员" in STAFF_SCHEDULE_RULES
    assert "不是偶像" in STAFF_SCHEDULE_RULES
    assert "陪同" in STAFF_SCHEDULE_RULES
    assert "同组合" not in STAFF_SCHEDULE_RULES


def test_a_staff_escort_replaces_the_ordinary_slot_it_overlaps():
    offer = EscortOffer(
        "SHHis", "lesson", time(14), time(16), "声乐课", "事务所练习室", ("nichika",)
    )
    slots = [
        Slot(time(9), time(12), "整理经费", "办公室", "work"),
        Slot(time(14), time(15), "准备资料", "办公室", "work"),
        Slot(time(17), time(18), "打工", "便利店", "work"),
    ]

    chosen = with_escort(slots, offer)

    assert [(s.start, s.what) for s in chosen] == [
        (time(9), "整理经费"), (time(14), "陪同SHHis进行声乐课"), (time(17), "打工"),
    ]
    assert next(s for s in chosen if s.start == time(14)).where == "事务所练习室"
    assert next(s for s in chosen if s.start == time(14)).kind == "work"


def test_escort_is_drawn_on_thirty_percent_then_live_trip_lesson_by_five_three_two():
    writer = object.__new__(ScheduleWriter)
    offers = [
        EscortOffer("SHHis", "live", time(18), time(20), "LIVE「A」", "剧场", ("nichika",)),
        EscortOffer("L'Antica", "trip", time(9), time(17), "大阪", "大阪", ("kogane",)),
        EscortOffer("illumination STARS", "lesson", time(13), time(15), "舞蹈课", "练习室", ("mano",)),
    ]

    writer._random = FakeRandom(29, 0, 0)  # type: ignore[attr-defined]
    assert writer._pick_escort(offers) is not None
    writer._random = FakeRandom(ESCORT_ODDS)  # type: ignore[attr-defined]
    assert writer._pick_escort(offers) is None

    writer._random = FakeRandom(0, 0, 0)  # type: ignore[attr-defined]
    assert writer._pick_escort(offers).kind == "live"  # type: ignore[union-attr]

    # Without live, trip and lesson weigh 3:2.
    writer._random = FakeRandom(0, 0, 0)  # type: ignore[attr-defined]
    assert writer._pick_escort(offers[1:]).kind == "trip"  # type: ignore[union-attr]
    writer._random = FakeRandom(0, 3, 0)  # type: ignore[attr-defined]
    assert writer._pick_escort(offers[1:]).kind == "lesson"  # type: ignore[union-attr]


async def test_the_same_unit_lesson_is_one_escort_offer_with_all_its_members():
    day = date(2026, 11, 17)

    class FakeSchedules:
        async def of(self, _user, cid, _day):
            if cid not in ("nichika", "mikoto"):
                return None
            return Schedule(day, (
                Slot(time(14), time(16), "声乐课", "练习室", "lesson"),
            ), ())

    class FakeEngine:
        def is_staff(self, cid):
            return cid == "hazuki"

        def unit_name(self, cid):
            return "SHHis" if cid in ("nichika", "mikoto") else None

    writer = object.__new__(ScheduleWriter)
    writer._characters = {  # type: ignore[attr-defined]
        cid: SimpleNamespace(id=cid, name=cid)
        for cid in ("hazuki", "nichika", "mikoto")
    }
    writer._schedules = FakeSchedules()  # type: ignore[attr-defined]
    writer._engine = FakeEngine()  # type: ignore[attr-defined]

    async def no_date(*_):
        return None

    async def no_favors(*_):
        return []

    writer._date_on = no_date  # type: ignore[attr-defined]
    writer._favors_on = no_favors  # type: ignore[attr-defined]

    offers = await writer._escort_offers("producer", day, "hazuki")

    assert len(offers) == 1
    assert offers[0].unit == "SHHis"
    assert offers[0].task() == "陪同SHHis进行声乐课"
    assert offers[0].members == ("mikoto", "nichika")


async def test_a_source_slot_is_marked_as_escorted_and_the_note_is_clipped(settings: Settings):
    async with database_in(settings.database_path) as db:
        await db.write(
            lambda conn: conn.executescript(
                "CREATE TABLE users (id TEXT PRIMARY KEY); INSERT INTO users VALUES ('producer');"
            )
        )
        await db.write(lambda conn: conn.executescript(_SCHEMA))
        schedules = Schedules(db, lambda: datetime.now(UTC))
        day = date(2026, 11, 17)
        await schedules.keep(
            "producer", "nichika", day,
            [Slot(time(14), time(16), "声乐课", "练习室", "lesson")], [],
        )

        assert await schedules.annotate(
            "producer", "nichika", day, time(14), time(16), "（七草叶月陪同）"
        )
        kept = await schedules.of("producer", "nichika", day)
        assert kept is not None
        assert kept.slots[0].what == "声乐课（七草叶月陪同）"
        assert not await schedules.annotate(
            "producer", "nichika", day, time(10), time(11), "（七草叶月陪同）"
        )


def test_a_unit_trip_picks_one_member_to_hold_the_trip_and_the_others_go_with_her():
    names = {"mano": "真乃", "kogane": "恋鐘"}
    twists, trips = _plan_trips(["mano", "kogane"], names, FakeRandom(0, 0, 1))

    place = TRIP_PLACES[0]
    assert trips == {"mano": (None, ()), "kogane": (place, ("mano",))}
    assert TRIP_WITH_TWIST.format(names="恋鐘", place=place) in twists["mano"]
    assert TRIP_WITH_TWIST.format(names="真乃", place=place) in twists["kogane"]


def test_without_a_unit_trip_only_one_member_can_have_a_solo_trip():
    names = {"mano": "真乃", "kogane": "恋鐘"}
    twists, trips = _plan_trips(["mano", "kogane"], names, FakeRandom(1, 0, 0, 0))

    place = TRIP_PLACES[0]
    assert trips == {"mano": (place, ()), "kogane": (None, ())}
    assert TRIP_TWIST.format(place=place) in twists["mano"]
    assert "kogane" not in twists


def test_a_unit_of_one_draws_once_for_her_trip_without_an_owner_index():
    twists, trips = _plan_trips(["mano"], {"mano": "真乃"}, FakeRandom(0, 0))

    assert trips == {"mano": (TRIP_PLACES[0], ())}
    assert TRIP_TWIST.format(place=TRIP_PLACES[0]) in twists["mano"]
    assert UNIT_TRIP_ODDS == 10 and SOLO_TRIP_ODDS == 20


def test_two_tripping_units_can_go_together_with_one_owner():
    units = [["mano", "hiori"], ["kogane", "mamimi"]]
    names = {"mano": "真乃", "hiori": "灯織", "kogane": "恋鐘", "mamimi": "摩美美"}

    plans, joint, joint_plan = _plan_day_trips(
        units, names, FakeRandom(0, 0, 0, 0, 0, 0, 1),
    )

    place = TRIP_PLACES[0]
    assert joint == (0, 1)
    assert joint_plan is not None
    owner = "hiori"
    assert joint_plan[1][owner] == (place, ("mano", "kogane", "mamimi"))
    assert [cid for cid, (trip, _) in joint_plan[1].items() if trip is not None] == [owner]
    assert plans == [({}, {cid: (None, ()) for cid in unit}) for unit in units]
    assert TRIP_WITH_TWIST.format(names="真乃、恋鐘、摩美美", place=place) in joint_plan[0][owner]


def test_only_one_pair_joins_when_three_units_trip():
    units = [["a1", "a2"], ["b1", "b2"], ["c1", "c2"]]
    names = {cid: cid.upper() for unit in units for cid in unit}

    plans, joint, joint_plan = _plan_day_trips(
        units, names, FakeRandom(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    )

    assert joint == (0, 1) and joint_plan is not None
    assert [cid for cid, (trip, _) in joint_plan[1].items() if trip is not None] == ["a1"]
    assert plans[0] == ({}, {"a1": (None, ()), "a2": (None, ())})
    assert plans[1] == ({}, {"b1": (None, ()), "b2": (None, ())})
    assert plans[2][1]["c1"][0] == TRIP_PLACES[0]


def test_a_failed_joint_roll_leaves_the_units_apart():
    units = [["mano", "hiori"], ["kogane", "mamimi"]]
    names = {"mano": "真乃", "hiori": "灯織", "kogane": "恋鐘", "mamimi": "摩美美"}

    plans, joint, joint_plan = _plan_day_trips(
        units, names, FakeRandom(0, 0, 0, 0, 1, 0, 1, 0, 1),
    )

    assert joint is None and joint_plan is None
    assert [cid for cid, (trip, _) in plans[0][1].items() if trip is not None] == ["hiori"]
    assert [cid for cid, (trip, _) in plans[1][1].items() if trip is not None] == ["mamimi"]


async def test_hers_for_today_is_written_once_from_the_early_morning(
    make_client: ClientFactory, settings: Settings
):
    # One twist (1), the first of them (0); no unit or solo trip (1, 1).
    client, llm, clock = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    writer = backend_of(client).schedule_writer

    await writer.run_due()
    too_early = len(llm.prompts)
    clock.now = today_at(6)
    await writer.run_due()
    await writer.run_due()

    assert too_early == 1  # the turn's only
    assert len(llm.prompts) == 3  # the turn's, then each unit's once
    prompt = llm.prompts[-1]
    told = "\n".join(m.content for m in prompt if m.role == "system")
    assert BATCH_SCHEDULE_RULES in told and TWISTS[0] in told
    assert f"星期{'一二三四五六日'[today_at(6).weekday()]}" in told
    assert "你是成年人。" in told
    assert prompt[-1].content == WANTS_TO_PLAN
    written = await schedule(client)
    assert written is not None
    assert [(s["what"], s["with"]) for s in written["slots"]] == [
        ("舞蹈课", []), ("逛文具店", ["kogane"]),
    ]
    assert written["twists"] == [TWISTS[0]]


async def test_a_day_the_model_fails_is_none_and_not_tried_again(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock = await talked(
        make_client, settings, LLMError("挂了"), LLMError("挂了"),
        numbers=(1, 1, 1, 1, 0, 0) * 2,
    )
    writer = backend_of(client).schedule_writer
    clock.now = today_at(6)

    await writer.run_due()
    await writer.run_due()

    assert len(llm.prompts) == 5  # the turn's, then two failed, then two retried
    assert await schedule(client) is None


async def test_off_none_is_written(make_client: ClientFactory, settings: Settings):
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=FakeClock(today_at(6)), settings=settings)
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")

    await backend_of(client).schedule_writer.run_due()

    assert len(llm.prompts) == 1


async def test_a_staff_member_is_written_with_her_own_prompt(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    characters.add(
        "hazuki", "七草はづき", "事务员",
        other_names=("七草叶月", "ななくさ はづき", "Nanakusa Hazuki"),
    )
    write_world(characters)
    staff_plan = (
        '{"wake": "07:30", "sleep": "23:30", "slots": ['
        '{"start": "09:00", "end": "10:00", "what": "整理经费",'
        ' "where": "事务所办公室", "kind": "work", "with": []}]}'
    )
    client, llm, clock = await talked(
        make_client, settings, [MANO_PLAN], [KOGANE_NONE], [staff_plan],
        numbers=(0,) * 50,
    )
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()

    written = await schedule(client, "hazuki")

    assert written is not None
    assert any(
        slot["what"].startswith("陪同") or slot["what"] == "整理经费"
        for slot in written["slots"]
    )
    joined = "\n".join(slot["what"] for slot in written["slots"])
    if "陪同" in joined:
        source = await schedule(client, "mano")
        assert source is not None
        assert any("（七草叶月陪同）" in slot["what"] for slot in source["slots"])
    prompts = ["\n".join(m.content for m in p if m.role == "system") for p in llm.prompts]
    assert any(STAFF_SCHEDULE_RULES in told for told in prompts)


async def test_her_turn_tells_where_she_is_now_and_without_one_is_as_it_was(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    before = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()
    clock.now = today_at(15)
    await send_turn(client, client.conversation_id, "在干嘛")  # type: ignore[attr-defined]

    # The turn's schedule is one of several system messages; the most recent one is
    # the history note, so search them all rather than assuming it is last.
    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "现在（15:00）你大概在：逛文具店" in told
    assert "大概的安排" not in before


async def test_a_group_live_is_drawn_on_its_own_odds_and_waits_three_days(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    characters.add("hiori", "風野灯織", "sheet", idol_id=2)
    write_world(characters)
    client, _, _ = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    writer = backend_of(client).schedule_writer
    me = user_id(settings.database_path, "producer")
    day = date(2026, 11, 9)

    writer._random = FakeRandom(0)
    first = await writer._live_for(me, ["mano", "hiori"], day)
    assert first == "illumination STARS 临时LIVE"

    second = await writer._live_for(me, ["mano", "hiori"], day + timedelta(days=2))
    assert second is None

    writer._random = FakeRandom(0)
    third = await writer._live_for(me, ["mano", "hiori"], day + timedelta(days=3))
    assert third == "illumination STARS 临时LIVE"

    # A world-setting live takes the day, and no group draw is made for it.
    writer._random = FakeRandom(0)
    assert await writer._live_for(me, ["mano", "hiori"], date(2026, 11, 7)) is None
    assert await writer._live_for(me, ["mano", "hiori"], date(2026, 11, 7)) is None


async def test_a_drawn_group_live_is_told_in_the_prompt(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    write_world(characters)
    client, llm, _ = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    backend = backend_of(client)

    await backend.engine.write_schedules(
        ["mano"], llm, date(2026, 11, 9), {"mano": []}, {"mano": None},
        lives={"mano": "illumination STARS 临时LIVE"},
    )
    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "今天你们组合有一场 LIVE：「illumination STARS 临时LIVE」" in told
    assert NO_LIVE not in told


async def test_a_group_that_did_not_draw_a_live_is_told_not_to_make_one(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    write_world(characters)
    client, llm, _ = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    backend = backend_of(client)

    await backend.engine.write_schedules(
        ["mano"], llm, date(2026, 11, 9), {"mano": []}, {"mano": None},
        lives={"mano": None},
    )
    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert NO_LIVE in told


async def test_a_world_live_beats_a_drawn_group_live(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    write_world(characters)
    client, llm, _ = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    backend = backend_of(client)

    await backend.engine.write_schedules(
        ["mano"], llm, date(2026, 11, 7), {"mano": []}, {"mano": None},
        lives={"mano": "illumination STARS 临时LIVE"},
    )
    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "今天你有 live：「THE IDOLM@STER SHINY COLORS 8thLIVE」" in told
    assert "临时LIVE" not in told


def test_an_ungranted_live_is_dropped_even_under_another_kind():
    live = Slot(time(19), time(21), "ALSTROEMERIA LIVE演出", "剧场", "work")
    concert = Slot(time(21), time(22), "演唱会安可", "剧场", "work")
    lesson = Slot(time(9), time(10), "练习", "练习室", "lesson")

    assert granted_live_slots([live, concert, lesson], False) == [lesson]
    assert granted_live_slots([live, concert, lesson], True) == [live, concert, lesson]


async def test_a_live_of_hers_that_day_is_to_be_in_it(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    write_world(characters)
    llm = FakeLLM(REPLY, [PLAN])
    client = make_client(llm, settings=replace(settings, schedules=True))
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "嗯")
    backend = backend_of(client)
    conversation = await backend.store.get_conversation(conversation_id)
    assert conversation is not None

    await backend.engine.write_schedule(conversation, llm, date(2026, 11, 7), [], "排练。")

    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "今天你有 live：「THE IDOLM@STER SHINY COLORS 8thLIVE」，在Kアリーナ横浜。必须排进去。" in told
    assert "你昨天的安排：排练。" in told


async def test_the_clerk_is_told_the_days_lives_so_she_does_not_invent_one(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    """Staff have no `lives_of`, so their schedule is told the day's real lives (or that
    there are none), to keep her from writing a live no one is standing on."""
    characters.add("hazuki", "七草はづき", "sheet", speakers=["はづき"])
    write_world(characters)
    llm = FakeLLM(REPLY, REPLY)
    client = make_client(llm, settings=replace(settings, schedules=True))
    backend = backend_of(client)

    await backend.engine.write_schedules(
        ["hazuki"], llm, date(2026, 11, 7), {"hazuki": []}, {"hazuki": None}
    )
    on_a_live_day = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "今天事务所各组合确实要举行的 LIVE" in on_a_live_day
    assert "「THE IDOLM@STER SHINY COLORS 8thLIVE」在Kアリーナ横浜，出演：櫻木真乃" in on_a_live_day

    await backend.engine.write_schedules(
        ["hazuki"], llm, date(2026, 11, 9), {"hazuki": []}, {"hazuki": None}
    )
    on_a_quiet_day = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "今天事务所没有 LIVE" in on_a_quiet_day
    assert "8thLIVE" not in on_a_quiet_day


async def test_hers_is_hers_to_read(make_client: ClientFactory, settings: Settings):
    client, _, clock = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()
    other = make_client(FakeLLM(), "other", settings=replace(settings, schedules=True))

    assert await schedule(other) is None
    assert (await client.get("/api/characters/nobody/schedule")).status_code == 404
    yesterday = (today_at(6) - timedelta(days=1)).date().isoformat()
    res = await client.get("/api/characters/mano/schedule", params={"day": yesterday})
    assert res.json() is None


async def test_what_she_is_up_to_now_is_told_for_the_line_under_her_name(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock = await talked(make_client, settings, [MANO_PLAN], [KOGANE_NONE])
    none_yet = (await client.get("/api/characters/mano/schedule/now")).json()
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()

    clock.now = today_at(10)
    lesson = (await client.get("/api/characters/mano/schedule/now")).json()
    clock.now = today_at(13)
    free = (await client.get("/api/characters/mano/schedule/now")).json()

    assert none_yet is None
    assert lesson == {"what": "舞蹈课", "where": "事务所", "kind": "lesson", "until": "12:00"}
    assert free == {"free": True}


async def test_before_sleep_with_no_slot_she_is_free(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(datetime(2026, 11, 9, 20, 0, tzinfo=UTC))
    client = make_client(FakeLLM(), clock=clock, settings=replace(settings, schedules=True))
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    await start_conversation(client)
    await keep_night(settings, time(7), time(23), date(2026, 11, 9))

    status = (await client.get("/api/characters/mano/schedule/now")).json()

    assert status == {"free": True}


async def test_without_todays_schedule_shes_free_when_yesterday_does_not_say_she_sleeps(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(datetime(2026, 11, 9, 20, 0, tzinfo=UTC))
    client = make_client(FakeLLM(), clock=clock, settings=replace(settings, schedules=True))
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    await start_conversation(client)
    await keep_night(settings, time(7), time(23), date(2026, 11, 8))

    status = (await client.get("/api/characters/mano/schedule/now")).json()

    assert status == {"free": True}


def rested(wake: str | None, sleep: str | None, day: date) -> Schedule:
    return Schedule(
        day, (Slot(time(10), time(12), "练习", "", "lesson"),), (),
        time.fromisoformat(wake) if wake else None, time.fromisoformat(sleep) if sleep else None,
    )


def test_she_is_asleep_from_bedtime_to_waking_and_her_bedtime_comes_first():
    day = date(2026, 10, 22)
    early = rested("07:00", "23:00", day)
    late = rested("09:00", "01:30", day)
    before = rested("07:30", "23:00", day - timedelta(days=1))
    night_owl = rested("09:00", "02:00", day - timedelta(days=1))

    def at(hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute))

    assert rest(early, before, at(6, 59)) == "asleep"
    assert rest(early, before, at(7)) is None
    assert rest(early, before, at(22, 40)) == "bedtime"
    assert rest(early, before, at(23, 5)) == "asleep"
    assert rest(late, before, at(23, 50)) is None
    assert rest(late, before, at(1, 10)) == "asleep"  # yesterday's bedtime, before today's waking
    assert rest(late, night_owl, at(1, 40)) == "bedtime"  # still up from last night
    assert rest(early, night_owl, at(2, 30)) == "asleep"
    assert rest(None, before, at(5)) == "asleep"  # today's not written: up as yesterday
    assert rest(None, None, at(3)) is None


def test_after_midnight_sleep_stays_asleep_until_waking_even_without_todays_schedule():
    day = date(2026, 10, 22)
    yesterday_late = rested("07:00", "00:30", day - timedelta(days=1))
    yesterday_early = rested("07:00", "23:00", day - timedelta(days=1))
    today_without_wake = Schedule(day, (), (), None, time(23))

    def at(hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute))

    assert rest(None, yesterday_late, at(0, 10)) == "bedtime"
    assert rest(None, yesterday_late, at(0, 35)) == "asleep"
    assert rest(None, yesterday_late, at(4)) == "asleep"
    assert rest(None, yesterday_late, at(7)) is None
    assert rest(None, yesterday_early, at(0, 10)) == "asleep"
    assert rest(None, yesterday_early, at(4)) == "asleep"
    # Today's waking is not written: yesterday's still tells when she gets up.
    assert rest(today_without_wake, yesterday_early, at(0, 10)) == "asleep"
    assert rest(today_without_wake, yesterday_early, at(7, 30)) is None


def test_when_she_gets_up_and_goes_to_bed_is_read_within_their_hours():
    assert parse_rest('{"wake": "07:30", "sleep": "00:30", "slots": []}') == (time(7, 30), time(0, 30))
    assert parse_rest('{"wake": "03:00", "sleep": "18:00"}') == (None, None)
    assert parse_rest('{"wake": "08:00", "sleep": "23:00"}') == (time(8), time(23))
    assert parse_rest("没有 JSON") == (None, None)


async def keep_night(settings: Settings, wake: time, sleep: time, day: date) -> None:
    async with database_in(settings.database_path) as db:
        await Schedules(db, lambda: datetime.now(UTC)).keep(
            user_id(settings.database_path, "producer"), "mano", day,
            [Slot(time(10), time(12), "练习", "", "lesson")], [], wake, sleep,
        )


async def test_a_message_while_she_sleeps_wakes_her_and_after_that_she_is_up(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY, REPLY, REPLY)
    clock = FakeClock(today_at(1))
    client = make_client(llm, clock=clock)
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    conversation = await start_conversation(client)
    await keep_night(settings, time(7), time(23), today_at(1).date())

    await send_turn(client, conversation, "还醒着吗")
    woken = context_of(llm.prompts[-1])
    status = (await client.get("/api/characters/mano/schedule/now")).json()
    clock.advance(minutes=5)
    await send_turn(client, conversation, "抱歉吵醒你了")
    up = context_of(llm.prompts[-1])

    assert "是被制作人的消息吵醒的" in woken
    assert "你们已经聊了一会儿" in up and "被制作人的消息吵醒" not in up
    assert status == {"asleep": True, "woken": True}


async def test_before_todays_schedule_is_written_a_message_before_sleep_is_not_waking_her(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(datetime(2026, 11, 9, 23, 46, tzinfo=UTC))
    client = make_client(
        FakeLLM(REPLY), clock=clock, settings=replace(settings, schedules=True)
    )
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    conversation = await start_conversation(client)
    await keep_night(settings, time(7), time(0), date(2026, 11, 9))
    await send_turn(client, conversation, "晚安")
    last_contact(
        settings.database_path, conversation, datetime(2026, 11, 9, 23, 46, tzinfo=UTC)
    )

    clock.now = datetime(2026, 11, 10, 0, 7, tzinfo=UTC)

    status = (await client.get("/api/characters/mano/schedule/now")).json()

    # Asleep by yesterday's bedtime; the last message was before she went to sleep, so it did
    # not wake her.
    assert status == {"asleep": True, "woken": False}


async def test_before_todays_schedule_is_written_shes_bedtime_not_without_a_status(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(datetime(2026, 11, 10, 0, 7, tzinfo=UTC))
    client = make_client(FakeLLM(), clock=clock, settings=replace(settings, schedules=True))
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    await start_conversation(client)
    await keep_night(settings, time(7), time(0, 30), date(2026, 11, 9))

    status = (await client.get("/api/characters/mano/schedule/now")).json()

    assert status == {"free": True, "bedtime": True}


async def test_woken_by_a_call_she_answers_it_first(
    mano_call: CallUnderTest, settings: Settings
):
    await keep_night(settings, time(7), time(20), date(2026, 9, 25))  # Shanghai's 20:30: asleep
    llm = FakeLLM(["<ja>……もしもし……？</ja><zh>……喂……？</zh>"])

    spoken, messages = await mano_call.run(llm, [answered(), SleepFrame(0.5)])

    assert spoken == ["……もしもし……？"]
    assert messages[0]["type"] == "user" and messages[0]["text"] == ""
    # The user line carries the turn's timestamp before the answering instruction.
    assert llm.prompts[0][-1].content.endswith(ANSWERING)
    assert "是被制作人的电话吵醒的" in context_of(llm.prompts[0])


async def test_awake_she_waits_for_the_user_to_speak(mano_call: CallUnderTest, settings: Settings):
    await keep_night(settings, time(7), time(23, 30), date(2026, 9, 25))  # Shanghai's 20:30: awake
    llm = FakeLLM()

    spoken, _ = await mano_call.run(llm, [answered(), SleepFrame(0.3)])

    assert spoken == [] and llm.prompts == []


def answered() -> RTVIClientMessageFrame:
    return RTVIClientMessageFrame(msg_id="a1", type="answered", data={})


def test_a_sleep_before_the_last_slot_is_moved_after_it():
    slots = [Slot(time(21, 30), time(23, 30), "乘坐新干线返回东京", "新干线上", "private")]

    kept, wake, sleep, bath = reconciled(slots, time(8), time(22), Bath(time(20, 50), 40))

    assert kept == slots
    assert sleep == time(0)  # 23:30 + 30 minutes
    assert bath == Bath(time(20, 50), 40)  # it ends just as the trip begins


def test_a_bath_running_into_a_slot_is_moved_earlier():
    slots = [Slot(time(21, 30), time(23, 30), "乘坐新干线返回东京", "新干线上", "private")]

    _, _, _, bath = reconciled(slots, None, time(0), Bath(time(21), 40))

    assert bath == Bath(time(20, 45), 40)


def test_a_bath_with_no_gap_before_bed_is_not_taken():
    slots = [Slot(time(9), time(23), "外景", "横滨", "work")]

    _, _, sleep, bath = reconciled(slots, None, time(22), Bath(time(20), 40))

    assert sleep == time(23, 30)
    assert bath is None


def test_a_day_already_agreeing_with_itself_is_left_alone():
    slots = [Slot(time(9), time(12), "练习", "练习室", "lesson")]
    bath = Bath(time(21, 45), 40)

    assert reconciled(slots, time(7), time(23), bath) == (slots, time(7), time(23), bath)


def test_a_wake_after_the_first_slot_is_moved_before_it():
    slots = [Slot(time(8), time(10), "上学", "学校", "school")]

    _, wake, _, _ = reconciled(slots, time(9), time(23), None)

    assert wake == time(7, 30)


def test_sleep_is_never_past_three():
    assert reconciled([], None, time(4), None)[2] == time(3)


CONFLICTING_PLAN = (
    '{"mano": {"slots": [{"start": "21:30", "end": "23:30", "what": "乘坐新干线返回东京",'
    ' "where": "新干线上", "kind": "private", "with": []}],'
    ' "wake": "08:00", "sleep": "22:00"}}'
)


async def test_a_written_day_is_stored_agreeing_with_itself(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock = await talked(make_client, settings, [CONFLICTING_PLAN], [KOGANE_NONE])
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()

    found = await schedule(client)

    assert found is not None
    assert found["sleep"] == "00:00"
    assert found["slots"][0]["end"] == "23:30"
