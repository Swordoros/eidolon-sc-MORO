"""The day's first talk in one of her work or life slots (GLOSSARY.md「行程」): +0.2 once a
day, outside the turns' daily gains, as a gift does; the first turn of a new conversation is
the producer's own, and a later turn that day adds nothing."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from eidolon.affinity import NORMAL
from eidolon.config import Settings
from eidolon.schedules import Schedules, Slot
from tests.conftest import ClientFactory, database_in
from tests.fakes import FAKE_JUDGE, FakeClock, FakeLLM
from tests.test_delete_account import user_id
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
DAY = datetime(2026, 11, 13, 10, tzinfo=UTC)
"""A Friday at 10:00 UTC, where the producer's profile says they live."""


def affinity(database: Path, character: str = "mano") -> float:
    with sqlite3.connect(database) as db:
        row = db.execute(
            "SELECT value FROM affinities WHERE user_id = ? AND character_id = ?",
            (user_id(database, "producer"), character),
        ).fetchone()
    return row[0] if row else NORMAL


def count(database: Path) -> int:
    with sqlite3.connect(database) as db:
        return db.execute("SELECT COUNT(*) FROM daily_talk_bonuses").fetchone()[0]


async def keep(settings: Settings, clock: FakeClock, day: datetime) -> None:
    async with database_in(settings.database_path) as db:
        await Schedules(db, lambda: clock.now).keep(
            user_id(settings.database_path, "producer"),
            "mano",
            day.date(),
            [Slot(time(9), time(12), "练习", "事务所", "lesson")],
            [],
        )


async def test_the_first_talk_of_a_day_in_a_slot_is_the_only_one(
    make_client: ClientFactory, settings: Settings
):
    settings = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True, schedules=True)
    database = settings.database_path
    clock = FakeClock(DAY)
    client = make_client(FakeLLM(REPLY, REPLY, REPLY), clock=clock, settings=settings)
    assert (await client.put("/api/profile", json={"timezone": "Etc/GMT"})).status_code == 200
    await keep(settings, clock, DAY)
    conversation = await start_conversation(client)

    await send_turn(client, conversation, "我来看看你")
    first = affinity(database)
    await send_turn(client, conversation, "还在忙吗")
    still = affinity(database)

    assert (count(database), first, still) == (1, 50.2, 50.2)

    clock.advance(days=1)
    await keep(settings, clock, clock.now)
    await send_turn(client, conversation, "今天也来看看你")

    assert count(database) == 2
    assert affinity(database) == pytest.approx(50.4)


async def test_outside_a_slot_or_without_a_schedule_nothing_is_claimed(
    make_client: ClientFactory, settings: Settings
):
    settings = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True, schedules=True)
    database = settings.database_path
    clock = FakeClock(DAY + timedelta(hours=3))  # 13:00, between her slots
    client = make_client(FakeLLM(REPLY, REPLY), clock=clock, settings=settings)
    assert (await client.put("/api/profile", json={"timezone": "Etc/GMT"})).status_code == 200
    await keep(settings, clock, clock.now)
    conversation = await start_conversation(client)

    await send_turn(client, conversation, "下午好")
    clock.advance(hours=2)
    await send_turn(client, conversation, "还在吗")

    assert (count(database), affinity(database)) == (0, 50.0)
