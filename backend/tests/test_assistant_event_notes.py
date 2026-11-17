from datetime import UTC, datetime

from eidolon.engine import assistant_event_notes, history_block, history_notes
from eidolon.gift_catalog import Gift
from eidolon.reply_format import Segment
from eidolon.store import ConversationEvent, Message

NOW = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)


def her(id: int, at: str) -> Message:
    return Message(
        id, "character", None, "はい", "嗯", at,
        segments=[Segment("はい", "嗯")],
    )


def user(id: int, at: str) -> Message:
    return Message(id, "user", "送你", None, None, at)


def test_a_gift_is_attached_to_the_next_two_assistant_messages():
    record = [
        user(1, "2026-11-17T19:50:00+00:00"),
        ConversationEvent(
            100, "gift", "2026-11-17T19:51:00+00:00", gift_id="cake", liked=True
        ),
        her(2, "2026-11-17T19:52:00+00:00"),
        user(3, "2026-11-17T19:53:00+00:00"),
        her(4, "2026-11-17T19:54:00+00:00"),
        her(5, "2026-11-17T19:55:00+00:00"),
    ]
    catalog = {"cake": Gift("cake", "蛋糕", "food", "common")}

    notes, handled = assistant_event_notes(
        record, [m for m in record if isinstance(m, Message)],
        catalog, lambda *_: None, "UTC",
    )

    assert handled == {100}
    assert notes[2] == ["（背景事件：制作人送给你一份蛋糕。这是你喜欢的东西。）"]
    assert notes[4] == ["（上一轮：收到蛋糕）"]
    assert 5 not in notes

    block = history_block(
        [message for message in record if isinstance(message, Message)], {}, NOW, "UTC",
        assistant_notes=notes,
    )
    assert block is not None
    assert "（背景事件：制作人送给你一份蛋糕。这是你喜欢的东西。）" in block
    assert "（上一轮：收到蛋糕）" in block


def test_an_event_after_the_last_assistant_stays_narration():
    record = [
        user(1, "2026-11-17T19:50:00+00:00"),
        her(2, "2026-11-17T19:51:00+00:00"),
        ConversationEvent(100, "gift", "2026-11-17T19:52:00+00:00", gift_id="cake"),
    ]
    catalog = {"cake": Gift("cake", "蛋糕", "food", "common")}

    notes, handled = assistant_event_notes(
        record, [m for m in record if isinstance(m, Message)],
        catalog, lambda *_: None, "UTC",
    )
    narration = history_notes(record, catalog, lambda *_: None, "UTC")

    assert notes == {} and handled == set()
    assert "制作人送给你一份蛋糕" in narration[None][0].text
