"""The songs and albums the world setting has, for the songs page, and the NetEase lookup
and stream that play them (fork's 歌曲 page).

Her songs are already in `world.json`; this only groups them by singer and by release. A
song is played through NetEase's official link: the browser asks this server, which asks
NetEase where the CDN copy is and relays it with the browser's `Range`. No audio is kept.
"""

import logging
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from eidolon.assembly import Backend
from eidolon.music import MusicLibrary

log = logging.getLogger(__name__)


def add_music_routes(app: FastAPI, backend: Backend) -> None:
    """The songs page's routes, over the world setting in `backend`."""
    songs = MusicLibrary(backend.world)
    characters = backend.characters

    @app.get("/api/music/songs")
    def music_songs(character_id: str | None = None) -> dict:
        """The shelf: all songs, or only hers when `character_id` is given."""
        singer = None
        if character_id is not None:
            character = characters.get(character_id)
            if character is None:
                raise HTTPException(404, "角色不存在")
            singer = character.name
        return {
            "songs": [track.described() for track in songs.songs(singer)],
            "singers": songs.singer_names(),
        }

    @app.get("/api/music/albums")
    def music_albums() -> dict:
        """The releases, newest first, with their track lists."""
        return {"albums": [album.described() for album in songs.albums()]}

    @app.get("/api/music/stream")
    async def music_stream(id: str, request: Request, title: str = "") -> Response:
        """Relay one song's audio from NetEase's CDN, keeping no copy of it.

        NetEase's outer URL redirects to an **http** CDN address, which a browser on this
        https page would refuse as mixed content. Ask it for that address, then ask the
        CDN with the browser's `Range`; some songs are licensed on another release, tried
        through `title`'s alternatives; a song with no licence is a 404.
        """
        client = httpx.AsyncClient(trust_env=False, timeout=20)

        async def location_of(candidate: str) -> str | None:
            outer = f"https://music.163.com/song/media/outer/url?id={quote(candidate)}.mp3"
            hop = await client.get(
                outer,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"},
                follow_redirects=False,
            )
            found_at = hop.headers.get("location")
            if hop.status_code not in (301, 302, 303, 307) or not found_at:
                return None
            return None if found_at.endswith("/404") else found_at

        if title and not songs.alternatives(title):
            await songs.lookup(client, title)
        tried = [id, *(songs.alternatives(title) if title else [])]
        upstream = None
        try:
            for candidate in tried:
                found_at = await location_of(candidate)
                if found_at is None:
                    continue
                headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"}
                if "range" in request.headers:
                    headers["Range"] = request.headers["range"]
                attempt = await client.send(
                    client.build_request("GET", found_at, headers=headers), stream=True
                )
                kind = attempt.headers.get("content-type", "")
                if attempt.status_code in (200, 206) and kind.startswith("audio/"):
                    upstream = attempt
                    break
                await attempt.aclose()
        except httpx.HTTPError as error:
            await client.aclose()
            log.warning("取网易云音频失败（%s）：%r", id, error)
            raise HTTPException(502, "取不到这首的音频") from None

        if upstream is None:
            await client.aclose()
            raise HTTPException(404, "网易云没有这首的版权")
        kind = upstream.headers.get("content-type", "audio/mpeg")
        export = {
            name: upstream.headers[name]
            for name in ("content-length", "content-range", "accept-ranges")
            if name in upstream.headers
        }

        async def pump() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            pump(), media_type=kind, headers=export, status_code=upstream.status_code
        )

    @app.get("/api/music/lookup")
    async def music_lookup(title: str) -> dict:
        """Which NetEase song `title` is: its id and name, or null."""
        async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
            found = await songs.lookup(client, title)
        return {"found": found}
