"""Letters over HTTP (GLOSSARY.md「信」): `/api/letters` lists the signed-in user's letters,
the latest first; `/api/letters/{id}` reads one; `/api/letters/{id}/read` has it read (PUT),
on every device, and `/api/letters/{id}/popped` popped up, so it does not again; DELETE
`/api/letters/{id}` deletes it, its voice with it; `/api/letters/{id}/voice/{block}` is a block of it read aloud, as mp3, the
browser's alone to keep. Another's letter, or none, is none (404), to admins too. Whether the
deployer has letters on or off, the letters written stay to be read."""

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.letter_prompt import Occasion
from eidolon.sessions import UserId

_VOICE_CACHE = {"Cache-Control": "private, max-age=31536000, immutable"}
"""A letter read aloud never changes once written."""


class WriteNow(BaseModel):
    """A letter to be written at once, under the development sign-in (`write_now`)."""

    character_id: str
    occasion: Occasion = "producer_birthday"


def add_letter_routes(app: FastAPI, backend: Backend) -> None:
    @app.get("/api/letters")
    async def letters(user_id: UserId) -> dict:
        """The user's letters, the latest first, each with the keepsake that came with it."""
        listed = await backend.letters.listed(user_id)
        shown = []
        for each in listed:
            body = each.shown()
            keepsake = await backend.keepsakes.for_letter(user_id, each.id)
            if keepsake is not None:
                body["keepsake"] = keepsake.shown()
            shown.append(body)
        return {"letters": shown}

    @app.get("/api/letters/{letter_id}")
    async def letter(letter_id: int, user_id: UserId) -> dict:
        found = await backend.letters.letter(user_id, letter_id)
        if found is None:
            raise HTTPException(404, "信不存在")
        return found.shown()

    @app.get("/api/letters/{letter_id}/voice/{block}")
    async def voice(letter_id: int, block: int, user_id: UserId) -> Response:
        mp3 = await backend.letters.voice(user_id, letter_id, block)
        if mp3 is None:
            raise HTTPException(404, "信不存在")
        return Response(mp3, media_type="audio/mpeg", headers=_VOICE_CACHE)

    @app.put("/api/letters/{letter_id}/read", status_code=204)
    async def mark_read(letter_id: int, user_id: UserId) -> None:
        if not await backend.letters.mark_read(user_id, letter_id):
            raise HTTPException(404, "信不存在")

    @app.put("/api/letters/{letter_id}/popped", status_code=204)
    async def mark_popped(letter_id: int, user_id: UserId) -> None:
        if not await backend.letters.mark_popped(user_id, letter_id):
            raise HTTPException(404, "信不存在")

    @app.delete("/api/letters/{letter_id}", status_code=204)
    async def delete(letter_id: int, user_id: UserId) -> None:
        if not await backend.letters.delete(user_id, letter_id):
            raise HTTPException(404, "信不存在")

    if backend.settings.dev_login:

        @app.post("/api/letters/write-now", status_code=201)
        async def write_now(user_id: UserId, body: WriteNow) -> dict:
            """Have her write a letter for the occasion at once, to try letters out without
            waiting for the day; 404 for a character she has no conversation with."""
            letter_id = await backend.letters.write_now(user_id, body.character_id, body.occasion)
            if letter_id is None:
                raise HTTPException(404, "还没有和她的对话")
            return {"id": letter_id}
