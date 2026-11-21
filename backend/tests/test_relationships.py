from __future__ import annotations

import sqlite3

import pytest

from datetime import datetime
from zoneinfo import ZoneInfo

from eidolon.relationships import (
    STATES,
    _active_day,
    _allowed,
    _checked,
    _from_text,
    _open,
    _parse_text,
    _render,
)


def test_the_editable_relationship_text_round_trips_through_its_labels():
    text = _render(
        "repairing",
        "有点别扭，想找机会道歉",
        "可靠，但总把自己排在最后",
        "比平时客气，有点小心翼翼",
        ["昨天因为安排争执过，还没有真正和好"],
        ["制作人记得她说过喜欢的东西"],
        ["容易突然撒娇，或者反复道歉"],
    )

    assert _parse_text(text)["关系状态"] == "repairing"
    assert "最近气氛：有点别扭" in text
    assert "对制作人的评价：可靠，但总把自己排在最后" in text
    assert "还挂着：昨天因为安排争执过" in text


def test_text_edited_before_the_labels_changed_still_parses():
    text = (
        "关系状态：warm\n最近气氛：最近相处得不错\n"
        "她现在的倾向：想多照顾他\n还没解决的事：他还没好好休息\n"
        "最近的温暖：他记得带热咖啡\n不要：客气到生分"
    )

    parsed = _from_text(text, "trust", False)

    assert parsed["tone"] == "想多照顾他"
    assert parsed["open_threads"] == ["他还没好好休息"]
    assert parsed["recent_warmth"] == ["他记得带热咖啡"]
    assert parsed["avoid"] == ["客气到生分"]


def test_each_line_is_cut_to_its_length_and_count():
    checked = _checked({
        "state": "warm",
        "recent": "长" * 80,
        "opinion": "评" * 30,
        "tone": "倾" * 50,
        "open_threads": ["还" * 60] * 3,
        "recent_warmth": ["暖" * 60] * 2,
        "avoid": ["变" * 60] * 2,
    })

    assert len(checked["recent"]) == 60
    assert len(checked["opinion"]) == 20
    assert len(checked["tone"]) == 40
    assert [len(item) for item in checked["open_threads"]] == [50, 50]
    assert [len(item) for item in checked["recent_warmth"]] == [40]
    assert [len(item) for item in checked["avoid"]] == [40]


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
    assert res.json()["baseline"] is None

    saved = await client.put(
        "/api/dev/relationship/mano",
        json={"baseline": "长期关系：会互相照顾。\n对制作人的评价：温柔的人。"},
    )
    assert saved.status_code == 200
    baseline = saved.json()["baseline"]
    assert baseline["relationship"] == "会互相照顾。"
    assert baseline["opinion"] == "温柔的人。"
    first_generated_at = baseline["generated_at"]

    edited = await client.put(
        "/api/dev/relationship/mano",
        json={"baseline": "长期关系：会互相照顾，也会拌嘴。\n对制作人的评价：温柔的人。"},
    )
    assert edited.status_code == 200
    assert edited.json()["baseline"]["relationship"] == "会互相照顾，也会拌嘴。"
    assert edited.json()["baseline"]["generated_at"] == first_generated_at


def test_old_summaries_migrate_their_opinion_and_leave_habits_empty():
    db = sqlite3.connect(":memory:")
    _open(db)
    db.execute(
        "INSERT INTO relationship_summaries"
        " (user_id, character_id, day, state, recent, tone, confidence, generated_text,"
        " generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "u", "mano", "2026-11-20", "warm", "昨天聊得很好。", "想靠近。", 0.8,
            "关系状态：warm\n最近气氛：昨天聊得很好。\n"
            "对制作人的评价：一个温柔的人。\n你现在的倾向：想靠近。",
            "2026-11-21T03:00:00+00:00",
        ),
    )

    _open(db)

    row = db.execute(
        "SELECT relationship, opinion, habits, generated_text FROM relationship_baselines"
        " WHERE user_id = 'u' AND character_id = 'mano'"
    ).fetchone()
    assert row is not None
    relationship, opinion, habits, generated = row
    assert relationship == ""
    assert opinion == "一个温柔的人。"
    assert habits == "[]"
    assert "长期关系：" not in generated
    assert "对制作人的评价：一个温柔的人。" in generated

    _open(db)
    [count] = db.execute("SELECT COUNT(*) FROM relationship_baselines").fetchone()
    assert count == 1
