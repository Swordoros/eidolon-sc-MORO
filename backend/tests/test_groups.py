"""The group chats (GLOSSARY.md「群聊」): a user starts a group of idols, who talk among
themselves about a topic by relevance, the user answering as an ordinary member; what the
user reveals is written into each member's memories with the feeling and the tier's
strength, as a private turn's are."""

import sqlite3
from datetime import datetime, timedelta, timezone

from eidolon.groups import Groups, Member
from eidolon.memory import NewMemory
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM


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
