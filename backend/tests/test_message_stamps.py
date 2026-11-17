"""What the user's messages are stamped with: the latest absolutely, the two before it relatively."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from eidolon.producer import current_message_stamp, earlier_message_stamp, history_stamp
from tests.conftest import ClientFactory
from tests.fakes import FakeClock, FakeLLM
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_current_message_stamp_says_today_time_and_part_of_day():
    now = datetime(2026, 11, 14, 15, 35, tzinfo=SHANGHAI)

    assert current_message_stamp(now, "Asia/Shanghai") == "[今天 下午 15:35]"


def test_earlier_message_stamp_says_how_long_ago_and_that_messages_part_of_day():
    now = datetime(2026, 11, 14, 15, 35, tzinfo=SHANGHAI)

    assert earlier_message_stamp("2026-11-14T06:50:00+00:00", now, "Asia/Shanghai") == "[45分钟前 下午]"
    assert earlier_message_stamp("2026-11-14T04:20:00+00:00", now, "Asia/Shanghai") == "[3小时前 中午]"
    assert earlier_message_stamp("2026-11-13T13:10:00+00:00", now, "Asia/Shanghai") == "[昨天 21:10 晚上]"
    assert earlier_message_stamp("2026-11-12T10:10:00+00:00", now, "Asia/Shanghai") == "[11月12日 18:10 傍晚]"


def test_history_stamps_use_relative_days_and_the_part_of_the_day():
    now = datetime(2026, 11, 14, 15, 35, tzinfo=SHANGHAI)

    assert history_stamp("2026-11-14T06:50:00+00:00", now, "Asia/Shanghai") == "[今天 下午 14:50]"
    assert history_stamp("2026-11-14T04:20:00+00:00", now, "Asia/Shanghai") == "[今天 中午 12:20]"
    assert history_stamp("2026-11-13T13:10:00+00:00", now, "Asia/Shanghai") == "[昨天 晚上]"
    assert history_stamp("2026-11-12T10:10:00+00:00", now, "Asia/Shanghai") == "[两天前 傍晚]"


async def test_history_is_one_chinese_block_with_relative_days_and_the_latest_turn(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch,
):
    clock = FakeClock(datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc))
    said_at = iter([
        "2026-11-14T04:00:00+00:00",
        "2026-11-14T06:50:00+00:00",
        "2026-11-14T07:35:00+00:00",
    ])
    monkeypatch.setattr("eidolon.engine.timestamp", lambda: next(said_at))
    llm = FakeLLM(REPLY, REPLY, REPLY)
    client = make_client(llm, clock=clock)
    conversation = await start_conversation(client)

    await send_turn(client, conversation, "第一句")
    clock.now = datetime(2026, 11, 14, 6, 50, tzinfo=timezone.utc)
    await send_turn(client, conversation, "第二句")
    clock.now = datetime(2026, 11, 14, 7, 35, tzinfo=timezone.utc)
    await send_turn(client, conversation, "第三句")

    prompt = llm.prompts[-1]
    contents = [m.content for m in prompt]
    history = next(m.content for m in prompt if m.role == "system" and "历史对话" in m.content)
    assert "[今天 中午 12:00]" in history
    assert "[今天 下午 14:50]" in history
    assert "制作人：第一句" in history and "角色：[" in history and "好的" in history
    assert "制作人：第二句" in history
    assert "<ja>" not in history and "<zh>" not in history
    assert contents[-1].startswith("------------当前对话--------------\n制作人：[今天 ")
    assert contents[-1].endswith("] 第三句")
