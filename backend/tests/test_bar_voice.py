"""The bar's voice (GLOSSARY.md「酒吧」): her lines said in her voice as the bar plays them, on
the deployer's key for Fish Audio, answered as mp3; 422 for a character the bar has not or
nothing said, 503 without speech synthesis."""

import httpx

from tests.conftest import ClientFactory
from tests.fakes import DEPLOYER_FISH_KEY, FakeFishAudio, FakeLLM


def faked(fish: FakeFishAudio) -> httpx.MockTransport:
    return httpx.MockTransport(fish.handle)


async def tts(client: httpx.AsyncClient, character_id: str, text: str) -> httpx.Response:
    return await client.post("/api/bar/tts", json={"character_id": character_id, "text": text})


async def test_her_line_is_voiced_on_the_deployers_key(make_client: ClientFactory):
    fish = FakeFishAudio()
    client = make_client(FakeLLM(), fish_transport=faked(fish))

    res = await tts(client, "mano", "こんばんは")

    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.content == FakeFishAudio.audio(fish.requests[0]["text"], "mano-voice")
    assert fish.requests[0]["key"] == DEPLOYER_FISH_KEY


async def test_her_line_is_voiced_a_sentence_at_a_time_without_a_pause(make_client: ClientFactory):
    fish = FakeFishAudio()
    client = make_client(FakeLLM(), fish_transport=faked(fish))

    res = await tts(client, "mano", "こんばんは。今日も来てくれてありがとう。")

    assert res.status_code == 200
    assert [request["text"] for request in fish.requests] == [
        "こんばんは。", "今日も来てくれてありがとう。",
    ]
    assert res.content == b"".join(
        FakeFishAudio.audio(request["text"], "mano-voice") for request in fish.requests
    )


async def test_a_character_the_bar_has_not_is_refused(make_client: ClientFactory):
    client = make_client(FakeLLM())

    res = await tts(client, "nobody", "こんばんは")

    assert res.status_code == 422


async def test_nothing_said_is_refused(make_client: ClientFactory):
    client = make_client(FakeLLM())

    res = await tts(client, "mano", "   ")

    assert res.status_code == 422


async def test_without_speech_synthesis_it_is_refused(make_client: ClientFactory):
    client = make_client(FakeLLM(), voice_configured=False)

    res = await tts(client, "mano", "こんばんは")

    assert res.status_code == 503
