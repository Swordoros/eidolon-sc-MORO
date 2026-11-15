"""The development switch (GLOSSARY.md「开发」): off by default, kept on the server; while on,
the API says the last conversation prompt the chat model read, and none otherwise."""

import httpx

from eidolon.config import Settings
from eidolon.engine import _dump_prompt
from eidolon.llm import ChatMessage
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]


async def dev_on(client: httpx.AsyncClient) -> bool:
    res = await client.get("/api/account")
    assert res.status_code == 200, res.text
    return res.json()["dev"]


async def last_prompt(client: httpx.AsyncClient, character_id: str = "mano") -> str | None:
    res = await client.get(f"/api/dev/last-prompt?character_id={character_id}")
    assert res.status_code == 200, res.text
    return res.json()["prompt"]


def test_dump_prompt_marks_each_message_with_its_role():
    dumped = _dump_prompt([ChatMessage("system", "规则"), ChatMessage("user", "你好")])
    assert dumped == "--- system ---\n规则\n\n--- user ---\n你好"


async def test_dev_is_off_by_default_and_can_be_switched(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(REPLY), settings=settings)

    assert await dev_on(client) is False
    assert await last_prompt(client) is None

    assert (await client.put("/api/account/dev", json={"on": True})).status_code == 204
    assert await dev_on(client) is True


async def test_a_turn_leaves_its_prompt_while_dev_is_on(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(REPLY), settings=settings)
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "你好")

    assert await last_prompt(client) is None  # the switch is off: hidden

    await client.put("/api/account/dev", json={"on": True})
    prompt = await last_prompt(client)

    assert prompt is not None
    assert "--- system ---" in prompt and "你好" in prompt
