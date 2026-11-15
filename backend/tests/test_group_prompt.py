from datetime import UTC, datetime, timedelta

from eidolon.group_context import GroupMemberNow, GroupNow
from eidolon.group_prompt import history_block
from eidolon.groups import Line, Member, _say_prompt


def test_group_history_has_relative_time_and_no_producer_state():
    now = datetime(2026, 11, 15, 22, 0, tzinfo=UTC)
    lines = [Line("mano", "こんばんは", (now - timedelta(minutes=5)).isoformat(), "晚上好")]
    told = history_block(lines, now, "Asia/Shanghai", {"mano": "真乃"})
    assert "真乃：晚上好" in told
    assert "制作人" not in told


def test_say_prompt_does_not_carry_producer_state():
    member = Member("mano", "真乃", "i", "s", "真乃")
    prompt = _say_prompt("system", member, [member], [], [], 0.0, [], "names")
    told = "\n".join(m.content for m in prompt)
    assert "好感" not in told and "生日" not in told and "制作人的事" not in told


def test_say_prompt_ends_with_what_the_last_person_said():
    member = Member("mano", "真乃", "i", "s", "真乃")
    hiori = Member("hiori", "灯織", "i", "s", "灯织")
    last = Line("hiori", "おやすみ", "2026-11-15T22:00:00+00:00", "晚安")
    prompt = _say_prompt("system", member, [member, hiori], [last], [], 0.0, [], "names")
    told = "\n".join(m.content for m in prompt)
    assert "# 上一个人说的" in told
    assert "灯織：晚安" in told


def test_group_now_lines_only_the_group_world():
    now = datetime(2026, 11, 15, 22, 0, tzinfo=UTC)
    group = GroupNow(
        "g", now, "Asia/Shanghai",
        (GroupMemberNow("mano", "真乃", "现在在宿舍休息", False),),
    )
    lines = "\n".join(group.lines())
    assert "真乃：现在在宿舍休息" in lines
    assert "制作人" not in lines
