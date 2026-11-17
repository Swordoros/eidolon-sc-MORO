from datetime import UTC, datetime, timedelta

from eidolon.engine import anchor_note, anchored, recent_reply
from eidolon.reply_format import Segment
from eidolon.store import Message

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def her(minutes: int) -> Message:
    return Message(
        1, "character", None, "はい", "嗯",
        (NOW - timedelta(minutes=minutes)).isoformat(), segments=[Segment("はい", "嗯")],
    )


def test_her_last_line_is_repeated_under_six_hours():
    assert recent_reply([her(30)], NOW) is not None
    assert recent_reply([her(60)], NOW) is not None
    assert recent_reply([her(6 * 60 - 1)], NOW) is not None
    assert recent_reply([her(6 * 60)], NOW) is None


def test_a_last_line_over_six_hours_ago_is_not_repeated():
    assert recent_reply([her(6 * 60 + 1)], NOW) is None
    assert recent_reply([her(24 * 60)], NOW) is None


def test_a_last_user_message_is_not_repeated_as_hers():
    said = Message(1, "user", "在吗", None, None, NOW.isoformat())

    assert recent_reply([said], NOW) is None


def test_the_note_says_how_long_ago_from_one_hour_on():
    assert anchor_note(her(30), NOW) is None
    assert anchor_note(her(60), NOW) == "（下面这句是你 1 小时前说的上一句，不是刚刚说的。）"
    assert anchor_note(her(185), NOW) == "（下面这句是你 3 小时前说的上一句，不是刚刚说的。）"
    assert anchor_note(None, NOW) is None


def test_the_repeated_line_is_marked_as_the_characters():
    assert anchored(her(30)).startswith("角色：<ja>はい</ja>")
    assert anchored(her(30), NOW).startswith("角色：[今天 晚上 30分钟前]")
    assert "<ja>はい</ja>" in anchored(her(30), NOW)
