"""The user's switch for letting her characters fall in love with them."""

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM


async def test_the_plove_switch_is_off_by_default_and_kept(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), settings=settings)

    res = await client.get("/api/account")
    assert res.status_code == 200
    assert res.json()["allow_plove"] is False

    assert (await client.put("/api/account/plove", json={"on": True})).status_code == 204
    assert (await client.get("/api/account")).json()["allow_plove"] is True

    assert (await client.put("/api/account/plove", json={"on": False})).status_code == 204
    assert (await client.get("/api/account")).json()["allow_plove"] is False
