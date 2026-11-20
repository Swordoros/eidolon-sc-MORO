"""Text conversation turns, driven through the backend's external API."""

import asyncio
import json

import httpx

from eidolon.llm import ChatMessage
from eidolon.characters import EXTRA_NAME_TRANSLATIONS
from eidolon.engine import SEGMENT_CORRECTION
from eidolon.reply_format import FORMAT_INSTRUCTIONS, names_instructions
from tests.conftest import MANO_SHEET, ClientFactory
from tests.fakes import FakeLLM, system_of


async def start_conversation(client: httpx.AsyncClient, character: str = "mano") -> str:
    res = await client.post("/api/conversations", json={"character_id": character})
    assert res.status_code == 201
    return res.json()["id"]


def without_stamp(content: str) -> str:
    """A prompt line without the time stamp the turn puts before the user's latest words."""
    return content.split("] ", 1)[1] if content.startswith("[") and "] " in content else content


async def send_turn(
    client: httpx.AsyncClient,
    conversation_id: str,
    text: str,
    own_llm: dict | None = None,
    *,
    own_keys: dict | None = None,
    read_aloud_model: str | None = None,
    listening: str | None = None,
    segment_complete: bool = False,
) -> list[dict]:
    """Send a text turn, bringing the user's `own_keys` if given, or else their `own_llm`
    alone (as `own_keys`, which turns and calls bring alike), and the model of their own key
    for speech synthesis if given (`read_aloud_model`), and collect the server-sent events it
    streams back."""
    events: list[dict] = []
    own_keys = own_keys or ({"llm": own_llm} if own_llm else None)
    body = {"text": text} | ({"own_keys": own_keys} if own_keys else {}) | (
        {"read_aloud_model": read_aloud_model} if read_aloud_model else {}
    ) | ({"listening": listening} if listening else {})
    query = "?segment_complete=1" if segment_complete else ""
    async with client.stream(
        "POST", f"/api/conversations/{conversation_id}/turns{query}", json=body
    ) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        async for line in res.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


async def leave_turn(
    client: httpx.AsyncClient, conversation_id: str, text: str, *, after: str
) -> list[dict]:
    """Send a text turn and leave, closing the page, as soon as an event of type `after`
    arrives; the events that came by then.

    Closing `client.stream(...)` early never reaches the app: httpx's ASGITransport runs
    the app to the end first. So this drives the app as uvicorn does, on ASGI spec 2.3,
    where the streaming response listens for `http.disconnect` and cancels the stream."""
    request = client.build_request(
        "POST", f"/api/conversations/{conversation_id}/turns", json={"text": text}
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": request.method,
        "scheme": "http",
        "path": request.url.path,
        "raw_path": request.url.raw_path,
        "query_string": b"",
        "headers": [(name.lower(), value) for name, value in request.headers.raw],
        "server": ("test", 80),
        "client": ("127.0.0.1", 50000),
        "root_path": "",
    }
    events: list[dict] = []
    left = asyncio.Event()
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": request.content, "more_body": False}
        await left.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        for line in message.get("body", b"").decode().splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
                if events[-1]["type"] == after:
                    left.set()

    app = client._transport.app  # pyright: ignore[reportAttributeAccessIssue]
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    return events


async def test_reply_streams_japanese_and_chinese_subtitle(make_client: ClientFactory):
    llm = FakeLLM(["<ja>こんにちは、プロデューサーさん！</ja><zh>你好，制作人！</zh>"])
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "你好呀")

    assert events[0] == {"type": "user", "text": "你好呀"}
    done = events[-1]
    assert done["type"] == "done"
    assert done["message"]["ja"] == "こんにちは、プロデューサーさん！"
    assert done["message"]["zh"] == "你好，制作人！"


def deltas(events: list[dict], lang: str) -> list[tuple[int, str]]:
    return [(e["segment"], e["text"]) for e in events if e["type"] == "delta" and e["lang"] == lang]


async def test_reply_is_streamed_segment_by_segment_even_when_tags_are_split(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<j", "a>ほわっ、", "プロデューサーさん", "！</", "ja><zh>哇，制作人！", "</zh>\n",
         "<ja>今日は</ja><", "zh>今天</zh>"]
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "こんにちは")

    assert deltas(events, "ja") == [(0, "ほわっ、"), (0, "プロデューサーさん"), (0, "！"), (1, "今日は")]
    assert deltas(events, "zh") == [(0, "哇，制作人！"), (1, "今天")]
    assert events[-1]["message"]["ja"] == "ほわっ、プロデューサーさん！今日は"
    assert events[-1]["message"]["zh"] == "哇，制作人！今天"


async def test_finished_segments_are_streamed_only_when_the_page_asks(
    make_client: ClientFactory,
):
    reply = ["<ja>一</ja><zh>一</zh>", "<ja>二</ja><zh>二</zh>"]
    llm = FakeLLM(reply, reply)
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    default = await send_turn(client, conversation_id, "你好")
    asked = await send_turn(client, conversation_id, "你好", segment_complete=True)

    assert [e for e in default if e["type"] == "segment-complete"] == []
    assert [(e["type"], e["segment"]) for e in asked if e["type"] == "segment-complete"] == [
        ("segment-complete", 0),
        ("segment-complete", 1),
    ]


async def test_reply_that_ignores_the_format_is_asked_to_write_it_again(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["えっと、", "ありがとうございますっ"],
        ["<ja>えっと、ありがとうございますっ</ja><zh>那个，谢谢您</zh>"],
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "谢谢")

    assert events[-1]["message"]["ja"] == "えっと、ありがとうございますっ"
    assert events[-1]["message"]["zh"] == "那个，谢谢您"
    assert len(llm.prompts) == 2
    assert SEGMENT_CORRECTION.split("{", 1)[0] in llm.prompts[1][-1].content


async def test_reply_with_only_a_chinese_subtitle_is_asked_to_write_it_again(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<zh>只有中文字幕</zh>"],
        ["<ja>日本語だけ</ja><zh>只有中文字幕</zh>"],
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "测试")

    assert events[-1]["message"]["ja"] == "日本語だけ"
    assert events[-1]["message"]["zh"] == "只有中文字幕"
    assert len(llm.prompts) == 2


async def test_empty_correction_falls_back_to_the_original_prompt(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<zh>只有中文字幕</zh>"],
        ["  "],
        ["<ja>原文</ja><zh>中文字幕</zh>"],
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "测试")

    assert events[-1]["message"]["ja"] == "原文"
    assert events[-1]["message"]["zh"] == "中文字幕"
    assert len(llm.prompts) == 3
    assert llm.prompts[1][-1].role == "user"
    assert SEGMENT_CORRECTION.split("{", 1)[0] in llm.prompts[1][-1].content


async def test_two_broken_attempts_show_no_invalid_text(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<zh>至少这段中文还可以读</zh>"],
        ["<ja>日文</ja><zh></zh>"],
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "测试")

    assert events[-1]["type"] == "error"
    assert "至少这段中文还可以读" not in str(events)


async def test_a_broken_segment_is_shown_readable_after_its_repair_fails(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<ja>第一句</ja><zh>第一句</zh><ja>坏句</ja><zh></zh>"],
        ["这不是可用的单段回复"],
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "测试")

    shown = "".join(e["text"] for e in events if e["type"] == "delta")
    speech = "".join(e["text"] for e in events if e["type"] == "speech-delta")
    assert "第一句" in shown and "坏句" in shown
    assert "坏句" not in speech
    assert events[-1]["message"]["segments"] == [
        {"ja": "第一句", "zh": "第一句"},
        {"ja": "坏句", "zh": ""},
    ]
    assert len(llm.prompts) == 2  # no whole-reply retry after a valid prefix


async def test_a_broken_middle_segment_is_repaired_without_replaying_the_first(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<ja>第一句</ja><zh>第一句</zh><ja>坏句</ja><zh></zh>"],
        ["<ja>修好的第二句</ja><zh>修好的第二句</zh>"],
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "测试")

    shown = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "第一句" in shown
    assert "修好的第二句" in shown
    assert "坏句" not in shown
    assert events[-1]["message"]["segments"] == [
        {"ja": "第一句", "zh": "第一句"},
        {"ja": "修好的第二句", "zh": "修好的第二句"},
    ]
    assert len(llm.prompts) == 2


async def test_both_sides_of_a_turn_are_kept_in_the_conversation_record(
    make_client: ClientFactory,
):
    client = make_client(
        FakeLLM(["<ja>おはようございます！</ja><zh>早上好！</zh><ja>ほわっ</ja><zh>哇</zh>"])
    )
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "早上好")

    reopened = make_client(FakeLLM())
    listed = (await reopened.get("/api/conversations", params={"character_id": "mano"})).json()
    messages = (await reopened.get(f"/api/conversations/{conversation_id}/messages")).json()

    assert [c["id"] for c in listed] == [conversation_id]
    assert [(m["role"], m["text"], m["ja"], m["zh"]) for m in messages] == [
        ("user", "早上好", None, None),
        ("character", None, "おはようございます！ほわっ", "早上好！哇"),
    ]
    assert messages[1]["segments"] == [
        {"ja": "おはようございます！", "zh": "早上好！"},
        {"ja": "ほわっ", "zh": "哇"},
    ]


async def test_prompt_carries_the_character_sheet_and_the_conversation_so_far(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<ja>ピーちゃんです！</ja><zh>是小皮！</zh>"],
        ["<ja>はいっ</ja><zh>嗯！</zh>"],
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, "这只鸽子叫什么？")
    await send_turn(client, conversation_id, "好可爱")

    # Without a world setting, the part every user's turns with her share is her sheet, her
    # birthday and how she is to format her reply, and how the names are written.
    assert llm.prompts[1][0] == ChatMessage(
        "system",
        f"{MANO_SHEET}\n\n你的生日是4月25日。\n\n{FORMAT_INSTRUCTIONS}"
        f"\n\n{names_instructions(EXTRA_NAME_TRANSLATIONS)}",
        cache_breakpoint=True,
    )
    system = system_of(llm.prompts[1])
    assert "櫻木真乃" in system
    assert "<ja>" in system and "<zh>" in system
    prompt = llm.prompts[1]
    history = next(
        m.content for m in prompt if m.role == "system" and "历史对话" in m.content
    )
    assert "制作人：这只鸽子叫什么？" in history
    last = [m for m in prompt if m.role == "assistant"][-1]
    assert "角色：" in last.content and "是小皮！" in last.content
    assert "<ja>" in last.content and "<zh>" in last.content
    assert "<ja>" not in history and "<zh>" not in history
    assert "------------当前对话--------------" in prompt[-1].content
    assert "制作人：" in prompt[-1].content and "好可爱" in prompt[-1].content


async def test_conversations_are_kept_apart(make_client: ClientFactory):
    llm = FakeLLM(["<ja>一</ja><zh>一</zh>"], ["<ja>二</ja><zh>二</zh>"])
    client = make_client(llm)
    first = await start_conversation(client)
    second = await start_conversation(client)

    await send_turn(client, first, "第一次")
    await send_turn(client, second, "第二次")

    # The second conversation opens with the tail of the first, so it does not start blank.
    prompt = llm.prompts[1]
    history = next(
        m.content for m in prompt if m.role == "system" and "历史对话" in m.content
    )
    assert "制作人：第一次" in history
    assert "角色：" in history and "一" in history
    assert "<ja>" not in history
    assert "第二次" in prompt[-1].content
    listed = (await client.get("/api/conversations", params={"character_id": "mano"})).json()
    assert [c["id"] for c in listed] == [second, first]


async def test_unknown_character_or_conversation_is_rejected(make_client: ClientFactory):
    client = make_client(FakeLLM())

    res = await client.post("/api/conversations", json={"character_id": "nobody"})
    assert res.status_code == 404
    res = await client.post("/api/conversations/missing/turns", json={"text": "hi"})
    assert res.status_code == 404


async def test_reply_cut_off_inside_a_closing_tag_is_asked_to_write_it_again(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(
        ["<ja>はいっ</ja><zh>好的</z"],
        ["<ja>はいっ</ja><zh>好的</zh>"],
    ))
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "好吗")

    assert events[-1]["message"]["segments"] == [{"ja": "はいっ", "zh": "好的"}]


async def test_the_prompt_tells_her_what_the_producer_is_listening_to(
    make_client: ClientFactory,
):
    llm = FakeLLM(["<ja>その曲、いいですね</ja><zh>那首歌，真好呢</zh>"])
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    await send_turn(
        client, conversation_id, "这首歌怎么样", listening="アルストロメリア · 大崎甘奈",
    )

    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "制作人现在正在听：アルストロメリア · 大崎甘奈。" in told
    assert "不要主动说起" in told


async def test_she_is_not_told_what_is_playing_unless_the_producer_mentions_music(
    make_client: ClientFactory,
):
    llm = FakeLLM(["<ja>こんばんは</ja><zh>晚上好</zh>"])
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    await send_turn(
        client, conversation_id, "晚上好", listening="アルストロメリア · 大崎甘奈",
    )

    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "制作人现在正在听" not in told


async def test_a_song_title_longer_than_the_field_is_cut_not_refused(
    make_client: ClientFactory,
):
    llm = FakeLLM(["<ja>その曲、いいですね</ja><zh>那首歌，真好呢</zh>"])
    client = make_client(llm)
    conversation_id = await start_conversation(client)
    long_title = "あ" * 150

    await send_turn(
        client, conversation_id, "这首歌怎么样", listening=long_title,
    )

    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert f"制作人现在正在听：{'あ' * 120}。" in told
    assert "あ" * 121 not in told
