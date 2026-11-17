from datetime import date
from types import SimpleNamespace

import pytest

from eidolon.relationships import Relationships, _diary_for


class FakeDB:
    def __init__(self, diary):
        self.diary = diary

    async def read(self, body, *args):
        if body is _diary_for:
            return self.diary
        raise AssertionError(body)


def status(tier: str):
    return SimpleNamespace(tier=tier, cooldown_until=None)


async def material(tier: str) -> str | None:
    relationship = object.__new__(Relationships)
    relationship._db = FakeDB((  # type: ignore[attr-defined]
        [
            {"ja": "今日は", "zh": "今天很开心。"},
            {"ja": "また明日", "zh": "明天也想见你。"},
        ],
        [{"ja": "ないしょ", "zh": "其实有点不安。"}],
    ))
    return await relationship._diary_material(  # type: ignore[attr-defined]
        "u", "a", date(2026, 11, 16), status(tier)
    )


@pytest.mark.parametrize("tier", ["cold", "distant"])
async def test_diary_is_hidden_below_trust(tier: str):
    assert await material(tier) is None


@pytest.mark.parametrize("tier", ["trust", "close"])
async def test_trust_and_close_supply_all_normal_pages(tier: str):
    text = await material(tier)
    assert text is not None
    assert "今天很开心。" in text
    assert "明天也想见你。" in text
    assert "其实有点不安。" not in text


async def test_intimate_supplies_the_private_page_too():
    text = await material("intimate")
    assert text is not None
    assert "今天很开心。" in text
    assert "其实有点不安。" in text
    assert "不要写进 recent" in text


class FakeLLM:
    def __init__(self):
        self.messages = None

    async def stream(self, messages):
        self.messages = messages
        yield (
            '{"state":"warm","recent":"昨天聊过。","opinion":"温柔。",'
            '"tone":"想靠近。","open_threads":[],"recent_warmth":[],'
            '"avoid":[],"confidence":0.8}'
        )


class Lease:
    def __init__(self, llm):
        self.keys = SimpleNamespace(memory_llm=llm)

    def release(self):
        pass


async def test_the_diary_is_given_to_the_summarizing_model():
    llm = FakeLLM()
    relationship = object.__new__(Relationships)
    relationship._config = SimpleNamespace(lease=lambda: Lease(llm))  # type: ignore[attr-defined]

    await relationship._ask(  # type: ignore[attr-defined]
        SimpleNamespace(name="角色"),
        status("close"),
        [],
        None,
        "[20:00] 制作人：晚上好。",
        "# 昨天的日记\n今天很开心。",
    )

    assert llm.messages is not None
    prompt = llm.messages[-1].content
    assert "# 昨天的日记" in prompt
    assert "今天很开心。" in prompt
