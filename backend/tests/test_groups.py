"""The group chats (GLOSSARY.md「群聊」): a user starts a group of idols, who talk among
themselves about a topic by relevance, the user answering as an ordinary member; what the
user reveals is written into each member's memories with the feeling and the tier's
strength, as a private turn's are."""

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from eidolon.groups import Groups, Member, _seven_am
from eidolon.memory import NewMemory
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM
from tests.test_reach_outs import backend_of


async def test_a_group_is_created_listed_read_and_dissolved(make_client: ClientFactory):
    client = make_client(FakeLLM())

    created = await client.post(
        "/api/groups", json={"name": "群聊", "character_ids": ["mano", "kogane"]}
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]

    assert [g["id"] for g in (await client.get("/api/groups")).json()] == [group_id]

    read = (await client.get(f"/api/group/{group_id}")).json()
    assert read["name"] == "群聊"
    assert [m["id"] for m in read["members"]] == ["mano", "kogane"]

    assert (await client.delete(f"/api/group/{group_id}")).status_code == 204
    assert (await client.get("/api/groups")).json() == []
    assert (await client.get(f"/api/group/{group_id}")).status_code == 404


async def test_the_groups_last_prompt_is_read_only_with_the_development_switch(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM())
    created = await client.post(
        "/api/groups", json={"name": "群聊", "character_ids": ["mano", "kogane"]}
    )
    group_id = created.json()["id"]
    backend_of(client).groups._last_prompts[group_id] = {
        "prompt": "PROMPT", "speaker_id": "mano", "speaker_name": "真乃",
    }

    off = (await client.get(f"/api/group/{group_id}/last-prompt")).json()
    await client.put("/api/account/dev", json={"on": True})
    on = (await client.get(f"/api/group/{group_id}/last-prompt")).json()

    assert off == {"prompt": None, "speaker_id": None, "speaker_name": None}
    assert on == {"prompt": "PROMPT", "speaker_id": "mano", "speaker_name": "真乃"}


async def test_a_group_with_no_members_picked_gets_the_default_ones(make_client: ClientFactory):
    client = make_client(FakeLLM())

    created = await client.post("/api/groups", json={"name": "群聊", "character_ids": []})
    assert created.status_code == 201, created.text

    read = (await client.get(f"/api/group/{created.json()['id']}")).json()
    assert [m["id"] for m in read["members"]] == ["mano", "kogane"]  # the only characters


def test_what_the_user_revealed_is_remembered_with_feeling_and_tier():
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, user_id TEXT, source_character_id TEXT,"
        " conversation_id TEXT, text TEXT, created_at TEXT, scope TEXT, emotion TEXT,"
        " strength REAL NOT NULL DEFAULT 1, maintain_until TEXT, kind TEXT NOT NULL DEFAULT 'short')"
    )
    now = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    owner = Member("mano", "櫻木真乃", "intro", "sheet", "真乃")
    written = [NewMemory("制作人养了一只猫", emotion="开心", tier="strong")]

    Groups._write_memories(None, db, "user-1", owner, written, now)

    row = db.execute(
        "SELECT text, emotion, strength, maintain_until, kind, conversation_id FROM memories"
    ).fetchone()
    assert row == (
        "制作人养了一只猫", "开心", 1.0,
        (now + timedelta(hours=48)).astimezone(timezone.utc).isoformat(), "short", None,
    )


def test_seven_am_is_the_next_morning_in_shanghai():
    # 23:00 in Shanghai (15:00 UTC) -> the next day's 7, as 23:00 UTC.
    now = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
    assert _seven_am(now, "Asia/Shanghai") == "2026-11-09T23:00:00+00:00"
    # Just before 7 in Shanghai (22:59 UTC) -> the same day's 7.
    just_before = datetime(2026, 11, 9, 22, 59, tzinfo=timezone.utc)
    assert _seven_am(just_before, "Asia/Shanghai") == "2026-11-09T23:00:00+00:00"


async def test_the_asleep_are_left_out_of_a_topic():
    groups = object.__new__(Groups)
    asleep = {"rinze"}

    async def fake_asleep(user_id: str, character_id: str, now: datetime) -> bool:
        return character_id in asleep

    groups._asleep = fake_asleep  # type: ignore[attr-defined]
    members = [
        Member("mano", "櫻木真乃", "i", "s", "真乃"),
        Member("rinze", "杜野凛世", "i", "s", "凛世"),
    ]

    awake = await groups._awake("u", members, datetime(2026, 11, 9, tzinfo=timezone.utc))

    assert [m.id for m in awake] == ["mano"]


async def test_the_next_topic_waits_until_they_all_wake():
    from datetime import time as clock_time

    groups = object.__new__(Groups)

    class FakeDaily:
        wakes = {"mano": clock_time(6, 0), "rinze": clock_time(8, 0)}

        async def of(self, user_id, character_id, day):
            wake = self.wakes.get(character_id)
            return type("S", (), {"wake": wake})() if wake is not None else None

    groups._daily = FakeDaily()  # type: ignore[attr-defined]
    members = [
        Member("mano", "櫻木真乃", "i", "s", "真乃"),
        Member("rinze", "杜野凛世", "i", "s", "凛世"),
    ]
    local = datetime(2026, 11, 9, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    awake_at = await groups._next_awake_at("u", members, local)

    assert awake_at == datetime(2026, 11, 9, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


async def test_the_topic_is_told_the_time_and_what_members_are_doing():
    from datetime import time as clock_time

    from eidolon.schedules import Schedule, Slot

    groups = object.__new__(Groups)
    day = datetime(2026, 11, 9, tzinfo=ZoneInfo("Asia/Shanghai")).date()

    class FakeDaily:
        async def of(self, user_id, character_id, when):
            if when == day:
                return Schedule(
                    day,
                    (Slot(clock_time(9), clock_time(16), "上学", "学校", "school"),),
                    (), wake=clock_time(7), sleep=clock_time(23),
                )
            return None

    class FakeStore:
        async def get_profile(self, user_id):
            return type("P", (), {"timezone": "Asia/Shanghai"})()

    groups._daily = FakeDaily()  # type: ignore[attr-defined]
    groups._store = FakeStore()  # type: ignore[attr-defined]
    members = [Member("mano", "櫻木真乃", "i", "s", "樱木真乃")]

    told = await groups._now_told(
        "u", members, datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)  # 10:00 Shanghai
    )

    assert "10:00" in told
    assert "现在在上学" in told
