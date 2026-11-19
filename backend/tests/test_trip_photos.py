"""Trip photos (GLOSSARY.md「外景照片」): a day her schedule has her away on a trip, she sends the
producer a photo from there once, unasked, as a reach-out with a photo; with others of her
unit on it, it may be of them all, each with a character prompt of her own. Her unit's free
members go on a trip with her now and then, and their days have them on it. Off with the
user's switch, with image generation's, or with her schedule's."""

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, time

import httpx
import pytest

from eidolon.characters import Character
from eidolon.config import Settings
from eidolon.engine import WANTS_TO_SEND_TRIP_PHOTO
from eidolon.image_generation import generation_request, openai_request
from eidolon.photo_prompt import GROUP_SIZE, group_prompt, read_tag
from eidolon.producer import Birthday
from eidolon.schedules import TRIP_WITH_TWIST, Schedules, Schedule, Slot
from eidolon.trip_photos import _trip_start  # pyright: ignore[reportPrivateUsage]
from tests.conftest import AVATAR, KOGANE_SHEET, MANO_PHOTO, MANO_SHEET, CharacterConfig, ClientFactory
from tests.fakes import FakeClock, FakeImageService, FakeLLM, FakeRandom
from tests.test_delete_account import user_id
from tests.test_photos import IMAGE
from tests.test_reach_outs import backend_of, reach_outs, record
from tests.test_schedules import PLAN
from tests.test_text_turn import send_turn, start_conversation

KOGANE_PHOTO = ("tsukioka kogane", "blonde hair, long hair, blue eyes")
REPLY = ["<ja>はい</ja><zh>好的</zh>"]
TAG = (
    "shot: selfie\nframing: upper body\nplace: kyoto, temple, autumn leaves, outdoors\n"
    "outfit: coat, scarf\naction: peace sign, smile\n{group}"
    "scene: She holds up a maple leaf with the temple gate behind her.\n"
    "words: 清水寺\nwords_on: the stone pillar behind her\nnote: 在清水寺拍的"
)
SENDS = [f"<photo>{TAG.format(group='')}</photo><ja>見て、紅葉です</ja><zh>你看，是红叶</zh>"]
TOGETHER = "group: yes\nothers: smile, waving\n"
SENDS_ALL = [
    f"<photo>{TAG.format(group=TOGETHER)}</photo><ja>みんなで撮りました</ja><zh>大家一起拍的</zh>"
]
TODAY = datetime.now(UTC).date()


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TODAY, time(hour, minute), UTC)


@pytest.fixture
def characters(tmp_path) -> CharacterConfig:
    """Mano and Kogane, both with their photo tags."""
    return (
        CharacterConfig(tmp_path / "characters")
        .add("mano", "櫻木真乃", MANO_SHEET, avatar=AVATAR, voice_id="mano-voice", photo=MANO_PHOTO)
        .add("kogane", "月岡恋鐘", KOGANE_SHEET, voice_id="kogane-voice", photo=KOGANE_PHOTO)
    )


async def talked(
    make_client: ClientFactory, settings: Settings, *replies, image: FakeImageService | None = None,
    clock: FakeClock | None = None, numbers: tuple[int, ...] = (0,),
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    llm = FakeLLM(REPLY, *replies)
    image = image or FakeImageService()
    client = make_client(
        llm, clock=clock or FakeClock(at(10)), random=FakeRandom(*numbers),
        settings=replace(settings, schedules=True, image=IMAGE),
        image_transport=image.transport(),
    )
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "今天也加油")
    return client, llm, conversation


async def on_a_trip(client: httpx.AsyncClient, settings: Settings, trip_with: tuple[str, ...] = ()) -> None:
    schedules: Schedules = backend_of(client).daily
    me = user_id(settings.database_path, "producer")
    slot = Slot(time(11), time(18), "拍外景", "京都", "work", ())
    await schedules.keep(me, "mano", TODAY, [slot], [], trip="京都", trip_with=trip_with)


async def on_a_late_trip(client: httpx.AsyncClient, settings: Settings) -> None:
    schedules: Schedules = backend_of(client).daily
    me = user_id(settings.database_path, "producer")
    slot = Slot(time(16), time(18), "横滨外景拍摄", "横滨", "work", ())
    await schedules.keep(me, "mano", TODAY, [slot], [], trip="横滨")


async def on_a_trip_after_bedtime(client: httpx.AsyncClient, settings: Settings) -> None:
    schedules: Schedules = backend_of(client).daily
    me = user_id(settings.database_path, "producer")
    slot = Slot(time(22, 30), time(23, 30), "横滨外景拍摄", "横滨", "work", ())
    await schedules.keep(
        me, "mano", TODAY, [slot], [], wake=time(7), sleep=time(22), trip="横滨"
    )


def test_the_trip_begins_with_the_shoot_not_the_meeting_or_the_travel():
    schedule = Schedule(
        TODAY,
        (
            Slot(time(9), time(9, 30), "关于横滨外景的短会", "事务所", "work"),
            Slot(time(9, 30), time(13), "前往横滨外景地（移动·飞行）", "东京→横滨", "work"),
            Slot(time(16), time(18), "横滨外景拍摄", "横滨", "work"),
        ),
        (), trip="横滨",
    )
    assert _trip_start(schedule) == time(16)


def test_a_trip_whose_only_slot_is_the_travel_begins_then():
    schedule = Schedule(
        TODAY,
        (Slot(time(14, 30), time(20, 30), "前往大阪拍摄外景", "大阪", "work"),),
        (), trip="大阪",
    )
    assert _trip_start(schedule) == time(14, 30)


def test_a_trip_with_no_slot_saying_when_has_no_photo_time():
    schedule = Schedule(TODAY, (Slot(time(10), time(12), "练习", "事务所", "work"),), (), trip="横滨")
    assert _trip_start(schedule) is None


async def test_a_trip_later_in_the_day_sends_nothing_before_it_starts(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    clock = FakeClock(at(12))
    client, llm, conversation = await talked(
        make_client, settings, SENDS, image=image, clock=clock, numbers=(0,)
    )
    await on_a_late_trip(client, settings)
    trips = backend_of(client).trip_photos

    await trips.run_due()
    before = len(llm.prompts)
    clock.now = at(16, 5)
    await trips.run_due()
    [sent] = reach_outs(await record(client, conversation))

    assert before == 1 and len(llm.prompts) == 2
    assert sent["photo"]["status"] == "ready"


async def test_a_trip_after_her_bedtime_sends_no_photo(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(22, 31))
    client, llm, _ = await talked(
        make_client, settings, SENDS, clock=clock, numbers=(0,)
    )
    await on_a_trip_after_bedtime(client, settings)

    await backend_of(client).trip_photos.run_due()

    assert len(llm.prompts) == 1


async def test_a_due_time_planned_before_the_trip_moves_to_its_start(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(12, 5))
    client, llm, conversation = await talked(
        make_client, settings, SENDS, clock=clock, numbers=(0,)
    )
    await on_a_late_trip(client, settings)
    me = user_id(settings.database_path, "producer")
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "INSERT INTO trip_photo_due (user_id, character_id, day, due_at)"
            " VALUES (?, ?, ?, ?)",
            (me, "mano", TODAY.isoformat(), at(12).isoformat()),
        )
    trips = backend_of(client).trip_photos

    await trips.run_due()
    with sqlite3.connect(settings.database_path) as db:
        [due] = db.execute(
            "SELECT due_at FROM trip_photo_due WHERE user_id = ? AND character_id = 'mano'",
            (me,),
        ).fetchone()

    assert datetime.fromisoformat(due) >= at(16)
    assert len(llm.prompts) == 1

    clock.now = at(16, 5)
    await trips.run_due()
    [sent] = reach_outs(await record(client, conversation))

    assert len(llm.prompts) == 2 and sent["photo"]["status"] == "ready"


async def test_on_a_trip_she_sends_one_photo_from_there_once_its_time_comes(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    clock = FakeClock(at(10))
    # Her time: 11:00 and 5 minutes on (1).
    client, llm, conversation = await talked(
        make_client, settings, SENDS, image=image, clock=clock, numbers=(1,)
    )
    await on_a_trip(client, settings)
    trips = backend_of(client).trip_photos

    await trips.run_due()
    too_early = len(llm.prompts)
    clock.now = at(11, 5)
    await trips.run_due()
    await trips.run_due()
    [sent] = reach_outs(await record(client, conversation))
    picture = await client.get(f"/api/photos/{sent['photo']['id']}/image")

    assert too_early == 1 and len(llm.prompts) == 2  # once
    assert llm.prompts[-1][-1].content.endswith(WANTS_TO_SEND_TRIP_PHOTO)
    assert "你今天在京都出外景" in str(llm.prompts[-1])
    assert sent["segments"] == [{"ja": "見て、紅葉です", "zh": "你看，是红叶"}]
    assert sent["photo"]["status"] == "ready" and picture.status_code == 200
    [request] = image.requests
    body = request["body"]["input"]
    assert body.startswith("1girl, solo, sakuragi mano") and "kyoto" in body
    assert 'the text "清水寺" is written on the stone pillar behind her, She holds up' in body
    assert request["body"]["parameters"]["v4_prompt"]["caption"]["char_captions"] == []


async def test_she_knows_she_sent_it_and_the_list_shows_a_photo(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await talked(make_client, settings, SENDS, REPLY)
    sent = await client.post("/api/trip-photos/send-now", json={"character_id": "mano"})
    listed = (await client.get("/api/conversations/latest")).json()

    await send_turn(client, conversation, "好漂亮")

    assert sent.status_code == 201
    [mano] = [c for c in listed if c["character_id"] == "mano"]
    assert mano["last_message"]["photo"] is True and mano["last_message"]["reach_out"] is True
    assert "shot: selfie" in str(llm.prompts[-2])  # her trip photo's prompt, of the format


async def test_with_her_unit_on_the_trip_it_may_be_of_them_all(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client, llm, _ = await talked(make_client, settings, SENDS_ALL, image=image, clock=FakeClock(at(17)))
    await on_a_trip(client, settings, trip_with=("kogane",))

    await backend_of(client).trip_photos.run_due()

    assert "和你一起来的有月岡恋鐘" in str(llm.prompts[-1])
    [request] = image.requests
    parameters = request["body"]["parameters"]
    base = request["body"]["input"]
    captions = parameters["v4_prompt"]["caption"]["char_captions"]
    assert base.startswith("2girls, idolmaster shiny colors, selfie")
    assert "upper body, side-by-side, kyoto" in base and "exactly 2 girls in the whole picture" in base
    assert "outlined text, sticker" in parameters["negative_prompt"]  # the words are drawn on
    assert [c["char_caption"] for c in captions] == [
        "girl, sakuragi mano, brown hair, medium hair, brown eyes, coat, scarf, peace sign, smile",
        "girl, tsukioka kogane, blonde hair, long hair, blue eyes, smile, waving",
    ]
    assert len(parameters["v4_negative_prompt"]["caption"]["char_captions"]) == 2
    assert (parameters["width"], parameters["height"]) == GROUP_SIZE


async def test_sent_now_with_others_named_it_may_be_of_them_all(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client, llm, _ = await talked(make_client, settings, SENDS_ALL, image=image)

    sent = await client.post("/api/trip-photos/send-now", json={
        "character_id": "mano", "place": "京都", "with": ["kogane"], "group": True,
    })
    nobody = await client.post("/api/trip-photos/send-now", json={
        "character_id": "mano", "with": ["no-such"],
    })

    assert sent.status_code == 201, sent.text
    assert "和你一起来的有月岡恋鐘（kogane），这次拍一张和她们的合照" in str(llm.prompts[-1])
    assert image.requests[0]["body"]["input"].startswith("2girls")
    assert nobody.status_code == 404


async def test_none_with_image_generation_or_trip_photos_switched_off(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await talked(make_client, settings, clock=FakeClock(at(17)))
    await on_a_trip(client, settings)
    me = user_id(settings.database_path, "producer")
    switches = backend_of(client).features

    await switches.switch(me, "images", False)
    await backend_of(client).trip_photos.run_due()
    await switches.switch(me, "images", True)
    await switches.switch(me, "trip_photos", False)
    await backend_of(client).trip_photos.run_due()

    assert len(llm.prompts) == 1
    assert reach_outs(await record(client, conversation)) == []


async def test_her_unit_mates_days_do_not_get_her_trip_content(
    make_client: ClientFactory, settings: Settings
):
    batch = '{"mano": ' + PLAN + ', "kogane": ' + PLAN + '}'
    llm = FakeLLM(REPLY, [batch], REPLY)
    clock = FakeClock(datetime.combine(TODAY, time(6), UTC))
    client = make_client(
        llm, clock=clock, random=FakeRandom(*((1, 1, 1, 1, 0, 0) * 8)),
        settings=replace(settings, schedules=True),
    )
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "早")
    backend = backend_of(client)
    me = user_id(settings.database_path, "producer")
    slot = Slot(time(9), time(18), "拍外景", "京都", "work", ())
    await backend.daily.keep(me, "kogane", TODAY, [slot], [], trip="京都", trip_with=("mano",))

    await backend.schedule_writer.run_due()
    mine = await backend.daily.of(me, "mano", TODAY)

    assert mine is not None and (mine.trip, mine.trip_with) == (None, ())
    assert TRIP_WITH_TWIST.format(names="月岡恋鐘", place="京都") not in str(llm.prompts[-1])


def test_a_group_prompt_takes_each_one_her_own_and_openai_gets_them_after():
    mano = Character("mano", "櫻木真乃", "", "v", "", None, "#ffbad6", Birthday(4, 24), 16,
                     photo_tag="sakuragi mano", photo_appearance="brown hair")
    kogane = Character("kogane", "月岡恋鐘", "", "v", "", None, "#f84cad", Birthday(4, 24), 16,
                       photo_tag="tsukioka kogane", photo_appearance="blonde hair")
    tag = read_tag("shot: other\nplace: beach, 2girls\noutfit: swimsuit\naction: smile")

    prompt = group_prompt([mano, kogane], tag)
    alone = group_prompt([mano], tag)

    assert "2girls, 2girls" not in prompt.positive and "beach" in prompt.positive
    assert prompt.characters[1] == "girl, tsukioka kogane, blonde hair, swimsuit, smile"
    assert alone.characters == () and alone.positive.startswith("1girl, solo")
    assert openai_request("m", prompt)["prompt"].endswith("girl, tsukioka kogane, blonde hair, swimsuit, smile")
    assert len(generation_request("m", prompt, 1)["parameters"]["v4_prompt"]["caption"]["char_captions"]) == 2
    assert tag.stripped == ("people",)  # how many are in it is not hers to write


def test_a_photo_of_them_together_is_never_so_far_off_their_faces_blur():
    from eidolon.photo_prompt import group_framing

    assert group_framing("full body", 2) == "cowboy shot"
    assert group_framing("cowboy shot", 2) == "cowboy shot"
    assert group_framing("full body", 3) == "upper body"
    assert group_framing("cowboy shot", 4) == "upper body"
    assert group_framing("portrait", 3) == "portrait"


def test_each_of_the_others_wears_and_does_what_she_wrote_for_her():
    mano = Character("mano", "櫻木真乃", "", "v", "", None, "#ffbad6", Birthday(4, 24), 16,
                     photo_tag="sakuragi mano", photo_appearance="brown hair")
    hiori = Character("hiori", "風野灯織", "", "v", "", None, "#000000", Birthday(4, 24), 16,
                      photo_tag="kazano hiori", photo_appearance="black hair")
    meguru = Character("meguru", "八宮めぐる", "", "v", "", None, "#000000", Birthday(4, 24), 16,
                       photo_tag="hachimiya meguru", photo_appearance="blonde hair")
    tag = read_tag(
        "shot: other\nplace: kyoto\noutfit: cardigan\naction: smile\n"
        "others: hiori: navy coat, holding camera / meguru: yellow hoodie, waving"
    )

    prompt = group_prompt([mano, hiori, meguru], tag)
    unnamed = group_prompt([mano, hiori], read_tag("shot: other\nothers: scarf, smile"))

    assert prompt.characters[1] == "girl, kazano hiori, black hair, navy coat, holding camera"
    assert prompt.characters[2] == "girl, hachimiya meguru, blonde hair, yellow hoodie, waving"
    assert unnamed.characters[1] == "girl, kazano hiori, black hair, scarf, smile"


def test_the_producer_in_a_photo_is_the_p_headed_producer_in_a_suit_or_a_skirt_suit():
    mano = Character("mano", "櫻木真乃", "", "v", "", None, "#ffbad6", Birthday(4, 24), 16,
                     photo_tag="sakuragi mano", photo_appearance="brown hair")
    tag = read_tag("shot: selfie\nplace: cinema\noutfit: coat\naction: smile\nyou: yes, peace sign")
    no = read_tag("shot: selfie\nyou: no")

    his = group_prompt([mano], tag)
    hers = group_prompt([mano], tag, producer="female")

    assert no.you is None and read_tag("shot: other").you is None
    assert his.positive.startswith("1girl, 1other, ")
    assert "exactly 1 girl and the p-head producer" in his.positive
    assert his.characters[1] == (
        "1.3::p-head producer::, faceless, black suit, white shirt, red necktie, peace sign"
    )
    assert hers.positive.startswith("1girl, 1other, ")
    p_head, genderswap = hers.characters[1].index("1.5::p-head"), hers.characters[1].index("1.2::genderswap")
    assert p_head < genderswap and "navy blue dress" in hers.characters[1]
    assert his.size == GROUP_SIZE
    assert read_tag("shot: other\nyou: yes").you == ("standing",)


def test_the_deployer_may_write_the_producers_look_their_own_way(monkeypatch: pytest.MonkeyPatch):
    mano = Character("mano", "櫻木真乃", "", "v", "", None, "#ffbad6", Birthday(4, 24), 16,
                     photo_tag="sakuragi mano", photo_appearance="brown hair")
    monkeypatch.setenv("EIDOLON_PRODUCER_LOOK_FEMALE", " 2::p-head producer::,  skirt suit , ")

    hers = group_prompt([mano], read_tag("shot: selfie\nyou: yes, waving"), producer="female")

    assert hers.characters[1] == "2::p-head producer::, skirt suit, waving"


async def test_a_trip_photo_never_has_the_producer_in_it_whatever_she_writes(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    written = TAG.format(group="you: yes, waving\n")
    sends = [f"<photo>{written}</photo><ja>見て</ja><zh>你看</zh>"]
    client, llm, _ = await talked(make_client, settings, sends, image=image)

    await client.post("/api/trip-photos/send-now", json={"character_id": "mano"})

    assert "you: " not in str(llm.prompts[-1])
    [request] = image.requests
    assert "p-head" not in json.dumps(request["body"])
    assert request["body"]["input"].startswith("1girl, solo, sakuragi mano")
