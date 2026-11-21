"""The developer's view of a character's relationship with the producer.

Only the development tools read or write it (GLOSSARY.md「开发」). The relationship itself
is written at 03:00 and is part of the conversation prompt; the user never sees it.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.producer import effective_timezone
from eidolon.relationships import Relationship, RelationshipBaseline, Relationships
from eidolon.sessions import UserId


class RelationshipEdit(BaseModel):
    text: str | None = None
    baseline: str | None = None


def _shown(
    row: Relationship | None, baseline: RelationshipBaseline | None, affinity
) -> dict:
    shown = {
        "relationship": None,
        "baseline": None,
        "current_affinity": affinity.shown() if affinity else None,
    }
    if row is not None:
        shown["relationship"] = {
            "day": row.day.isoformat(),
            "text": row.text,
            "state": row.state,
            "affinity_tier": row.affinity_tier,
            "edited": row.edited,
            "generated_at": row.generated_at.isoformat(),
        }
    if baseline is not None:
        shown["baseline"] = {
            "text": baseline.text,
            "relationship": baseline.relationship,
            "opinion": baseline.opinion,
            "habits": list(baseline.habits),
            "edited": baseline.edited,
            "generated_at": baseline.generated_at.isoformat(),
        }
    return shown


def add_relationship_routes(app: FastAPI, backend: Backend) -> None:
    relationships: Relationships = backend.relationships
    store = backend.store

    async def require_dev(user_id: str) -> None:
        if not await store.get_dev(user_id):
            raise HTTPException(404, "没有这项开发功能")

    async def zone(user_id: str) -> str:
        return effective_timezone(await store.get_profile(user_id), None)

    @app.get("/api/dev/relationship/{character_id}")
    async def get_relationship(user_id: UserId, character_id: str) -> dict:
        await require_dev(user_id)
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")
        timezone = await zone(user_id)
        now = backend.clock()
        row = await relationships.dev_get(user_id, character_id, now, timezone)
        baseline = await relationships.baseline(user_id, character_id)
        status = await backend.affinities.status(user_id, character_id)
        return _shown(row, baseline, status)

    @app.put("/api/dev/relationship/{character_id}")
    async def save_relationship(
        user_id: UserId, character_id: str, body: RelationshipEdit
    ) -> dict:
        await require_dev(user_id)
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")
        if body.text is None and body.baseline is None:
            raise HTTPException(422, "没有要保存的关系文本")
        try:
            if body.text is not None:
                await relationships.save(
                    user_id, character_id, body.text, backend.clock(), await zone(user_id)
                )
            if body.baseline is not None:
                await relationships.save_baseline(
                    user_id, character_id, body.baseline, backend.clock()
                )
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        row = await relationships.dev_get(
            user_id, character_id, backend.clock(), await zone(user_id)
        )
        baseline = await relationships.baseline(user_id, character_id)
        return _shown(row, baseline, await backend.affinities.status(user_id, character_id))

    @app.post("/api/dev/relationship/{character_id}/regenerate")
    async def regenerate_relationship(user_id: UserId, character_id: str) -> dict:
        await require_dev(user_id)
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")
        try:
            row = await relationships.regenerate(
                user_id, character_id, backend.clock(), await zone(user_id)
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        baseline = await relationships.baseline(user_id, character_id)
        return _shown(row, baseline, await backend.affinities.status(user_id, character_id))
