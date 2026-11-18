"""The producer's current weather, for the conversation list's header."""

from fastapi import FastAPI

from eidolon.assembly import Backend
from eidolon.sessions import UserId


def add_weather_routes(app: FastAPI, backend: Backend) -> None:
    @app.get("/api/weather")
    async def weather(user_id: UserId) -> dict:
        """The producer's current weather, or Tokyo's without a city set; null when it
        cannot be known."""
        profile = await backend.store.get_profile(user_id)
        known = await backend.her_now.weather("typing", profile.city, backend.clock(), None)
        if known is None:
            return {"weather": None}
        today = known.there if profile.city is not None else known.tokyo
        return {"weather": today.now.shown() if today is not None else None}
