from __future__ import annotations

import pytest

from datetime import datetime
from zoneinfo import ZoneInfo

from eidolon.relationships import (
    STATES,
    _active_day,
    _allowed,
    _from_text,
    _parse_text,
    _render,
)


def test_the_editable_relationship_text_round_trips_through_its_labels():
    text = _render(
        "repairing",
        "有点别扭，想找机会道歉",
        "比平时客气，有点小心翼翼",
        ["昨天因为安排争执过，还没有真正和好"],
        ["制作人记得她说过喜欢的东西"],
        ["不要突然撒娇，也不要反复道歉"],
    )

    assert _parse_text(text)["关系状态"] == "repairing"
    assert "最近气氛：有点别扭" in text


def test_an_edited_relationship_must_follow_the_labels_and_be_compatible_with_affinity():
    text = "关系状态：warm\n最近气氛：最近相处得不错"

    assert _from_text(text, "trust", False)["state"] == "warm"
    with pytest.raises(ValueError, match="好感阶段"):
        _from_text(text, "distant", False)
    with pytest.raises(ValueError, match="缺少"):
        _from_text("最近气氛：只有气氛", "trust", False)


def test_a_relationship_written_at_three_stays_until_three_the_next_day():
    zone = ZoneInfo("Asia/Shanghai")
    assert _active_day(datetime(2026, 11, 12, 2, 59, tzinfo=zone)).isoformat() == "2026-11-11"
    assert _active_day(datetime(2026, 11, 12, 3, 0, tzinfo=zone)).isoformat() == "2026-11-12"


def test_affinity_is_the_hard_edge_of_the_relationship_states():
    assert _allowed("cold", False) == {"cooling", "tense", "repairing", "neutral"}
    assert _allowed("close", False) == {"neutral", "warm", "affectionate", "repairing"}
    assert _allowed("intimate", False) >= {"affectionate", "romantic"}
    assert _allowed("intimate", True) == {"cooling"}
    assert set(STATES) == {
        "cooling", "tense", "repairing", "neutral", "warm", "affectionate", "romantic",
    }


async def test_the_dev_relationship_route_returns_today_or_nothing(make_client):
    from tests.fakes import FakeLLM

    client = make_client(FakeLLM())
    await client.put("/api/account/dev", json={"on": True})

    res = await client.get("/api/dev/relationship/mano")

    assert res.status_code == 200
    assert res.json()["relationship"] is None

    generated = await client.post("/api/dev/relationship/mano/regenerate")
    assert generated.status_code == 200
    relationship = generated.json()["relationship"]
    assert relationship["state"] == "neutral"
    assert "最近没有新的交流" in relationship["text"]

    saved = await client.put(
        "/api/dev/relationship/mano",
        json={"text": "关系状态：neutral\n最近气氛：最近没有新的交流"},
    )
    assert saved.status_code == 200
    assert saved.json()["relationship"]["edited"] is True
