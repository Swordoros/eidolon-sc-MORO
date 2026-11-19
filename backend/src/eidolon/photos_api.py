"""The photos she sent over HTTP (GLOSSARY.md「照片」, ADR-0024): how one is coming along at
`/api/photos/{id}`, for the browser to wait on while it is being generated, and its picture,
once sent, at `/api/photos/{id}/image`. Only the user whose conversation it is in reads
either; to anyone else, admins too, there is no such photo (404). The picture is the
browser's alone to keep: never a shared cache's."""

from fastapi import FastAPI, HTTPException, Response

from eidolon.assembly import Backend
from eidolon.sessions import UserId

_PHOTO_CACHE = {"Cache-Control": "private, max-age=31536000, immutable"}
"""A photo's picture never changes once sent."""

NOT_FOUND = "照片不存在"


def add_photo_routes(app: FastAPI, backend: Backend) -> None:
    store = backend.store

    @app.get("/api/photos/{photo_id}")
    async def read_photo(photo_id: str, user_id: UserId) -> dict:
        """Whether the photo is being generated (`pending`), was sent (`ready`) or was not
        (`failed`), and why not."""
        photo = await store.photo(photo_id, user_id)
        if photo is None:
            raise HTTPException(404, NOT_FOUND)
        return {"status": photo.status, "reason": photo.reason}

    @app.post("/api/photos/{photo_id}/retry")
    async def retry_photo(photo_id: str, user_id: UserId) -> dict:
        """Try a failed photo again, from the tag already kept with it."""
        photo = await store.photo(photo_id, user_id)
        if photo is None:
            raise HTTPException(404, NOT_FOUND)
        if photo.status == "pending":
            return {"status": "pending", "reason": None}
        if photo.status != "failed":
            raise HTTPException(409, "这张照片已经发出来了")
        conversation = await store.photo_conversation(photo_id, user_id)
        if conversation is None:
            raise HTTPException(404, NOT_FOUND)
        character = backend.characters.get(conversation.character_id)
        if character is None:
            raise HTTPException(404, NOT_FOUND)
        if not await backend.photos.retry(photo, character, user_id):
            current = await store.photo(photo_id, user_id)
            if current is not None and current.status == "pending":
                return {"status": "pending", "reason": None}
            raise HTTPException(409, "这张照片现在不能重试")
        return {"status": "pending", "reason": None}

    @app.get("/api/photos/{photo_id}/image")
    async def read_photo_image(photo_id: str, user_id: UserId) -> Response:
        image = await store.photo_image(photo_id, user_id)
        if image is None:
            raise HTTPException(404, NOT_FOUND)
        return Response(image, media_type="image/webp", headers=_PHOTO_CACHE)
