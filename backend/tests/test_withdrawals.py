from __future__ import annotations

from tests.fakes import FakeLLM
from tests.test_text_turn import send_turn, start_conversation


async def test_withdrawing_a_turn_keeps_its_score_but_takes_both_messages_out(make_client):
    llm = FakeLLM(["<ja>はいっ</ja><zh>嗯</zh>"])
    client = make_client(llm)
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "打错的话")

    record = (await client.get(f"/api/conversations/{conversation_id}/messages")).json()
    user = next(entry for entry in record if entry.get("role") == "user")

    withdrawn = await client.post(
        f"/api/conversations/{conversation_id}/messages/{user['id']}/withdraw"
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["text"] == "打错的话"

    after = (await client.get(f"/api/conversations/{conversation_id}/messages")).json()
    assert [entry.get("type") for entry in after] == ["withdrawal"]
    assert after[0]["text"] == "打错的话"
    assert all(entry.get("role") not in ("user", "character") for entry in after)
