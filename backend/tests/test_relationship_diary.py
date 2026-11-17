from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eidolon.relationships import Relationships, _diary_for, _latest_row


class FakeDB:
    def __init__(self, diary):
        self.diary = diary

    async def read(self, body, *args):
        if body is _latest_row:
            return None
        if body is _diary_for:
            return self.diary
        raise AssertionError(body)


def status(tier: str):
    return SimpleNamespace(tier=tier, cooldown_until=None)


async def told(tier: str) -> str | None:
    relationship = object.__new__(Relationships)
    relationship._db = FakeDB((  # type: ignore[attr-defined]
        [
            {"ja": "今日は", "zh": "今天很开心。"},
            {"ja": "また明日", "zh": "明天也想见你。"},
        ],
        [{"ja": "ないしょ", "zh": "其实有点不安。"}],
    ))
    return await relationship.prompt(
        "u", "a", datetime(2026, 11, 17, 20, 0, tzinfo=UTC), "UTC", status(tier)
    )


@pytest.mark.parametrize("tier", ["cold", "distant"])
async def test_diary_is_hidden_below_trust(tier: str):
    assert await told(tier) is None


async def test_trust_sees_all_normal_pages_but_not_the_private_one():
    text = await told("trust")
    assert text is not None
    assert "今天很开心。" in text
    assert "明天也想见你。" in text
    assert "其实有点不安。" not in text


async def test_close_sees_all_normal_pages_but_not_the_private_one():
    text = await told("close")
    assert text is not None
    assert "今天很开心。" in text
    assert "明天也想见你。" in text
    assert "其实有点不安。" not in text


async def test_intimate_sees_the_private_page_too():
    text = await told("intimate")
    assert text is not None
    assert "今天很开心。" in text
    assert "其实有点不安。" in text
