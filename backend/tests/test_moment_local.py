from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from eidolon.moments import _MomentCandidate, _plan_local
from eidolon.schedules import Schedule, Slot
from tests.fakes import FakeRandom

DAY = date(2026, 11, 16)
ZONE = "Asia/Shanghai"
Z = ZoneInfo(ZONE)
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=Z)


def schedule(kind: str, start=time(9), end=time(12), sleep=time(23)) -> Schedule:
    return Schedule(
        DAY, (Slot(start, end, "拍摄", "横滨", kind),), (), time(7), sleep,
    )


def test_work_lesson_and_live_are_work_moments_in_the_producers_time():
    for kind in ("work", "lesson"):
        planned = _plan_local(
            [_MomentCandidate("juri", "close", False, schedule(kind), None)],
            DAY, NOW, 3, ZONE, FakeRandom(499, 500, 0),
        )
        [moment] = planned
        assert moment.kind == "work"
        assert moment.due_at.tzinfo == Z

    planned = _plan_local(
        [_MomentCandidate("juri", "close", False, schedule("live"), None)],
        DAY, NOW, 3, ZONE, FakeRandom(499, 500, 0),
    )
    [moment] = planned
    assert moment.due_at.hour >= 12


def test_at_most_three_characters_and_no_one_asleep():
    candidates = [
        _MomentCandidate(f"idol{i}", "intimate", False, None, None)
        for i in range(5)
    ]
    planned = _plan_local(candidates, DAY, NOW, 5, ZONE, FakeRandom(*([0] * 20)))
    assert len(planned) == 3

    asleep = schedule("work", end=time(23), sleep=time(22))
    assert _plan_local(
        [_MomentCandidate("juri", "close", False, asleep, None)],
        DAY, NOW, 3, ZONE, FakeRandom(499, 999, 0),
    ) == []
