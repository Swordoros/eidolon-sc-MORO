"""Keepsakes (GLOSSARY.md「纪念品」): what she gives the producer, and the cabinet. Turned on in
`.env`, switched off by a user for themselves; put down as due for her scene, an anniversary, a
birthday, a gift she liked, a promise kept, a trip, a holiday's letter; given at a tick as an
event of her current conversation, or with a letter; chosen on the chat model with what she
says giving it; its picture drawn square, the thing alone, where there is image generation,
and drawn later otherwise, all the missing at one go."""

import io
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
from PIL import Image

from eidolon.config import GptImageSettings, Settings
from eidolon.engine import EmptyReply, WANTS_TO_GIVE, read_keepsake
from eidolon.keepsakes import PER_DAY
from eidolon.photo_prompt import keepsake_prompt
from eidolon.promises import Promise
from eidolon.schedules import Bath, TRIP_TWIST, Slot
from tests.conftest import ClientFactory
from tests.fakes import FakeClock, FakeImageService, FakeLLM, FakeRandom, context_of
from tests.test_delete_account import user_id
from tests.test_photos import IMAGE
from tests.test_reach_outs import backend_of, last_contact, record
from tests.test_schedules import MANO_PLAN, schedule
from tests.test_schedules import talked as schedule_talked
from tests.test_schedules import today_at as schedule_today_at
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
GIVES = [
    "<keepsake>\nname: 手作的四叶草书签\nlook: 压干的四叶草封在透明书签里，系着粉色丝带。\n"
    "emoji: 🍀\ntags: bookmark, four-leaf clover, pressed flower, pink ribbon, nude\n</keepsake>"
    "<ja>[softly] これ、プロデューサーさんに</ja><zh>这个，送给制作人</zh>"
]
TODAY = datetime.now(UTC).date()
GPT = GptImageSettings("gpt-key", "https://api.openai.test/v1")


def at(hour: int, minute: int = 0, day: date = TODAY) -> datetime:
    return datetime.combine(day, time(hour, minute), UTC)


def test_what_she_gives_is_read_from_its_tag_and_what_she_says_after_it():
    draft = read_keepsake(GIVES[0])

    assert draft.name == "手作的四叶草书签" and draft.emoji == "🍀"
    assert draft.look.startswith("压干的四叶草")
    assert [(s.ja, s.zh) for s in draft.said] == [("これ、プロデューサーさんに", "这个，送给制作人")]
    with pytest.raises(EmptyReply):
        read_keepsake("<ja>あの</ja><zh>那个</zh>")  # names nothing
    assert read_keepsake("<keepsake>\nname: 布丁\n</keepsake><ja>はい</ja><zh>给</zh>").emoji == "🎁"


def test_its_picture_is_the_thing_alone_square_and_all_ages():
    prompt, stripped = keepsake_prompt("bookmark, four-leaf clover, nude, 1girl, pink ribbon", "flat color")

    assert prompt.size == (1024, 1024)
    assert prompt.positive.startswith("background dataset, no humans, still life, object focus, flat color, bookmark")
    assert "nude" not in prompt.positive and "1girl" not in prompt.positive
    assert stripped == ("nudity",)
    assert "1girl" in prompt.negative and "nsfw" in prompt.negative
    assert "no text" in prompt.positive and ", text," in prompt.negative


def test_a_keepsake_picture_never_keeps_a_hand_or_palm_in_its_prompt():
    prompt, stripped = keepsake_prompt(
        "keychain, blue, boat, metal, on palm || "
        "A small blue sailing boat keychain sits on a palm."
    )

    assert "palm" not in prompt.positive.lower()
    assert "hand" not in prompt.positive.lower()
    assert "on a plain surface" in prompt.positive
    assert "people" in stripped
    assert "palm" in prompt.negative and "hand" in prompt.negative


def test_what_it_is_leads_and_her_sentence_describing_it_follows_the_tags():
    draft = read_keepsake(
        "<keepsake>\nname: 和纸鸽子\nwhat: bird figurine\ntags: washi paper, light blue, round\n"
        "scene: A small round pigeon figurine, made of light blue washi paper, sits on a desk.\n"
        "</keepsake><ja>はい</ja><zh>给</zh>"
    )
    prompt, _ = keepsake_prompt(draft.tags)

    assert prompt.positive.startswith(
        "background dataset, no humans, still life, object focus, bird figurine, washi paper,"
    )
    assert "round, A small round pigeon figurine made of light blue washi paper sits on a desk, " in (
        prompt.positive
    )


def test_words_written_on_it_are_drawn_in_quotes_where_it_says_and_text_is_let_in():
    draft = read_keepsake(
        "<keepsake>\nname: 生日卡\ntags: birthday card, cake\nwords: 「おめでとう」{1.5::x::}\n"
        "words_on: the card, inside\n</keepsake><ja>はい</ja><zh>给</zh>"
    )
    prompt, _ = keepsake_prompt(draft.tags)

    assert draft.tags == 'birthday card, cake, "おめでとう 1.5 x" @ the card inside'
    assert prompt.positive.endswith(
        'birthday card, cake, the text "おめでとう 1.5 x" is written on the card inside,'
        " high complexity, rating:general, very aesthetic, masterpiece"
    )
    assert ", text," not in prompt.negative and "watermark" in prompt.negative
    plain, _ = keepsake_prompt("cake")
    assert plain.positive.endswith("cake, high complexity, rating:general, very aesthetic, masterpiece, no text")


def test_numbers_alone_are_not_written_on_it():
    draft = read_keepsake(
        "<keepsake>\nname: 四叶草书签\ntags: paper bookmark, light green\n"
        "words: 1 2 3 4\nwords_on: one leaf of the clover\n</keepsake><ja>はい</ja><zh>给</zh>"
    )
    prompt, _ = keepsake_prompt(draft.tags)

    assert "1 2 3 4" not in draft.tags
    assert "the text" not in prompt.positive
    assert "no text" in prompt.positive


def test_numbers_stored_before_are_left_out_when_it_is_drawn_again():
    prompt, _ = keepsake_prompt(
        'paper bookmark, light green, "1 2 3 4" @ one leaf of the clover || '
        "A paper bookmark sits on a desk."
    )

    assert "the text" not in prompt.positive
    assert "no text" in prompt.positive


async def talked(
    make_client: ClientFactory, settings: Settings, *replies, clock: FakeClock | None = None,
    image: FakeImageService | None = None, gpt_service: FakeImageService | None = None,
    random: FakeRandom | None = None, **switches,
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    llm = FakeLLM(REPLY, *replies)
    client = make_client(
        llm, clock=clock or FakeClock(at(12)),
        settings=replace(
            settings, keepsakes=True, image=IMAGE if image else None, **switches
        ),
        image_transport=image.transport() if image else None,
        gpt_image_transport=gpt_service.transport() if gpt_service else None,
        **({"random": random} if random else {}),
    )
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "今天也辛苦了")
    return client, llm, conversation


async def cabinet(client: httpx.AsyncClient) -> dict:
    res = await client.get("/api/keepsakes")
    assert res.status_code == 200, res.text
    return res.json()


async def test_given_now_it_comes_in_the_conversation_and_the_cabinet_and_she_knows_it(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await talked(make_client, settings, GIVES, REPLY)

    res = await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    shown = await cabinet(client)
    unseen = (await client.get("/api/keepsakes/unseen")).json()
    events = [e for e in await record(client, conversation) if e.get("type") == "event"]
    await send_turn(client, conversation, "谢谢你的书签")

    assert res.status_code == 201
    assert llm.prompts[1][-1].content == WANTS_TO_GIVE
    assert "制作人前几天送了你一块布丁" in context_of(llm.prompts[1])
    [given] = shown["keepsakes"]
    assert (given["name"], given["emoji"], given["has_image"]) == ("手作的四叶草书签", "🍀", False)
    assert given["said"] == [{"ja": "これ、プロデューサーさんに", "zh": "这个，送给制作人"}]
    assert shown["missing"] == 1 and shown["can_draw"] is False
    assert unseen == {"unseen": 1}
    assert [(e["kind"], e["keepsake_id"]) for e in events] == [("keepsake", given["id"])]
    history = next(
        m.content for m in llm.prompts[-1] if m.role == "system" and "历史对话" in m.content
    )
    assert "旁白：你送给了制作人「手作的四叶草书签」。" in history
    assert (await client.get(f"/api/keepsakes/{given['id']}/image")).status_code == 404
    await client.post("/api/keepsakes/seen")
    assert (await client.get("/api/keepsakes/unseen")).json() == {"unseen": 0}


async def test_with_image_generation_its_picture_is_drawn_square(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client, _, _ = await talked(make_client, settings, GIVES, image=image)

    await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    [given] = (await cabinet(client))["keepsakes"]
    picture = await client.get(f"/api/keepsakes/{given['id']}/image")

    assert given["has_image"] is True
    assert image.requests[0]["body"]["input"].startswith("background dataset, no humans, still life")
    with Image.open(io.BytesIO(picture.content)) as webp:
        assert (webp.format, webp.size) == ("WEBP", (1024, 1024))


async def test_with_gpt_image_its_picture_is_drawn_by_gpt_image(
    make_client: ClientFactory, settings: Settings
):
    gpt = FakeImageService()
    novelai = FakeImageService()
    client, _, _ = await talked(
        make_client, settings, GIVES, image=novelai, gpt_service=gpt, gpt_image=GPT
    )

    await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    [given] = (await cabinet(client))["keepsakes"]
    picture = await client.get(f"/api/keepsakes/{given['id']}/image")

    [request] = gpt.requests
    prompt = request["body"]["prompt"]
    assert novelai.requests == []
    assert request["url"].endswith("/images/generations")
    assert request["body"]["size"] == "1024x1024"
    assert "Subject: a close-up still life of bookmark, four-leaf clover" in prompt
    assert "nude" not in prompt and "1girl" not in prompt
    assert given["has_image"] is True
    with Image.open(io.BytesIO(picture.content)) as webp:
        assert (webp.format, webp.size) == ("WEBP", (1024, 1024))


async def test_the_pictures_missing_are_drawn_at_one_go_once_there_is_image_generation(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client, settings, GIVES)
    await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    backend = backend_of(client)
    image = FakeImageService()
    drawn = make_client(
        FakeLLM(), settings=replace(settings, keepsakes=True, image=IMAGE),
        image_transport=image.transport(), clock=FakeClock(at(13)),  # after she gave it
    )
    before = (await drawn.get("/api/keepsakes")).json()

    started = (await drawn.post("/api/keepsakes/fill-images")).json()
    task = backend_of(drawn).keepsakes._filling[user_id(settings.database_path, "producer")]  # pyright: ignore[reportPrivateUsage]
    await task
    after = (await drawn.get("/api/keepsakes")).json()

    assert backend is not None
    assert (before["missing"], before["can_draw"]) == (1, True)
    assert started["started"] is True
    assert after["missing"] == 0 and after["keepsakes"][0]["has_image"] is True


async def test_her_scene_played_through_has_her_give_one_soon_after(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(12))
    client, llm, _ = await talked(make_client, settings, GIVES, clock=clock)
    keepsakes = backend_of(client).keepsakes
    me = user_id(settings.database_path, "producer")

    await keepsakes.scene_played(me, "mano", "close", clock.now)
    await keepsakes.run_due()
    soon = len((await cabinet(client))["keepsakes"])
    clock.advance(minutes=11)
    await keepsakes.run_due()

    assert soon == 0
    assert len((await cabinet(client))["keepsakes"]) == 1
    assert "第一次走到了「亲近」" in context_of(llm.prompts[-1])


async def test_a_liked_gift_now_and_then_has_her_give_one_back_the_next_day(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(12))
    client, _, _ = await talked(
        make_client, settings, GIVES, clock=clock, random=FakeRandom(0, 30, 1),
    )
    keepsakes = backend_of(client).keepsakes
    me = user_id(settings.database_path, "producer")

    await keepsakes.gift_given(me, "mano", "布丁", True, None, clock.now)  # one in three: this one
    await keepsakes.gift_given(me, "mano", "布丁", True, None, clock.now)  # not this
    await keepsakes.gift_given(me, "mano", "布丁", False, None, clock.now)  # not liked
    await keepsakes.run_due()
    today = len((await cabinet(client))["keepsakes"])
    clock.now = at(9, 31, TODAY + timedelta(days=1))
    await keepsakes.run_due()

    assert today == 0
    assert len((await cabinet(client))["keepsakes"]) == 1


async def test_her_birthday_keepsake_comes_with_her_letter_and_not_in_the_conversation(
    make_client: ClientFactory, settings: Settings
):
    letter = [
        '{"salutation": "プロデューサーさんへ",'
        ' "paragraphs": [{"ja": "お誕生日おめでとう", "zh": "生日快乐"}]}'
    ]
    client, _, conversation = await talked(make_client, settings, letter, GIVES, letters=True)
    keepsakes = backend_of(client).keepsakes

    await client.post(
        "/api/letters/write-now", json={"character_id": "mano", "occasion": "producer_birthday"}
    )
    await keepsakes.run_due()
    [written] = (await client.get("/api/letters")).json()["letters"]
    events = [e for e in await record(client, conversation) if e.get("type") == "event"]

    assert written["keepsake"]["name"] == "手作的四叶草书签"
    assert written["keepsake"]["occasion"] == "birthday"
    assert events == []
    assert len((await cabinet(client))["keepsakes"]) == 1


async def test_one_at_a_time_and_a_character_waits_three_days(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(12))
    client, _, _ = await talked(
        make_client, settings, *[GIVES] * (PER_DAY + 1), clock=clock,
        random=FakeRandom(*[0] * 2 * (PER_DAY + 1)),  # one in four: each; 30 minutes after
    )
    keepsakes = backend_of(client).keepsakes
    me = user_id(settings.database_path, "producer")

    for n in range(PER_DAY + 1):
        await keepsakes.promise_settled(
            Promise(n + 1, me, "mano", f"约定{n}", TODAY), "kept", clock.now - timedelta(hours=3)
        )
    await keepsakes.run_due()
    await keepsakes.run_due()

    assert len((await cabinet(client))["keepsakes"]) == 1

    clock.advance(days=3)
    await keepsakes.run_due()

    assert len((await cabinet(client))["keepsakes"]) == 2


async def test_the_day_after_a_trip_she_brings_something_from_there(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(10))
    client, llm, conversation = await talked(
        make_client, settings, GIVES, clock=clock, schedules=True,
    )
    last_contact(settings.database_path, conversation, at(9))
    me = user_id(settings.database_path, "producer")
    await backend_of(client).daily.keep(
        me, "mano", TODAY - timedelta(days=1), [Slot(time(10), time(12), "外景", "大阪", "work")],
        [], trip="大阪",
    )

    await backend_of(client).keepsakes.run_due()

    assert len((await cabinet(client))["keepsakes"]) == 1
    assert "你刚从大阪回来" in context_of(llm.prompts[-1])


async def test_a_trip_gives_at_the_end_of_the_day_and_not_while_she_is_asleep_or_bathing(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(23, 0))
    client, llm, conversation = await talked(
        make_client, settings, GIVES, clock=clock, schedules=True,
    )
    last_contact(settings.database_path, conversation, at(9))
    me = user_id(settings.database_path, "producer")
    keepsakes = backend_of(client).keepsakes
    await backend_of(client).daily.keep(
        me, "mano", TODAY,
        [Slot(time(10), time(16), "外景", "大阪", "work")],
        [], wake=time(7), sleep=time(23, 30),
        bath=Bath(time(22, 50), 30), trip="大阪",
    )

    await keepsakes.run_due()
    assert len((await cabinet(client))["keepsakes"]) == 0

    clock.advance(minutes=30)  # bath over, asleep
    await keepsakes.run_due()
    assert len((await cabinet(client))["keepsakes"]) == 0

    clock.now = at(9, 1, TODAY + timedelta(days=1))
    await keepsakes.run_due()
    assert len((await cabinet(client))["keepsakes"]) == 1
    assert "你刚从大阪回来" in context_of(llm.prompts[-1])


async def test_switched_off_or_not_turned_on_she_gives_none(
    make_client: ClientFactory, settings: Settings
):
    client, llm, _ = await talked(make_client, settings, GIVES)
    keepsakes = backend_of(client).keepsakes
    me = user_id(settings.database_path, "producer")
    await client.put("/api/account/features/keepsakes", json={"on": False})

    await keepsakes.scene_played(me, "mano", "close", at(11))
    await keepsakes.run_due()

    assert (await cabinet(client))["keepsakes"] == []
    assert len(llm.prompts) == 1


async def test_a_day_now_and_then_has_a_trip_away_from_tokyo(
    make_client: ClientFactory, settings: Settings
):
    # No twist (0), a unit trip (0), to the third place (2).
    client, _, clock = await schedule_talked(
        make_client, settings, [MANO_PLAN], numbers=(0, 0, 2, 1, 0)
    )
    clock.now = schedule_today_at(6)

    await backend_of(client).schedule_writer.run_due()

    written = await schedule(client)
    assert written is not None and written["trip"] == "北海道"
    assert written["twists"] == [TRIP_TWIST.format(place="北海道")]


async def test_with_its_pictures_switched_off_it_still_comes_without_one_and_none_are_missing(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client, _, _ = await talked(make_client, settings, GIVES, image=image)
    res = await client.put("/api/account/features/keepsake_images", json={"on": False})

    await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    shown = await cabinet(client)

    assert res.status_code == 204 and image.requests == []
    assert shown["keepsakes"][0]["has_image"] is False
    assert (shown["missing"], shown["can_draw"]) == (0, False)
    assert (await client.post("/api/keepsakes/fill-images")).json()["started"] is False


async def test_one_given_before_its_sentence_is_described_anew_and_redrawn(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    described = [
        "what: bookmark\ntags: four-leaf clover, pink ribbon\n"
        "scene: A clear bookmark with a pressed four-leaf clover lies on a desk."
    ]
    client, _, _ = await talked(make_client, settings, GIVES, described, image=image)
    await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    [given] = (await cabinet(client))["keepsakes"]

    redrawn = await client.post(f"/api/keepsakes/{given['id']}/redraw")

    assert redrawn.status_code == 200, redrawn.text
    assert redrawn.json()["drawn"] == 1 and given["drawn"] == 0
    assert "A clear bookmark with a pressed four-leaf clover lies on a desk" in (
        image.requests[-1]["body"]["input"]
    )
    assert (await client.post("/api/keepsakes/999/redraw")).status_code == 404


async def test_taken_out_it_leaves_the_cabinet_and_the_conversation(
    make_client: ClientFactory, settings: Settings
):
    client, _, conversation = await talked(make_client, settings, GIVES)
    await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    [given] = (await cabinet(client))["keepsakes"]

    taken = await client.delete(f"/api/keepsakes/{given['id']}")
    other = make_client(FakeLLM(), user="someone")

    assert taken.status_code == 204
    assert (await cabinet(client))["keepsakes"] == []
    assert [e for e in await record(client, conversation) if e.get("type") == "event"] == []
    assert (await other.delete(f"/api/keepsakes/{given['id']}")).status_code == 404


async def test_a_souvenir_more_than_a_day_after_its_trip_is_let_go(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(10))
    client, _, conversation = await talked(
        make_client, settings, GIVES, clock=clock, schedules=True,
    )
    last_contact(settings.database_path, conversation, at(9))
    me = user_id(settings.database_path, "producer")
    keepsakes = backend_of(client).keepsakes
    await keepsakes._put_down(
        me, "mano", f"souvenir:{TODAY - timedelta(days=2)}", "souvenir", "京都", at(10),
    )

    await keepsakes.run_due()

    assert (await cabinet(client))["keepsakes"] == []


async def test_a_trip_souvenir_is_not_planned_again_after_it_was_given(
    make_client: ClientFactory, settings: Settings
):
    # A trip whose souvenir was given must not be re-planned by the next tick,
    # nor come back after the three-day cooldown.
    clock = FakeClock(at(10))
    client, llm, conversation = await talked(
        make_client, settings, GIVES, clock=clock, schedules=True,
    )
    last_contact(settings.database_path, conversation, at(9))
    me = user_id(settings.database_path, "producer")
    await backend_of(client).daily.keep(
        me, "mano", TODAY - timedelta(days=1),
        [Slot(time(10), time(12), "work", "Osaka", "work")], [], trip="Osaka",
    )

    keepsakes = backend_of(client).keepsakes
    await keepsakes.run_due()
    await keepsakes.run_due()
    clock.advance(days=3)
    await keepsakes.run_due()

    assert len((await cabinet(client))["keepsakes"]) == 1


async def test_a_given_keepsake_is_also_said_first_in_the_conversation(
    make_client: ClientFactory, settings: Settings
):
    client, _, conversation = await talked(
        make_client, settings, GIVES, reach_outs=True,
    )

    res = await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    entries = await record(client, conversation)
    [given] = (await cabinet(client))["keepsakes"]

    assert res.status_code == 201
    [announced] = [e for e in entries if e.get("type") == "reach_out"]
    assert announced["zh"] == "这个，送给制作人"
    assert announced["ja"] == "これ、プロデューサーさんに"
    assert given["said"] == [{"ja": announced["ja"], "zh": announced["zh"]}]


async def test_a_keepsake_is_announced_soon_after_a_reach_out(
    make_client: ClientFactory, settings: Settings
):
    """The gift is already given; a scheduled reach-out an hour before must not
    swallow the only line that tells the producer about it."""
    client, _, conversation = await talked(
        make_client, settings, GIVES, reach_outs=True,
    )
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "INSERT INTO reach_outs (conversation_id, ja, zh, segments, created_at, seen)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            (conversation, "さっきの話", "刚才的话", "[]", at(11).isoformat()),
        )

    res = await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    entries = await record(client, conversation)

    assert res.status_code == 201
    [announced] = [
        e for e in entries
        if e.get("type") == "reach_out" and e["zh"] == "这个，送给制作人"
    ]
    assert announced["ja"] == "これ、プロデューサーさんに"
