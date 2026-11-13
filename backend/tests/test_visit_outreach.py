"""夜间搭话（visit_outreach）和每日行程的作息挂钩：她睡着或泡澡时不主动搭话。"""

from datetime import UTC, datetime, time

from eidolon.schedules import Slot
from eidolon.store import ConversationEvent
from eidolon.visit_outreach.history import narrations
from tests.conftest import ClientFactory
from tests.fakes import FakeClock, FakeLLM
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


async def test_the_reach_out_tells_her_what_she_is_doing_now(make_client: ClientFactory, settings):
    """每日行程喂进搭话：按时间告诉她今天的大概安排，并强调「现在你大概在做什么」。"""
    client = make_client(FakeLLM(REPLY), clock=FakeClock(at(15)))
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    backend = backend_of(client)
    me = user_id(settings.database_path, "producer")
    await backend.daily.keep(
        me, "mano", TODAY,
        [Slot(time(9), time(18), "练习", "事务所", "lesson")], [],
        wake=time(7), sleep=time(22),
    )
    conversation = await backend.store.create_conversation(me, "mano")

    ready = await backend.visits.real_generate.context.prepare(conversation)

    assert "你大概在" in ready.about_now
    assert "练习" in ready.about_now


def test_keepsake_events_are_told_without_being_mistaken_for_gifts():
    """纪念品事件也有 gift_id=None；夜间搭话不能把它当成礼物而断言失败。"""
    event = ConversationEvent(
        1, "keepsake", "2026-11-13T02:18:51+00:00",
        keepsake_id=15, note="手作的猫头鹰钥匙扣",
    )

    assert narrations([event], {}, "Asia/Shanghai")[None] == "（你送给了制作人「手作的猫头鹰钥匙扣」。）\n"


def test_missed_call_events_are_told_as_a_missed_call():
    event = ConversationEvent(2, "missed_call", "2026-11-13T02:20:00+00:00")

    assert narrations([event], {}, "Asia/Shanghai")[None] == "（制作人打来电话，你那会儿在洗澡，没接到。）\n"
