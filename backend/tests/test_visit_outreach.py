"""夜间搭话（visit_outreach）和每日行程的作息挂钩：她睡着或泡澡时不主动搭话。"""

from datetime import UTC, datetime, time

from eidolon.schedules import Slot
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM
from tests.test_delete_account import user_id
from tests.test_reach_outs import backend_of

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
TODAY = datetime.now(UTC).date()


def at(hour: int) -> datetime:
    return datetime.combine(TODAY, time(hour), UTC)


async def test_a_sleeping_idol_does_not_reach_out(make_client: ClientFactory, settings):
    """她 22:00 就睡了：23:00 访问不再触发搭话，20:00（醒着）照常走后面的资格判断。"""
    client = make_client(FakeLLM(REPLY))
    backend = backend_of(client)
    me = user_id(settings.database_path, "producer")
    await backend.daily.keep(
        me, "mano", TODAY,
        [Slot(time(9), time(18), "练习", "事务所", "lesson")], [],
        wake=time(7), sleep=time(22),
    )
    conversation = await backend.store.create_conversation(me, "mano")
    await client.put("/api/account/visit-outreach-mode", json={"mode": "natural"})

    asleep, _, _ = await backend.visits.host.eligibility(conversation, at(23))
    awake, _, _ = await backend.visits.host.eligibility(conversation, at(20))

    assert asleep == "asleep"
    assert awake != "asleep"
