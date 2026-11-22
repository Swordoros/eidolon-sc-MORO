from datetime import UTC, datetime
from dataclasses import replace

from eidolon.engine import (
    HistoryNote,
    anchor_event_notes,
    assistant_event_notes,
    history_block,
    history_notes,
    photo_caption,
    tail_event_notes,
)
from eidolon.gift_catalog import Gift
from eidolon.reply_format import Segment
from eidolon.store import ConversationEvent, Message, Photo

NOW = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)


def her(id: int, at: str) -> Message:
    return Message(
        id, "character", None, "はい", "嗯", at,
        segments=[Segment("はい", "嗯")],
    )


def user(id: int, at: str) -> Message:
    return Message(id, "user", "送你", None, None, at)


def sent_photo(note: str = "练习后刚歇下来，脸上有点汗但笑得很满足的自拍") -> Photo:
    return Photo("p", "ready", None, f"shot: selfie\nnote: {note}\n")


def test_a_sent_photo_is_described_under_its_message_in_the_history():
    message = replace(her(1, "2026-11-17T19:50:00+00:00"), photo=sent_photo())

    block = history_block([message], {}, NOW, "UTC")

    assert block is not None
    assert "角色：" in block
    assert "旁白：你当时向制作人发送了一张照片：练习后刚歇下来，脸上有点汗但笑得很满足的自拍。" in block
    assert block.index("角色：") < block.index("旁白：你当时向制作人发送了一张照片")


def test_a_failed_photo_keeps_its_marker_and_gets_no_description():
    failed = Photo("p", "failed", "timeout", "shot: selfie\nnote: 不该出现\n")
    message = replace(her(1, "2026-11-17T19:50:00+00:00"), photo=failed)

    block = history_block([message], {}, NOW, "UTC")

    assert block is not None and "（照片没有送到）" in block
    assert "你当时向制作人发送了一张照片" not in block


def test_an_old_note_outside_the_shown_history_is_not_brought_back():
    old = HistoryNote(
        "2026-11-07T05:22:04+00:00", "event",
        "旁白：（背景事件：制作人送过你一份布丁）",
    )
    recent = her(2, "2026-11-17T19:52:00+00:00")

    block = history_block([recent], {1: [old]}, NOW, "UTC")

    assert block is not None
    assert "制作人送过你一份布丁" not in block
    assert "11月7日" not in block


def test_a_reachout_photo_is_described_in_its_history_note():
    note = HistoryNote(
        "2026-11-17T19:50:00+00:00",
        "reachout",
        segments=(Segment("はい", "嗯"),),
        photo=sent_photo("在窗边拍的一张照片"),
    )

    block = history_block([], {None: [note]}, NOW, "UTC")

    assert block is not None
    assert "旁白：你当时向制作人发送了一张照片：在窗边拍的一张照片。" in block


def test_photo_caption_needs_a_sent_photo_and_a_note():
    assert photo_caption(sent_photo()) == "练习后刚歇下来，脸上有点汗但笑得很满足的自拍"
    assert photo_caption(Photo("p", "failed", "timeout", "note: 没有发出去")) is None
    assert photo_caption(Photo("p", "ready", None, "shot: selfie")) is None


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
    assert notes[2] == ["旁白：（背景事件：制作人送过你一份蛋糕）"]
    assert 4 not in notes
    assert 5 not in notes

    block = history_block(
        [message for message in record if isinstance(message, Message)], {}, NOW, "UTC",
        assistant_notes=notes,
    )
    assert block is not None
    assert block.count("（背景事件：制作人送过你一份蛋糕）") == 1
    assert block.index("（背景事件：制作人送过你一份蛋糕）") < block.index("角色：嗯")


def test_a_gift_before_the_current_user_becomes_a_tail_note_not_history():
    record = [
        user(1, "2026-11-17T19:50:00+00:00"),
        her(2, "2026-11-17T19:51:00+00:00"),
        ConversationEvent(
            100, "gift", "2026-11-17T19:52:00+00:00", gift_id="cake", liked=False
        ),
        user(3, "2026-11-17T19:53:00+00:00"),
    ]
    catalog = {"cake": Gift("cake", "蛋糕", "food", "common")}
    history = [message for message in record[:2] if isinstance(message, Message)]

    tail, handled = tail_event_notes(record, catalog, NOW, "UTC")
    notes = history_notes(record, catalog, lambda *_: None, "UTC", skip=handled)

    assert handled == {100}
    assert tail == [
        "[今天 晚上 19:52]\n旁白：（刚刚发生：制作人送了你一份蛋糕）"
    ]
    assert notes.get(3, []) == []
    block = history_block(history, notes, NOW, "UTC")
    assert block is not None
    assert "（背景事件：制作人送过你一份蛋糕）" not in block


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

    assert current == ["旁白：（刚刚发生：制作人送了你一份蛋糕，你很喜欢）"]
    assert previous == ["旁白：（之前发生：制作人送了你一份蛋糕，你已经收下了）"]


def test_an_event_after_the_last_assistant_becomes_a_tail_note():
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
    tail, tail_handled = tail_event_notes(record, catalog, NOW, "UTC")
    history = history_notes(
        record, catalog, lambda *_: None, "UTC", skip=handled | tail_handled,
    )

    assert notes == {} and handled == set()
    assert tail_handled == {100}
    assert "制作人送了你一份蛋糕" in tail[0]
    assert all(not values for values in history.values())
