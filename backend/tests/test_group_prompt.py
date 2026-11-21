from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

from eidolon.affinity import Status
from eidolon.characters import load_characters
from eidolon.group_context import GroupMemberNow, GroupNow
from eidolon.group_prompt import history_block
from eidolon.groups import TOPIC_GAP, TOPIC_INTERVAL, Groups, Line, Member, _say_prompt, _topic_prompt
from eidolon.hearts import hearts_path, load_hearts


def characters_with_hearts():
    path = Path(__file__).resolve().parents[2] / "characters" / "characters.toml"
    characters = load_characters(path)
    hearts = load_hearts(hearts_path(path), characters.values())
    return {
        character_id: replace(character, heart=hearts.get(character_id))
        for character_id, character in characters.items()
    }


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
    prompt = _topic_prompt("system", [], "names", [], "")
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
    told = await groups._system("user", member, "周末茶会", [member])
    assert "对制作人态度很冷" in told
    assert "不要提好感度" in told
    assert "吃醋" not in told


async def test_group_romance_tiers_allow_jealousy():
    characters = characters_with_hearts()
    groups = object.__new__(Groups)
    groups._characters = characters  # type: ignore[attr-defined]
    groups._store = None  # type: ignore[attr-defined]
    groups._world = None  # type: ignore[attr-defined]

    class Affinities:
        async def status(self, user_id: str, character_id: str) -> Status:
            assert character_id == "nichika"
            return Status("close", None)

    groups._affinities = Affinities()  # type: ignore[attr-defined]
    member = groups._members_by_id("nichika")
    assert member is not None
    told = await groups._system("user", member, "周末茶会", [member])
    assert "可以按你的性格有一点在意或吃醋" in told
    assert "不要比较谁和制作人更亲近" not in told


async def test_group_members_without_romance_do_not_get_jealousy():
    characters = characters_with_hearts()
    groups = object.__new__(Groups)
    groups._characters = characters  # type: ignore[attr-defined]
    groups._store = None  # type: ignore[attr-defined]
    groups._world = None  # type: ignore[attr-defined]

    class Affinities:
        async def status(self, user_id: str, character_id: str) -> Status:
            assert character_id == "asahi"
            return Status("close", None)

    groups._affinities = Affinities()  # type: ignore[attr-defined]
    member = groups._members_by_id("asahi")
    assert member is not None
    told = await groups._system("user", member, "周末茶会", [member])
    assert "不要比较谁和制作人更亲近" in told
    assert "吃醋" not in told


def bare_groups() -> Groups:
    """A group chat with only the character data: no store, world or affinity to read."""
    groups = object.__new__(Groups)
    groups._store = None  # type: ignore[attr-defined]
    groups._world = None  # type: ignore[attr-defined]
    groups._affinities = None  # type: ignore[attr-defined]
    return groups


async def test_the_group_system_names_itself_and_everyone_in_it():
    member = Member("mano", "真乃", "i", "s", "真乃")
    hiori = Member("hiori", "灯織", "i", "s", "灯织")

    told = await bare_groups()._system("user", member, "周末茶会", [member, hiori])

    assert "「周末茶会」" in told
    assert "群里有灯織" in told
    assert "制作人也在" in told
    assert "不用主动说出来" in told


async def test_a_group_of_one_says_only_she_and_the_producer_are_in_it():
    member = Member("mano", "真乃", "i", "s", "真乃")

    told = await bare_groups()._system("user", member, "一个人的群", [member])

    assert "「一个人的群」" in told
    assert "只有你和制作人" in told
    assert "群里有" not in told


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
