from datetime import UTC, datetime

from eidolon.engine import (
    anchor_event_notes,
    assistant_event_notes,
    history_block,
    history_notes,
)
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


def test_a_gift_is_attached_once_to_the_next_assistant_message():
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
    assert notes[2] == ["旁白：（背景事件：制作人送了你一份蛋糕）"]
    assert 4 not in notes
    assert 5 not in notes

    block = history_block(
        [message for message in record if isinstance(message, Message)], {}, NOW, "UTC",
        assistant_notes=notes,
    )
    assert block is not None
    assert block.count("（背景事件：制作人送了你一份蛋糕）") == 1
    assert block.index("（背景事件：制作人送了你一份蛋糕）") < block.index("角色：嗯")


def test_the_repeated_last_line_gets_the_current_or_previous_gift_note():
    record = [
        user(1, "2026-11-17T19:50:00+00:00"),
        ConversationEvent(
            100, "gift", "2026-11-17T19:51:00+00:00", gift_id="cake", liked=True
        ),
        her(2, "2026-11-17T19:52:00+00:00"),
        user(3, "2026-11-17T19:53:00+00:00"),
        her(4, "2026-11-17T19:54:00+00:00"),
    ]
    catalog = {"cake": Gift("cake", "蛋糕", "food", "common")}

    current = anchor_event_notes(record, her(2, "2026-11-17T19:52:00+00:00"), catalog)
    previous = anchor_event_notes(record, her(4, "2026-11-17T19:54:00+00:00"), catalog)

    assert current == ["旁白：（背景事件：刚刚制作人送了你一份蛋糕，你很喜欢）"]
    assert previous == ["旁白：（背景事件：制作人送了你一份蛋糕，你已经收下了）"]


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
    assert "制作人送了你一份蛋糕" in narration[None][0].text
