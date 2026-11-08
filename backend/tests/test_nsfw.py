"""The explicit scenes (NSFW): a turn that asks to cross the friend line into a physical
relation opens a scene in the intimate tier, for an adult character only, and only with the
user's own switch on; anywhere else it is out of line as ever. The judge asks a `beyond`
question for it, apart from the out-of-line one."""

from dataclasses import replace

import pytest

from eidolon.affinity import NSFW_OFF_LINE, Judged, nsfw_line, nsfw_open
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeLLM, FakeServiceClients, level
from tests.test_affinity import keep, scenes_off
from tests.test_affinity_turns import value
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the judge model and affinity on, so that turns are judged."""
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)


async def nsfw_on(client: ClientFactory) -> None:
    """Turn the user's own switch for the adult conversation on."""
    res = await client.put("/api/account/nsfw", json={"conversation": True})
    assert res.status_code == 204


def test_nsfw_is_open_only_for_adults_in_intimate_out_of_a_cooldown():
    assert nsfw_open("intimate", False, 18) is True
    assert nsfw_open("intimate", False, 17) is False
    assert nsfw_open("intimate", False, None) is False
    assert nsfw_open("close", False, 22) is False
    assert nsfw_open("intimate", True, 22) is False


def test_a_beyond_turn_writes_a_scene_an_off_turn_refuses_and_an_ordinary_one_is_left():
    beyond = Judged(2, beyond=True)
    assert nsfw_line(True, beyond) is not None  # the scene's prompts
    assert nsfw_line(False, beyond) == [NSFW_OFF_LINE]
    assert nsfw_line(True, Judged(2)) is None  # an ordinary turn in the tier


async def test_an_adult_beyond_turn_is_not_out_of_line_when_nsfw_is_on(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    clients = FakeServiceClients()
    clients.judge.judgings = [level(2, out_of_line=True, beyond=True)]
    client = make_client(FakeLLM(REPLY), clients=clients, settings=settings, clock=clock)
    conversation = await start_conversation(client, "kogane")  # 19, an adult
    keep(settings.database_path, "producer", 90, clock.now, character="kogane")
    scenes_off(settings.database_path)
    await nsfw_on(client)

    await send_turn(client, conversation, "今晚留下来")

    assert value(settings.database_path, character="kogane") == 90  # no −10 for out of line


async def test_the_same_turn_is_out_of_line_when_nsfw_is_off(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    clients = FakeServiceClients()
    clients.judge.judgings = [level(2, out_of_line=True, beyond=True)]
    client = make_client(FakeLLM(REPLY), clients=clients, settings=settings, clock=clock)
    conversation = await start_conversation(client, "kogane")
    keep(settings.database_path, "producer", 90, clock.now, character="kogane")
    scenes_off(settings.database_path)

    await send_turn(client, conversation, "今晚留下来")

    assert value(settings.database_path, character="kogane") == 80  # −10 for out of line


async def test_a_minor_beyond_turn_is_out_of_line_even_when_nsfw_is_on(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    clients = FakeServiceClients()
    clients.judge.judgings = [level(2, out_of_line=True, beyond=True)]
    client = make_client(FakeLLM(REPLY), clients=clients, settings=settings, clock=clock)
    conversation = await start_conversation(client, "mano")  # 16, under age
    keep(settings.database_path, "producer", 90, clock.now, character="mano")
    scenes_off(settings.database_path)
    await nsfw_on(client)

    await send_turn(client, conversation, "今晚留下来")

    assert value(settings.database_path, character="mano") == 80  # −10 for out of line

