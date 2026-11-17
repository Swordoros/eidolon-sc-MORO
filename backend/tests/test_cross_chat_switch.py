"""The user's switch for letting group and private chat contexts cross."""

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM


async def test_the_cross_chat_switch_is_off_by_default_and_kept(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), settings=settings)

    res = await client.get("/api/account")
    assert res.status_code == 200
    assert res.json()["cross_chat"] is False

    assert (await client.put("/api/account/cross-chat", json={"on": True})).status_code == 204
    assert (await client.get("/api/account")).json()["cross_chat"] is True

    assert (await client.put("/api/account/cross-chat", json={"on": False})).status_code == 204
    assert (await client.get("/api/account")).json()["cross_chat"] is False
