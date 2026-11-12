"""Memory, driven through the memories module's interface: what a user tells any of the
characters is written down in the background, found again in later turns by every
character of that user's (as hearsay when it was told to someone else, ADR-0002), and
can be listed, deleted and cleared; no user ever reaches another's."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

import pytest

from eidolon.characters import Character, load_characters
from eidolon import memory
from eidolon.data_access import Database
from eidolon.llm import ChatLLM
from eidolon.memory import (  # pyright: ignore[reportPrivateUsage]
    MAX_FOUND,
    Memories,
    Memory,
    MemoryTurn,
    _memories_prompt,
    memory_query,
    parse_memories,
)
from eidolon.store import Conversation, Message, Segment, Store, add_turn
from tests.conftest import CharacterConfig
from tests.fakes import FakeBackgroundLLM, FakeEmbedder, FakeReranker
from tests.test_shared_memory import CAT, TRIP, memorized

NOW = datetime(2026, 9, 25, 3, 0, tzinfo=UTC)
TZ = "Asia/Shanghai"


class World:
    """A store with users and conversations for the memories module to remember them by."""

    def __init__(
        self, db: Database, store: Store, characters: dict[str, Character], producer: str
    ) -> None:
        self.db = db
        self.store = store
        self.characters = characters
        self.producer = producer

    @classmethod
    async def made(cls, db: Database, characters: dict[str, Character]) -> "World":
        """The world of the store in `db`, the producer made in it."""
        store = Store(db)
        return cls(db, store, characters, (await store.create_user("producer")).id)

    async def conversation(self, character_id: str, user_id: str | None = None) -> Conversation:
        return await self.store.create_conversation(user_id or self.producer, character_id)

    async def reply(
        self, conversation: Conversation, text: str, reply: Segment = Segment("はいっ", "好的")
    ) -> Message:
        return await self.db.write(add_turn, conversation.id, text, [reply])


@pytest.fixture
async def world(tmp_path: Path, characters: CharacterConfig) -> AsyncIterator[World]:
    db = Database(tmp_path / "eidolon.db")
    try:
        yield await World.made(db, load_characters(characters.path))
    finally:
        await db.close()


MemoriesFactory = Callable[..., Memories]

_writers: WeakKeyDictionary[Memories, ChatLLM] = WeakKeyDictionary()
"""The background model each test's memories write down with, as a turn's models say."""


def writing_with(memories: Memories, llm: ChatLLM) -> Memories:
    """`memories`, whose turns write down with `llm`."""
    _writers[memories] = llm
    return memories


async def begin(memories: Memories, conversation: Conversation) -> MemoryTurn:
    """Begin a turn of `conversation`, writing down with the model `memories` were made with."""
    return await memories.begin(conversation, TZ, _writers.get(memories) or FakeBackgroundLLM())


@pytest.fixture
async def make_memories(world: World) -> AsyncIterator[MemoriesFactory]:
    """Memories on the world's database; with a background model, embedder and reranker
    unless told otherwise (`off=True`)."""
    made: list[Memories] = []

    def factory(
        *replies: str | Exception,
        off: bool = False,
        background_llm: FakeBackgroundLLM | None = None,
        embedder: FakeEmbedder | None = None,
        reranker: FakeReranker | None = None,
    ) -> Memories:
        memories = (
            Memories(world.db, world.characters, lambda: NOW)
            if off
            else writing_with(
                Memories(
                    world.db, world.characters, lambda: NOW,
                    embedder=embedder or FakeEmbedder(),
                    reranker=reranker or FakeReranker("制作人"),
                ),
                background_llm or FakeBackgroundLLM(*replies),
            )
        )
        made.append(memories)
        return memories

    yield factory
    for memories in made:
        await memories.aclose()


async def tell(memories: Memories, world: World, conversation: Conversation, text: str) -> None:
    """Take a turn in which the user says `text`, and write down what it reveals."""
    turn = await begin(memories, conversation)
    history = await world.store.list_messages(conversation.id)
    turn.write_down(history, text, await world.reply(conversation, text))


async def find(memories: Memories, world: World, conversation: Conversation, text: str) -> str:
    """Take a turn in which the user says `text`, finding the memories relevant to it as
    retrieval does, the turn's query embedded apart from the memories."""
    turn = await begin(memories, conversation)
    query = memory_query(await world.store.list_messages(conversation.id), text)
    candidates = await turn.candidates(await FakeEmbedder().embed_query(query))
    return await turn.find(query, candidates) or ""


def line_of(section: str, memory: str) -> str:
    return next(line for line in section.splitlines() if memory in line)


async def test_what_the_user_told_mano_reaches_kogane_as_hearsay(
    world: World, make_memories: MemoriesFactory
):
    memories = make_memories(memorized(CAT))

    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白哦")
    section = await find(memories, world, await world.conversation("kogane"), "你知道我家的猫吗？")

    line = line_of(section, CAT)
    assert "櫻木真乃" in line and "转述" in line
    assert "（今天，制作人对櫻木真乃说的" in line


async def test_memory_dates_are_said_relative_to_today(world: World):
    now = datetime(2026, 11, 10, 12, tzinfo=ZoneInfo(TZ))
    memory = Memory(
        1, "u", "mano", "櫻木真乃",
        "制作人在2026年11月6日开始学做饭，2026-11-09去出差，2026/11/11回来。"
        "更早的是2026年10月1日。",
        "2026-11-09T02:00:00+00:00",
    )

    prompt = _memories_prompt(
        [memory], world.characters["mano"], TZ, "开头", now
    )

    assert "制作人在四天前开始学做饭，昨天去出差，明天回来。更早的是2026年10月1日。" in prompt
    assert "（昨天，制作人对你说的）" in prompt


async def test_a_memory_told_to_the_character_herself_is_not_hearsay(
    world: World, make_memories: MemoriesFactory
):
    memories = make_memories(memorized(CAT))

    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白哦")
    section = await find(memories, world, await world.conversation("mano"), "你还记得我家的猫吗？")

    line = line_of(section, CAT)
    assert "对你说的" in line
    assert "转述" not in line


async def test_a_memory_told_to_a_character_since_removed_is_hearsay_from_another_idol(
    world: World, make_memories: MemoriesFactory
):
    earlier = make_memories(memorized(CAT))
    await tell(earlier, world, await world.conversation("mano"), "我家的猫叫小白哦")
    await earlier.entries(world.producer)  # written down
    without_mano = {"kogane": world.characters["kogane"]}
    memories = Memories(
        world.db, without_mano, lambda: NOW,
        embedder=FakeEmbedder(), reranker=FakeReranker("制作人"),
    )

    section = await find(memories, world, await world.conversation("kogane"), "你知道我家的猫吗？")
    [cat] = await memories.entries(world.producer)
    await memories.aclose()

    assert "听事务所的另一位偶像说" in line_of(section, CAT)
    assert (cat.source_character_id, cat.source_character_name) == ("mano", None)


async def test_a_unit_memory_reaches_only_who_the_source_shares_it_with(world: World):
    """The clerk's `unit` memory reaches her sister, not her sister's unit mate; an idol's
    reaches her unit, not the clerk (GLOSSARY.md「记忆」)."""
    groups = {
        "hazuki": frozenset({"hazuki", "nichika"}),  # 叶月 + 妹妹日花（家庭）
        "nichika": frozenset({"nichika", "mikoto"}),  # SHHis
        "mikoto": frozenset({"nichika", "mikoto"}),
    }
    memories = Memories(
        world.db, world.characters, lambda: NOW,
        share_group=lambda cid: groups.get(cid, frozenset()),
    )
    kept = [
        Memory(1, world.producer, "hazuki", "七草はづき", "制作人请我吃了蛋糕",
               "2026-09-25T00:00:00+00:00", scope="unit"),
        Memory(2, world.producer, "nichika", "七草にちか", "制作人来看我练习",
               "2026-09-25T00:00:00+00:00", scope="unit"),
    ]

    assert [m.id for m in memories._visible(kept, "nichika")] == [1, 2]
    assert [m.id for m in memories._visible(kept, "mikoto")] == [2]
    assert [m.id for m in memories._visible(kept, "hazuki")] == [1]
    await memories.aclose()


async def test_a_memory_is_written_with_its_emotion_and_tiers_strength(
    world: World, make_memories: MemoriesFactory
):
    reply = '{"memories": [{"text": "制作人养了一只猫", "emotion": "开心", "tier": "strong"}]}'
    memories = make_memories(reply)

    await tell(memories, world, await world.conversation("mano"), "我养了猫")
    [kept] = await memories.entries(world.producer)

    assert kept.emotion == "开心"
    assert kept.strength == 1.0  # a strong memory begins fullest
    assert kept.maintain_until is not None  # and holds for its tier's maintenance
    assert kept.kind == "short"


async def test_a_revived_memory_is_refreshed_to_its_new_tier(
    world: World, make_memories: MemoriesFactory
):
    replies = [
        '{"memories": [{"text": "制作人喜欢猫", "tier": "strong"}]}',
        superseding("制作人喜欢狗", "制作人喜欢猫"),
        '{"memories": [{"text": "制作人喜欢猫", "tier": "weak"}]}',
    ]
    memories = make_memories(*replies)

    await tell(memories, world, await world.conversation("mano"), "我喜欢猫")
    await tell(memories, world, await world.conversation("mano"), "其实我喜欢狗")
    await tell(memories, world, await world.conversation("mano"), "还是喜欢猫")

    by_text = {m.text: m for m in await memories.entries(world.producer)}
    assert by_text["制作人喜欢猫"].superseded_by is None  # holds again
    assert by_text["制作人喜欢猫"].strength == 0.5  # refreshed as its new tier, weak


async def test_one_users_memories_never_reach_another_user(
    world: World, make_memories: MemoriesFactory
):
    reranker = FakeReranker("猫")
    memories = make_memories(memorized(CAT), memorized("制作人喜欢狗"), reranker=reranker)
    friend = (await world.store.create_user("friend")).id

    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白")
    # Written down before the friend speaks: the background model's scripted replies go
    # in order, the cat to whichever user asks first.
    await memories.entries(world.producer)
    await tell(memories, world, await world.conversation("mano", friend), "我喜欢狗")
    section = await find(memories, world, await world.conversation("kogane", friend), "你知道我家的猫吗？")

    assert CAT not in section and "制作人喜欢狗" in section
    assert not any(CAT in doc for _, docs in reranker.calls for doc in docs)
    assert [m.text for m in await memories.entries(friend)] == ["制作人喜欢狗"]


async def test_the_background_model_reads_the_exchange_and_what_is_already_remembered(
    world: World, make_memories: MemoriesFactory
):
    background = FakeBackgroundLLM(memorized(CAT), memorized(CAT, TRIP))
    memories = make_memories(background_llm=background)
    conversation = await world.conversation("mano")

    await tell(memories, world, conversation, "我家的猫叫小白哦")
    await tell(memories, world, conversation, "下周六我要去北海道出差")
    listed = await memories.entries(world.producer)

    first, second = ("\n".join(m.content for m in p) for p in background.prompts)
    assert "我家的猫叫小白哦" in first and "櫻木真乃" in first
    assert "2026年9月25日" in first  # so relative dates can be written down as dates
    assert "我家的猫叫小白哦" in second and "下周六我要去北海道出差" in second
    assert CAT in second.split("# 最近的对话")[0]  # already remembered, not written down again
    # Written down twice, remembered once, newest first.
    assert [m.text for m in listed] == [TRIP, CAT]


async def test_the_background_model_is_told_to_keep_only_what_still_matters_later(
    world: World, make_memories: MemoriesFactory
):
    background = FakeBackgroundLLM(memorized())
    memories = make_memories(background_llm=background)

    await tell(memories, world, await world.conversation("mano"), "今天好累")
    await memories.entries(world.producer)

    instructions = next(m.content for m in background.prompts[0] if m.role == "system")
    assert "过后仍有意义" in instructions
    # What lasts a few days and a friend would ask about later is written down…
    assert "持续几天" in instructions and "过后会问起" in instructions
    assert all(event in instructions for event in ("生病", "考试", "面试", "搬家", "出差"))
    # …what is over by the end of the day is not.
    assert "当天就会过去" in instructions
    assert all(state in instructions for state in ("累", "困", "心情不好"))


async def test_the_background_model_is_told_how_to_write_the_other_characters_names(
    world: World,
):
    background = FakeBackgroundLLM(memorized())
    luca = replace(
        world.characters["mano"], id="luca", name="斑鳩ルカ", short_name="ルカ",
        name_zh="斑鸠路加",
    )
    memories = writing_with(
        Memories(
            world.db, {**world.characters, "luca": luca}, lambda: NOW,
            embedder=FakeEmbedder(), reranker=FakeReranker("制作人"),
        ),
        background,
    )
    try:
        await tell(memories, world, await world.conversation("luca"), "我收藏了路加以前 solo 的 CD")
        await memories.entries(world.producer)
    finally:
        await memories.aclose()

    instructions = next(m.content for m in background.prompts[0] if m.role == "system")
    assert "斑鳩ルカ → 斑鸠路加" in instructions
    assert "ルカ → 路加" in instructions
    assert "记忆里一律照它写" in instructions
    assert "不要自己音译" in instructions
    assert "不要把两个角色的名字拼在一起" in instructions


async def test_irrelevant_memories_are_not_found(world: World, make_memories: MemoriesFactory):
    memories = make_memories(memorized(CAT), reranker=FakeReranker("北海道", others=0.001))

    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白哦")

    assert await find(memories, world, await world.conversation("kogane"), "北海道に行ったことある？") == ""


async def test_only_the_most_relevant_few_are_found(world: World, make_memories: MemoriesFactory):
    told = [f"制作人喜欢第{i}种点心" for i in range(1, 8)]
    memories = make_memories(memorized(*told))

    await tell(memories, world, await world.conversation("mano"), "我喜欢好多点心")
    section = await find(memories, world, await world.conversation("mano"), "你知道我喜欢什么点心吗？")

    assert sum(t in section for t in told) == MAX_FOUND


@pytest.mark.parametrize(
    "failure", [RuntimeError("background model is down"), "好的，我记住了", '{"memories": "猫"}']
)
async def test_a_failed_extraction_writes_nothing_down(
    world: World, make_memories: MemoriesFactory, failure: str | Exception
):
    memories = make_memories(failure)

    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白哦")

    assert await memories.entries(world.producer) == []


async def test_a_memory_is_found_once_it_can_be_embedded(
    world: World, make_memories: MemoriesFactory
):
    embedder = FakeEmbedder()
    memories = make_memories(memorized(CAT), embedder=embedder)
    embedder.down = True
    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白哦")
    assert [m.text for m in await memories.entries(world.producer)] == [CAT]

    assert await find(memories, world, await world.conversation("kogane"), "你知道我家的猫吗？") == ""

    embedder.down = False
    assert CAT in await find(memories, world, await world.conversation("kogane"), "你知道我家的猫吗？")


async def test_a_deleted_memory_is_found_no_more(world: World, make_memories: MemoriesFactory):
    background = FakeBackgroundLLM(memorized(CAT, TRIP))
    memories = make_memories(background_llm=background)
    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白，下周六去北海道")
    cat = next(m for m in await memories.entries(world.producer) if m.text == CAT)

    assert await memories.delete(world.producer, cat.id)

    assert [m.text for m in await memories.entries(world.producer)] == [TRIP]
    kogane = await world.conversation("kogane")
    section = await find(memories, world, kogane, "你知道我家的猫吗？")
    assert TRIP in section and CAT not in section
    # Nor is the background model told it is still remembered.
    await tell(memories, world, kogane, "对了")
    await memories.entries(world.producer)
    assert CAT not in "\n".join(m.content for m in background.prompts[-1])


async def test_clearing_forgets_everything(world: World, make_memories: MemoriesFactory):
    """A turn's memories are written in the background; clearing right after the turn
    must not let them turn up afterwards."""
    memories = make_memories(memorized(CAT), memorized(TRIP))
    conversation = await world.conversation("mano")
    await tell(memories, world, conversation, "我家的猫叫小白哦")
    await tell(memories, world, conversation, "下周六我要去北海道出差")

    await memories.clear(world.producer)

    assert await memories.entries(world.producer) == []
    assert await find(memories, world, await world.conversation("kogane"), "你知道我家的猫吗？") == ""


async def test_what_is_written_down_too_slowly_to_wait_for_is_still_forgotten(
    world: World, make_memories: MemoriesFactory, monkeypatch: pytest.MonkeyPatch
):
    """Clearing waits for what is being written down only a while; whatever the turns
    before the clearing write down after that is dropped rather than turning up again."""
    gate = asyncio.Event()
    memories = make_memories(background_llm=FakeBackgroundLLM(memorized(CAT), gate=gate))
    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白哦")
    monkeypatch.setattr(memory, "SETTLE_TIMEOUT", 0.01)

    await memories.clear(world.producer)
    gate.set()  # the background model answers at last
    monkeypatch.setattr(memory, "SETTLE_TIMEOUT", 5.0)

    assert await memories.entries(world.producer) == []


async def test_with_memory_off_turns_neither_find_nor_write_down_but_users_still_manage_theirs(
    world: World, make_memories: MemoriesFactory
):
    """Memories kept while memory was on can still be seen and deleted once it is off."""
    kept = make_memories(memorized(CAT, TRIP))
    await tell(kept, world, await world.conversation("mano"), "我家的猫叫小白，下周六去北海道")
    await kept.entries(world.producer)
    memories = make_memories(off=True)

    assert await find(memories, world, await world.conversation("kogane"), "你知道我家的猫吗？") == ""
    await tell(memories, world, await world.conversation("kogane"), "我最近在学吉他")
    listed = await memories.entries(world.producer)
    assert [m.text for m in listed] == [TRIP, CAT]  # newest first

    cat = next(m for m in listed if m.text == CAT)
    assert await memories.delete(world.producer, cat.id)
    assert [m.text for m in await memories.entries(world.producer)] == [TRIP]
    await memories.clear(world.producer)
    assert await memories.entries(world.producer) == []


async def test_memories_are_embedded_without_a_title(world: World, make_memories: MemoriesFactory):
    embedder = FakeEmbedder()
    memories = make_memories(memorized(CAT), embedder=embedder)

    await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白哦")
    await memories.entries(world.producer)  # written down

    assert embedder.documents == [CAT] and embedder.titles == [None]


async def test_writing_down_embeds_the_turn_with_the_characters_previous_reply_as_people_see_it(
    world: World, make_memories: MemoriesFactory
):
    """「它昨晚又叫了一夜」: the reply before it says what 「它」 is, to show the background model
    the memories nearest it."""
    embedder = FakeEmbedder()
    memories = make_memories(memorized(CAT), memorized(), embedder=embedder)
    conversation = await world.conversation("mano")
    await tell(memories, world, conversation, "我家的猫叫小白哦")
    await world.reply(
        conversation, "我回来了", Segment("[happy] おかえりなさい", "[happy] 欢迎回来，猫还好吗？")
    )

    await tell(memories, world, conversation, "它昨晚又叫了一夜")
    await memories.entries(world.producer)  # written down

    assert embedder.queries == ["我家的猫叫小白哦", "欢迎回来，猫还好吗？\n它昨晚又叫了一夜"]


async def test_writing_down_a_turn_embedded_already_embeds_it_no_more(
    world: World, make_memories: MemoriesFactory
):
    """The turn's query, embedded to find its memories (see `retrieval`), shows the
    background model the memories nearest it all the same."""
    embedder, background = FakeEmbedder(), FakeBackgroundLLM(memorized(CAT), memorized())
    memories = make_memories(embedder=embedder, background_llm=background)
    conversation = await world.conversation("mano")
    await tell(memories, world, conversation, "我家的猫叫小白哦")
    await memories.entries(world.producer)
    turn = await begin(memories, conversation)
    history, text = await world.store.list_messages(conversation.id), "小白最近胖了"
    query_vector = await FakeEmbedder().embed_query(memory_query(history, text))

    turn.write_down(history, text, await world.reply(conversation, text), query_vector)
    await memories.entries(world.producer)

    assert embedder.queries == ["我家的猫叫小白哦"]
    assert f"1. [producer] {CAT}" in background.prompts[-1][-1].content


# Superseding (ADR-0014): what a new memory updates is superseded by it, found no more but
# still listed, and comes back when what superseded it is deleted.

OSAKA = "制作人住在大阪"
TOKYO = "制作人从大阪搬到了东京"
KYOTO = "制作人从东京搬到了京都"
DOG = "制作人养了一只狗"


def test_memories_have_a_subject_and_self_memories_carry_evidence():
    written = parse_memories(json.dumps({"memories": [
        {"text": "制作人养了一只猫", "subject": "producer"},
        {"text": "真乃小时候见过这种花", "subject": "self", "evidence": "この花、見たことある"},
    ]}, ensure_ascii=False))

    assert written is not None
    assert [m.subject for m in written] == ["producer", "self"]
    assert written[1].evidence == "この花、見たことある"
    assert parse_memories('{"memories": [{"text": "制作人喜欢咖啡"}]}')[0].subject == "producer"


async def test_self_memories_are_always_private(world: World, make_memories: MemoriesFactory):
    background = FakeBackgroundLLM(json.dumps({"memories": [{
        "text": "真乃喜欢花", "subject": "self", "scope": "shared",
    }]}, ensure_ascii=False))
    memories = make_memories(background_llm=background)

    await tell(memories, world, await world.conversation("mano"), "我喜欢花")
    [written] = await memories.entries(world.producer)

    assert written.subject == "self"
    assert written.scope == "private"


async def test_two_views_of_one_event_become_one_private_both_memory(
    world: World, make_memories: MemoriesFactory
):
    def answer(prompt: str) -> str:
        if "你负责判断几条记忆" in prompt:
            return json.dumps({"memories": [{
                "text": "浅仓透在福岡吃到好吃的特产，带回一份给正在出差的制作人。",
                "subject": "both", "scope": "private", "emotion": "开心",
                "tier": "strong", "new": [1, 2],
            }]}, ensure_ascii=False)
        return json.dumps({"memories": [
            {"text": "浅仓透在福岡吃到好吃的特产，于是也带给制作人一份。", "subject": "self"},
            {"text": "制作人在出差时收到了浅仓透从福岡带回来的特产。", "subject": "producer"},
        ]}, ensure_ascii=False)

    memories = make_memories(background_llm=FakeBackgroundLLM(answer, answer))
    await tell(memories, world, await world.conversation("mano"), "我从福岡带了特产给你")

    kept = await memories.entries(world.producer)
    holding = [m for m in kept if m.superseded_by is None]
    assert len(holding) == 1
    assert holding[0].subject == "both"
    assert holding[0].scope == "private"
    assert {m.resolution for m in kept if m.superseded_by is not None} == {"merged"}


def known(prompt: str) -> dict[str, int]:
    """The number of each memory the background model was shown as already remembered."""
    section = prompt.split("# 已经记住的事")[1]
    found = {}
    for line in section.splitlines():
        if line.startswith("#"):
            break
        number, _, text = line.partition(". ")
        if not number.isdigit():
            continue
        if text.startswith("[") and "] " in text:
            text = text.partition("] ")[2]
        found[text] = int(number)
    return found


def superseding(text: str, *old: str) -> Callable[[str], str]:
    """The background model's answer writing down `text`, superseding the memories `old`
    by the numbers it was shown them under."""
    def answer(prompt: str) -> str:
        numbers = [known(prompt)[o] for o in old]
        return json.dumps({"memories": [{"text": text, "supersedes": numbers}]},
                          ensure_ascii=False)
    return answer


async def listed(memories: Memories, world: World) -> dict[str, str | None]:
    """Each of the user's memories, with the text of what superseded it, if anything."""
    entries = await memories.entries(world.producer)
    texts = {m.id: m.text for m in entries}
    return {m.text: texts.get(m.superseded_by) if m.superseded_by else None for m in entries}


async def test_the_background_model_is_shown_the_holding_memories_nearest_the_turn(
    world: World, make_memories: MemoriesFactory
):
    """Not the latest: a memory told long ago is still known when it comes up again."""
    others = [f"制作人喜欢第{i}种点心" for i in range(1, 26)]
    background = FakeBackgroundLLM(
        memorized(CAT), *(memorized(*others[i:i + 5]) for i in range(0, 25, 5))
    )
    memories = make_memories(background_llm=background)
    conversation = await world.conversation("mano")
    for said in ["我家的猫叫小白哦", *["我喜欢点心"] * 5]:
        await tell(memories, world, conversation, said)
    await memories.entries(world.producer)

    await tell(memories, world, conversation, "我家的猫最近总是半夜叫")
    await memories.entries(world.producer)

    shown = known("\n".join(m.content for m in background.prompts[-1]))
    assert len(shown) == memory.SHOWN_MEMORIES
    assert shown[CAT] == 1  # the nearest first


async def test_superseded_memories_are_not_shown_to_the_background_model(
    world: World, make_memories: MemoriesFactory
):
    background = FakeBackgroundLLM(memorized(OSAKA), superseding(TOKYO, OSAKA))
    memories = make_memories(background_llm=background)
    await tell(memories, world, await world.conversation("mano"), "我住在大阪")
    await tell(memories, world, await world.conversation("mano"), "我搬到东京了")

    await tell(memories, world, await world.conversation("mano"), "东京好热闹")
    await memories.entries(world.producer)

    assert list(known("\n".join(m.content for m in background.prompts[-1]))) == [TOKYO]


async def test_while_the_embedder_is_down_the_background_model_is_shown_the_latest(
    world: World, make_memories: MemoriesFactory
):
    others = [f"制作人喜欢第{i}种点心" for i in range(1, 26)]
    embedder = FakeEmbedder()
    background = FakeBackgroundLLM(
        memorized(CAT), *(memorized(*others[i:i + 5]) for i in range(0, 25, 5))
    )
    memories = make_memories(background_llm=background, embedder=embedder)
    conversation = await world.conversation("mano")
    for said in ["我家的猫叫小白哦", *["我喜欢点心"] * 5]:
        await tell(memories, world, conversation, said)
    embedder.down = True

    await tell(memories, world, conversation, "我家的猫最近总是半夜叫")
    await memories.entries(world.producer)

    shown = known("\n".join(m.content for m in background.prompts[-1]))
    assert set(shown) == set(others[-memory.SHOWN_MEMORIES:])


async def test_a_new_memory_supersedes_what_it_updates_whoever_it_was_told_to(
    world: World, make_memories: MemoriesFactory
):
    """Told to Mano they live in Osaka and to Kogane they moved to Tokyo, Mano hears of
    Tokyo, not Osaka; Osaka is still listed, as superseded."""
    memories = make_memories(memorized(OSAKA, DOG), superseding(TOKYO, OSAKA))
    await tell(memories, world, await world.conversation("mano"), "我住在大阪，养了一只狗")
    await tell(memories, world, await world.conversation("kogane"), "我搬到东京了")

    assert await listed(memories, world) == {TOKYO: None, DOG: None, OSAKA: TOKYO}
    section = await find(memories, world, await world.conversation("mano"), "你知道我住在哪吗？")
    assert TOKYO in section and "听月岡恋鐘说" in line_of(section, TOKYO)
    assert OSAKA not in section and DOG in section


async def test_a_memory_is_superseded_only_by_someone_who_may_recall_it(world: World):
    """A `unit` memory told to one idol is not shown to an idol outside her group, so that
    idol's model cannot supersede it (GLOSSARY.md「记忆」, ADR-0014 with scope)."""
    background = FakeBackgroundLLM(
        '{"memories": [{"text": "制作人请我吃了蛋糕", "scope": "unit"}]}',
        json.dumps(
            {"memories": [{"text": "制作人请我吃了蛋糕还送了礼物", "supersedes": [1]}]},
            ensure_ascii=False,
        ),
    )
    memories = writing_with(
        Memories(
            world.db, world.characters, lambda: NOW,
            embedder=FakeEmbedder(), reranker=FakeReranker("制作人"),
            # 每个偶像的群组只有她自己：unit 记忆不外传。
            share_group=lambda cid: frozenset({cid}),
        ),
        background,
    )
    await tell(memories, world, await world.conversation("mano"), "我请你吃蛋糕")
    await memories.entries(world.producer)  # 真乃先记下这条 unit 记忆
    await tell(memories, world, await world.conversation("kogane"), "我请你吃蛋糕，还送了礼物")
    await memories.entries(world.producer)

    by_text = {m.text: m for m in await memories.entries(world.producer)}
    assert by_text["制作人请我吃了蛋糕"].superseded_by is None  # 恋鐘不在真乃的群组里，看不到、也取代不了
    assert by_text["制作人请我吃了蛋糕还送了礼物"].superseded_by is None
    await memories.aclose()


async def test_one_memory_may_supersede_several(world: World, make_memories: MemoriesFactory):
    both = "制作人的狗去世了，现在住在东京"
    memories = make_memories(memorized(OSAKA, DOG, CAT), superseding(both, OSAKA, DOG))
    await tell(memories, world, await world.conversation("mano"), "我住在大阪，养了猫和狗")
    await tell(memories, world, await world.conversation("mano"), "狗去世了，我也搬到东京了")

    assert await listed(memories, world) == {both: None, CAT: None, OSAKA: both, DOG: both}


@pytest.mark.parametrize(
    "answer",
    [
        {"memories": [{"text": TOKYO, "supersedes": [0, 3, -1, 99]}]},  # out of range
        {"memories": [{"text": TOKYO, "supersedes": ["1", True, 1.0, None]}]},  # not numbers
        {"memories": [{"text": TOKYO, "supersedes": 1}]},  # not a list
        {"memories": [TOKYO]},  # as before superseding
    ],
)
async def test_what_the_background_model_gets_wrong_supersedes_nothing(
    world: World, make_memories: MemoriesFactory, answer: dict
):
    memories = make_memories(
        memorized(OSAKA, DOG), json.dumps(answer, ensure_ascii=False)
    )
    await tell(memories, world, await world.conversation("mano"), "我住在大阪，养了一只狗")
    await tell(memories, world, await world.conversation("mano"), "我搬到东京了")

    assert await listed(memories, world) == {TOKYO: None, DOG: None, OSAKA: None}


async def test_a_memory_supersedes_only_what_the_background_model_was_shown(
    world: World, make_memories: MemoriesFactory
):
    """Another user's memory, numbered past what was shown, stays as it is."""
    friend = (await world.store.create_user("friend")).id
    memories = make_memories(
        memorized(OSAKA), memorized(DOG),
        json.dumps({"memories": [{"text": TOKYO, "supersedes": [1, 2]}]}, ensure_ascii=False),
    )
    await tell(memories, world, await world.conversation("mano", friend), "我住在大阪")
    await memories.entries(friend)  # written down first, as the scripted replies go in order
    await tell(memories, world, await world.conversation("mano"), "我养了一只狗")
    await tell(memories, world, await world.conversation("mano"), "我搬到东京了")

    assert await listed(memories, world) == {TOKYO: None, DOG: TOKYO}
    assert [m.superseded_by for m in await memories.entries(friend)] == [None]


async def test_a_superseded_memory_comes_back_when_what_superseded_it_is_deleted(
    world: World, make_memories: MemoriesFactory
):
    memories = make_memories(memorized(OSAKA), superseding(TOKYO, OSAKA))
    await tell(memories, world, await world.conversation("mano"), "我住在大阪")
    await tell(memories, world, await world.conversation("kogane"), "我搬到东京了")
    tokyo = next(m for m in await memories.entries(world.producer) if m.text == TOKYO)

    assert await memories.delete(world.producer, tokyo.id)

    assert await listed(memories, world) == {OSAKA: None}
    section = await find(memories, world, await world.conversation("mano"), "你知道我住在哪吗？")
    assert OSAKA in section


async def test_deleting_a_superseded_memory_brings_back_none_before_it(
    world: World, make_memories: MemoriesFactory
):
    """Osaka, then Tokyo, then Kyoto: deleting Tokyo leaves Kyoto superseding Osaka."""
    memories = make_memories(
        memorized(OSAKA), superseding(TOKYO, OSAKA), superseding(KYOTO, TOKYO)
    )
    for said in ["我住在大阪", "我搬到东京了", "我又搬到京都了"]:
        await tell(memories, world, await world.conversation("mano"), said)
    tokyo = next(m for m in await memories.entries(world.producer) if m.text == TOKYO)

    assert await memories.delete(world.producer, tokyo.id)

    assert await listed(memories, world) == {KYOTO: None, OSAKA: KYOTO}
    kyoto = next(m for m in await memories.entries(world.producer) if m.text == KYOTO)
    assert await memories.delete(world.producer, kyoto.id)
    assert await listed(memories, world) == {OSAKA: None}


async def test_a_memory_deleted_while_being_superseded_stays_deleted(
    world: World, make_memories: MemoriesFactory, monkeypatch: pytest.MonkeyPatch
):
    gate = asyncio.Event()
    background = FakeBackgroundLLM(memorized(OSAKA, DOG), superseding(TOKYO, OSAKA, DOG),
                                   gate=gate)
    memories = make_memories(background_llm=background)
    gate.set()
    await tell(memories, world, await world.conversation("mano"), "我住在大阪，养了一只狗")
    osaka = next(m for m in await memories.entries(world.producer) if m.text == OSAKA)
    gate.clear()
    await tell(memories, world, await world.conversation("mano"), "我搬到东京了，狗也送人了")
    monkeypatch.setattr(memory, "SETTLE_TIMEOUT", 0.01)

    assert await memories.delete(world.producer, osaka.id)
    gate.set()  # the background model answers at last
    monkeypatch.setattr(memory, "SETTLE_TIMEOUT", 5.0)

    assert await listed(memories, world) == {TOKYO: None, DOG: TOKYO}


async def test_writing_down_what_was_superseded_brings_it_back(
    world: World, make_memories: MemoriesFactory
):
    """Moving back to Osaka: the memory of living there holds again, superseding Tokyo,
    rather than being written down a second time."""
    memories = make_memories(
        memorized(OSAKA), superseding(TOKYO, OSAKA), superseding(OSAKA, TOKYO)
    )
    for said in ["我住在大阪", "我搬到东京了", "我又搬回大阪了"]:
        await tell(memories, world, await world.conversation("mano"), said)

    assert await listed(memories, world) == {TOKYO: OSAKA, OSAKA: None}
    section = await find(memories, world, await world.conversation("mano"), "你知道我住在哪吗？")
    assert OSAKA in section and TOKYO not in section


async def test_memories_kept_before_superseding_all_hold(tmp_path: Path, world: World):
    """A database from before superseding gains it on opening, every memory holding."""
    with sqlite3.connect(tmp_path / "eidolon.db") as db:
        db.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " user_id TEXT NOT NULL REFERENCES users(id), source_character_id TEXT NOT NULL,"
            " conversation_id TEXT REFERENCES conversations(id), text TEXT NOT NULL,"
            " created_at TEXT NOT NULL, UNIQUE (user_id, text))"
        )
        db.execute(
            "INSERT INTO memories (user_id, source_character_id, text, created_at)"
            " VALUES (?, 'mano', ?, ?)",
            (world.producer, CAT, NOW.isoformat()),
        )
    db.close()
    memories = Memories(
        world.db, world.characters, lambda: NOW,
        embedder=FakeEmbedder(), reranker=FakeReranker("制作人"),
    )

    [cat] = await memories.entries(world.producer)
    section = await find(memories, world, await world.conversation("kogane"), "你知道我家的猫吗？")
    await memories.aclose()

    assert (cat.text, cat.superseded_by) == (CAT, None)
    assert CAT in section
