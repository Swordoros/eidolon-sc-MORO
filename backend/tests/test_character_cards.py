"""Character cards (GLOSSARY.md「角色资料」): the characters the producer names in a turn are
told to the speaker as a card of who they are and how the speaker stands with them, kept
apart from the references the reranker chose, and never quoting the same fact twice."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from eidolon.character_cards import CharacterCards
from eidolon.characters import load_characters
from eidolon.config import Settings
from eidolon.memory import FORGET_THRESHOLD
from eidolon.store import Message
from tests.conftest import MANO_SHEET, CharacterConfig, ClientFactory
from tests.fakes import FakeEmbedder, FakeJudge, FakeLLM, FakeReranker, context_of
from tests.test_delete_account import user_id
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]

MANO_FACTS = """\
# 档案
## 基本信息
櫻木真乃，283プロ所属偶像，illumination STARS 成员。16 岁，高中一年级。
## 兴趣与特技
喜欢鸟，常去公园。

# 人物关系
## 風野灯織
illumination STARS 的伙伴，性格认真。
## 月岡恋鐘
同事务所的偶像。
"""
HIORI_FACTS = """\
# 档案
## 基本信息
風野灯織，illumination STARS 成员。16 岁，高中一年级。
## 兴趣与特技
喜欢咖啡，做事认真。
"""
KOGANE_FACTS = "# 家乡\n来自长崎，最喜欢什锦面。"


@pytest.fixture
def characters(tmp_path: Path) -> CharacterConfig:
    return (
        CharacterConfig(tmp_path / "characters")
        .add("mano", "櫻木真乃", MANO_SHEET, speakers=["真乃"], facts=MANO_FACTS, short_name="真乃")
        .add(
            "hiori",
            "風野灯織",
            "# 人设\n你是風野灯織。",
            speakers=["灯織"],
            facts=HIORI_FACTS,
            short_name="灯織",
            other_names=("风野灯织", "ふうの ひおり", "Fuwano Hiori"),
        )
        .add(
            "kogane",
            "月岡恋鐘",
            "# 人设\n你是月岡恋鐘。",
            speakers=["恋鐘"],
            facts=KOGANE_FACTS,
            short_name="恋鐘",
        )
    )


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


def cards(characters: CharacterConfig) -> tuple[CharacterCards, dict[str, object]]:
    loaded = load_characters(characters.path)
    return CharacterCards(loaded.values()), loaded


def test_a_name_in_what_the_producer_said_is_found(characters: CharacterConfig):
    index, loaded = cards(characters)

    found = index.mentioned(loaded["mano"], [], "灯織ちゃんは元気？")

    assert [character.id for character in found] == ["hiori"]


def test_a_name_in_an_earlier_line_is_found_too(characters: CharacterConfig):
    index, loaded = cards(characters)
    history = [
        Message(1, "user", "灯織の話をしていたよね", None, None, "2026-11-16T00:00:00+00:00"),
        Message(2, "character", None, "うん", "嗯", "2026-11-16T00:00:01+00:00"),
    ]

    found = index.mentioned(loaded["mano"], history, "それでね")

    assert [character.id for character in found] == ["hiori"]


def test_the_speaker_is_never_in_her_own_cards(characters: CharacterConfig):
    index, loaded = cards(characters)

    assert index.mentioned(loaded["mano"], [], "真乃はどう思う？") == []


def test_a_chinese_name_and_a_reading_find_her(characters: CharacterConfig):
    index, loaded = cards(characters)

    assert [character.id for character in index.mentioned(loaded["mano"], [], "灯织呢？")] == ["hiori"]
    assert [character.id for character in index.mentioned(loaded["mano"], [], "ふうのひおり")] == ["hiori"]


def test_two_named_characters_are_the_most(characters: CharacterConfig):
    index, loaded = cards(characters)

    found = index.mentioned(loaded["mano"], [], "灯織と恋鐘と真乃")

    assert [character.id for character in found] == ["hiori", "kogane"]


async def test_a_card_has_her_own_introduction_and_the_speakers_relation(
    characters: CharacterConfig,
):
    index, loaded = cards(characters)

    card = await index.build(loaded["mano"], [loaded["hiori"]], None)

    assert card is not None
    assert card.prompt.startswith("# 她提到的人")
    assert "## 風野灯織" in card.prompt
    assert "illumination STARS 成员" in card.prompt
    assert "和你（真乃）：illumination STARS 的伙伴，性格认真。" in card.prompt
    assert card.covered == {"人物关系 / 風野灯織"}


async def test_without_a_relation_she_gets_only_who_she_is(characters: CharacterConfig):
    index, loaded = cards(characters)

    card = await index.build(loaded["kogane"], [loaded["hiori"]], None)

    assert card is not None
    assert "和你（" not in card.prompt
    assert card.covered == frozenset()


async def test_her_own_facts_are_not_stated_twice(characters: CharacterConfig):
    index, loaded = cards(characters)

    card = await index.build(loaded["mano"], [loaded["hiori"]], None)

    assert card is not None
    assert card.prompt.count("illumination STARS 的伙伴，性格认真。") == 1


def memory(
    memory_id: int,
    source: str,
    text: str,
    days: int = 3,
    scope: str = "shared",
    strength: float = 1.0,
) -> SimpleNamespace:
    created = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    return SimpleNamespace(
        id=memory_id, source_character_id=source, text=text, created_at=created,
        scope=scope, strength=strength, subject="producer",
    )


def test_what_she_told_recently_is_related_to_the_named_character(characters: CharacterConfig):
    index, loaded = cards(characters)

    told = index.related(
        [loaded["hiori"]],
        [
            memory(1, "hiori", "制作人周末要加班，可能没空休息", days=3),
            memory(2, "hiori", "制作人怕冷", scope="private"),
            memory(3, "hiori", "制作人喜欢咖啡", strength=FORGET_THRESHOLD - 0.01),
            memory(4, "kogane", "制作人喜欢什锦面"),
            memory(5, "hiori", "已经用过的记忆"),
        ],
        datetime.now(UTC), "UTC", exclude=(5,),
    )

    assert told is not None
    assert told.startswith("# 相关内容")
    assert "3天前灯織告诉过你：制作人周末要加班，可能没空休息" in told
    assert "怕冷" not in told
    assert "喜欢咖啡" not in told
    assert "什锦面" not in told
    assert "已经用过" not in told


def test_the_related_memory_says_how_long_ago(characters: CharacterConfig):
    index, loaded = cards(characters)

    today = index.related(
        [loaded["hiori"]], [memory(1, "hiori", "今天的事", days=0)], datetime.now(UTC), "UTC"
    )
    yesterday = index.related(
        [loaded["hiori"]], [memory(2, "hiori", "昨天的事", days=1)], datetime.now(UTC), "UTC"
    )

    assert today is not None and "今天灯織告诉过你：今天的事" in today
    assert yesterday is not None and "昨天灯織告诉过你：昨天的事" in yesterday


def test_one_memory_for_each_named_character_and_two_at_most(characters: CharacterConfig):
    index, loaded = cards(characters)

    told = index.related(
        [loaded["hiori"], loaded["kogane"]],
        [
            memory(1, "hiori", "灯織的旧事", days=5),
            memory(2, "hiori", "灯織的新事", days=1),
            memory(3, "kogane", "恋鐘的事", days=2),
        ],
        datetime.now(UTC), "UTC",
    )

    assert told is not None
    assert "灯織的新事" in told and "灯織的旧事" not in told
    assert "恋鐘告诉过你：恋鐘的事" in told
    assert told.count("告诉过你：") == 2


def test_nothing_related_without_a_shared_or_unit_memory(characters: CharacterConfig):
    index, loaded = cards(characters)

    assert index.related(
        [loaded["hiori"]], [memory(1, "hiori", "私事", scope="private")], datetime.now(UTC), "UTC"
    ) is None


async def system_prompt(
    client: httpx.AsyncClient, llm: FakeLLM, text: str, character: str = "mano"
) -> str:
    conversation_id = await start_conversation(client, character)
    events = await send_turn(client, conversation_id, text)
    assert events[-1]["type"] == "done"
    return context_of(llm.prompts[-1])


async def test_a_named_character_reaches_the_prompt_as_a_card(
    make_client: ClientFactory, settings: Settings, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker("風野灯織")
    )

    system = await system_prompt(client, llm, "灯織ちゃんは元気？")

    assert "# 她提到的人" in system
    assert "## 風野灯織" in system
    assert "和你（真乃）" in system
    # The relation reaches the prompt once, in the card, not again under 角色资料.
    assert system.count("illumination STARS 的伙伴，性格认真。") == 1


async def test_a_named_character_is_still_cards_when_nothing_else_is_looked_up(
    make_client: ClientFactory, settings: Settings, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker()
    )

    system = await system_prompt(client, llm, "灯織ちゃんは元気？")

    assert "# 她提到的人" in system
    assert "# 回忆与资料" not in system


async def test_what_the_named_character_told_her_lately_is_related(
    make_client: ClientFactory, settings: Settings, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker()
    )
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "INSERT INTO memories (user_id, source_character_id, text, created_at, scope,"
            " strength, subject, kind) VALUES (?, ?, ?, ?, 'shared', 1.0, 'producer', 'short')",
            (
                user_id(settings.database_path, "producer"),
                "hiori",
                "制作人周末要加班，可能没空休息",
                datetime.now(UTC).isoformat(),
            ),
        )

    system = await system_prompt(client, llm, "灯織ちゃんは元気？")

    assert "# 相关内容" in system
    assert "今天灯織告诉过你：制作人周末要加班" in system
