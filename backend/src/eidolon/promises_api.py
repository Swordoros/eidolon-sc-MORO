"""Promises over HTTP (GLOSSARY.md「约定」), for users who agreed to the terms: what the
producer promised each idol and has not done yet (`/api/promises`), with the day each is for;
and saying one was done (`POST /api/promises/{id}/fulfil`), which takes it away as giving her
the gift of its name would."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.promises import PromisePeriod
from eidolon.sessions import UserId


class PeriodEdit(BaseModel):
    period: PromisePeriod


def add_promise_routes(app: FastAPI, backend: Backend) -> None:
    promises = backend.promises
    if promises is None:
        return

    @app.get("/api/promises")
    async def list_promises(user_id: UserId) -> list[dict]:
        return [promise.shown() for promise in await promises.open_for(user_id)]

    @app.patch("/api/promises/{promise_id}/period")
    async def set_period(user_id: UserId, promise_id: int, body: PeriodEdit) -> dict:
        promise = await promises.one(user_id, promise_id)
        if promise is None:
            raise HTTPException(404, "没有这个约定")
        if promise.kind not in ("meeting", "call"):
            raise HTTPException(422, "只有见面和电话约定可以设置时段")
        if await backend.daily.of(user_id, promise.character_id, promise.due) is not None:
            raise HTTPException(409, "今天的日程已经生成，去聊天里和她商量")
        saved = await promises.set_period(user_id, promise_id, body.period, backend.clock())
        if saved is None:
            raise HTTPException(422, "这个时段无效")
        return saved.shown()

    @app.post("/api/promises/{promise_id}/fulfil")
    async def fulfil(user_id: UserId, promise_id: int) -> dict:
        if not await promises.fulfil(user_id, promise_id):
            raise HTTPException(404, "没有这个约定")
        return {"ok": True}
