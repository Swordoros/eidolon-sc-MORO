"""Work moments now come from the AI daily schedule, not the old producer plan."""

from datetime import date, datetime, time
from dataclasses import replace

import pytest

from eidolon.config import Settings
from eidolon.moment_prompt import JOBS_OPENING, WORK_STATUS_OPENING
from eidolon.schedules import Slot
from tests.conftest import ClientFactory
from tests.fakes import context_of
from tests.test_admin_users import ADMIN
from tests.test_delete_account import user_id
from tests.test_moments import POST, START, talked, tick, timeline
from tests.test_reach_outs import backend_of

DAY = date(2026, 10, 19)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return replace(settings, moments=True, admins=frozenset({ADMIN}))


async def schedule(client, kind: str, start: time, end: time, sleep: time = time(23)):
    backend = backend_of(client)
    user = user_id(backend.settings.database_path, "producer")
    await backend.daily.keep(
        user, "mano", DAY,
        [Slot(start, end, "拍摄", "横滨", kind)], (),
        wake=time(7), sleep=sleep,
    )


async def test_work_moment_uses_the_ai_schedule_and_says_it_is_still_working(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock, _ = await talked(make_client, POST, at=START)
    await schedule(client, "work", time(9), time(12))

    await tick(client, clock, START)
    await tick(client, clock, datetime(2026, 10, 19, 1, 29, tzinfo=START.tzinfo))
    assert await timeline(client) == []

    await tick(client, clock, datetime(2026, 10, 19, 1, 30, tzinfo=START.tzinfo))
    [moment] = await timeline(client)
    assert moment["posted_at"] == "2026-10-19T01:30:00+00:00"
    told = context_of(llm.prompts[-1])
    assert WORK_STATUS_OPENING in told
    assert "你正在拍摄" in told
    assert JOBS_OPENING not in told


async def test_live_is_posted_only_after_it_ends(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock, _ = await talked(make_client, POST, at=START)
    await schedule(client, "live", time(9), time(12))

    await tick(client, clock, START)
    assert await timeline(client) == []
    await tick(client, clock, datetime(2026, 10, 19, 3, 59, tzinfo=START.tzinfo))
    assert await timeline(client) == []

    await tick(client, clock, datetime(2026, 10, 19, 4, 0, tzinfo=START.tzinfo))
    [moment] = await timeline(client)
    told = context_of(llm.prompts[-1])
    assert WORK_STATUS_OPENING in told and "你刚结束" in told
    assert JOBS_OPENING in told


async def test_no_work_moment_is_planned_while_she_is_asleep(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock, _ = await talked(make_client, POST, at=START)
    await schedule(client, "work", time(12), time(13), sleep=time(12))

    await tick(client, clock, START)
    await tick(client, clock, datetime(2026, 10, 19, 2, 0, tzinfo=START.tzinfo))

    assert await timeline(client) == []
    assert len(llm.prompts) == 1
