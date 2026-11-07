"""The night-visit window (夜间搭话): 20:00 through 04:59, its deadlines and its night key."""

from datetime import datetime, timezone

from eidolon.visit_outreach.policy import eligibility, first_body_deadline, night_key

DAY = datetime(2026, 11, 7, tzinfo=timezone.utc)


def at(hour: int, minute: int = 0) -> datetime:
    return DAY.replace(hour=hour, minute=minute)


def test_the_window_is_20_to_5_not_22_to_5():
    """By day, from 05:00 to 19:59, a visit is outside the window; the night hours pass
    through to whatever a conversation would say of them."""
    for hour in (5, 6, 12, 19):
        assert eligibility(at(hour), at(hour), None, None, False) == "outside_window"
    for hour in (20, 21, 23, 0, 3, 4):
        assert eligibility(at(hour), at(hour), None, None, False) != "outside_window"


def test_first_body_deadline_is_the_next_5_am_from_20_on():
    assert first_body_deadline(at(20)) == DAY.replace(day=8, hour=5)
    assert first_body_deadline(at(23, 59)) == DAY.replace(day=8, hour=5)
    assert first_body_deadline(at(19)) == DAY.replace(hour=5)
    assert first_body_deadline(at(4)) == DAY.replace(hour=5)


def test_night_key_keeps_5_am_as_the_cut():
    """The small hours belong to the night begun the day before; 05:00 is the new day."""
    assert night_key(at(3)) == "2026-11-06"
    assert night_key(at(4)) == "2026-11-06"
    assert night_key(at(5)) == "2026-11-07"
    assert night_key(at(20)) == "2026-11-07"
