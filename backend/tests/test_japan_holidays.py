from datetime import date, time

from eidolon.engine import day_schedule_rules
from eidolon.japan_holidays import day_kind, holiday_name
from eidolon.schedules import Slot, trim_lessons


def lesson(start: int, end: int, *with_: str) -> Slot:
    return Slot(time(start), time(end), "练习", "练习室", "lesson", with_)


def test_japanese_holidays_include_substitute_and_between_holidays():
    assert day_kind(date(2026, 5, 3)) == "holiday"
    assert holiday_name(date(2026, 5, 3)) == "憲法記念日"
    assert holiday_name(date(2026, 5, 6)) == "振替休日"
    assert holiday_name(date(2026, 9, 22)) == "国民の休日"
    assert holiday_name(date(2027, 3, 22)) == "振替休日"
    assert day_kind(date(2026, 11, 21)) == "weekend"
    assert day_kind(date(2026, 11, 24)) == "workday"


def test_weekend_keeps_one_lesson_group_lesson_included():
    kept = trim_lessons(
        [lesson(9, 10, "mano"), lesson(11, 12), lesson(13, 14)],
        date(2026, 11, 21),
    )

    assert [(slot.start, slot.with_) for slot in kept] == [
        (time(9), ("mano",)),
    ]


def test_a_workday_keeps_every_lesson():
    slots = [lesson(9, 10), lesson(11, 12)]
    assert trim_lessons(slots, date(2026, 11, 24)) == slots


def test_the_day_rules_name_the_holiday_and_its_character():
    saturday = day_schedule_rules(date(2026, 11, 21))
    new_year = day_schedule_rules(date(2027, 1, 1))
    workday = day_schedule_rules(date(2026, 11, 24))

    assert "周末" in saturday
    assert "lesson 最多 1 段" in saturday
    assert "元日" in new_year
    assert "初詣" in new_year
    assert "工作日" in workday
