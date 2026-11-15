from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from eidolon.affinity import Status
from eidolon.characters import load_characters
from eidolon.group_context import GroupMemberNow, GroupNow
from eidolon.group_prompt import history_block
from eidolon.groups import TOPIC_GAP, TOPIC_INTERVAL, Groups, Line, Member, _say_prompt, _topic_prompt


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


def test_topic_prompt_ends_with_the_task():
    member = Member("mano", "真乃", "i", "s", "真乃")
    prompt = _topic_prompt("system", member, [member], [], "names", [], "")
    told = prompt[-1].content
    assert told.rstrip().endswith("不要写「新话题：」这样的前缀。")


def test_a_member_uses_her_group_sheet():
    characters = load_characters(Path(__file__).resolve().parents[2] / "characters" / "characters.toml")
    groups = object.__new__(Groups)
    groups._characters = characters  # type: ignore[attr-defined]
    member = groups._members_by_id("nichika")
    assert member is not None
    assert member.sheet.startswith("# 群聊摘要")
    assert "姐姐七草叶月" in member.sheet


def test_the_topic_cooldown_is_eight_hours():
    assert TOPIC_INTERVAL == timedelta(hours=8)
    assert TOPIC_GAP == (timedelta(hours=8), timedelta(hours=8))


async def test_group_system_injects_only_her_own_affinity():
    characters = load_characters(Path(__file__).resolve().parents[2] / "characters" / "characters.toml")
    groups = object.__new__(Groups)
    groups._characters = characters  # type: ignore[attr-defined]
    groups._store = None  # type: ignore[attr-defined]
    groups._world = None  # type: ignore[attr-defined]

    class Affinities:
        async def status(self, user_id: str, character_id: str) -> Status:
            assert character_id == "nichika"
            return Status("cold", None)

    groups._affinities = Affinities()  # type: ignore[attr-defined]
    member = groups._members_by_id("nichika")
    assert member is not None
    told = await groups._system("user", member)
    assert "对制作人态度很冷" in told
    assert "不要提好感度" in told


async def test_group_memories_are_three_own_and_one_public():
    class Memories:
        async def kept(self, user_id: str):
            return [
                SimpleNamespace(text="自己的旧记忆", source_character_id="mano", scope="private"),
                SimpleNamespace(text="自己的记忆一", source_character_id="mano", scope="private"),
                SimpleNamespace(text="自己的记忆二", source_character_id="mano", scope="private"),
                SimpleNamespace(text="自己的记忆三", source_character_id="mano", scope="private"),
                SimpleNamespace(text="别人的私事", source_character_id="hiori", scope="private"),
                SimpleNamespace(text="公开记忆旧", source_character_id="hiori", scope="shared"),
                SimpleNamespace(text="另一件公开的事", source_character_id="kogane", scope="shared"),
            ]

    groups = object.__new__(Groups)
    groups._memories = Memories()  # type: ignore[attr-defined]
    member = Member("mano", "真乃", "i", "s", "真乃")
    told = await groups._material("user", member)
    assert "自己的记忆一" in told
    assert "自己的记忆二" in told
    assert "自己的记忆三" in told
    assert "自己的旧记忆" not in told
    assert "别人的私事" not in told
    assert "公开记忆旧" not in told  # only the latest public one is kept
    assert "另一件公开的事" in told
