"""The calendar over HTTP (GLOSSARY.md「日历」): `/api/calendar?year=` gives the idols'
birthdays and the holidays that year has (both empty without the calendar file), the user's
own anniversaries (纪念日), and their promises (约定) still to come, for those signed in; and
Japan's holidays of a year at `/api/calendar/holidays?year=`. The producer's own birthday is
in their profile."""

import re

from fastapi import FastAPI, Query, Response

from eidolon import anniversaries
from eidolon.assembly import Backend
from eidolon.japan_holidays import holidays_of
from eidolon.producer import effective_timezone
from eidolon.sessions import UserId

_KEPT_A_DAY = {"Cache-Control": "private, max-age=86400"}
"""A year's holidays change only as the law does, and the library with it."""

_YEAR = re.compile(r"[0-9]{1,4}")


def add_calendar_routes(app: FastAPI, backend: Backend) -> None:
    @app.get("/api/calendar")
    async def calendar(user_id: UserId, year: int = Query(ge=1, le=9999)) -> dict:
        """The calendar of `year` (GLOSSARY.md「日历」), for the calendar page."""
        days = backend.special_days
        profile = await backend.store.get_profile(user_id)
        return {
            **(days.described(year) if days is not None else {"birthdays": [], "holidays": []}),
            "anniversaries": anniversaries.described(
                await backend.store.first_records(user_id), year,
                effective_timezone(profile, None),
            ),
            "promises": [
                promise.shown()
                for promise in await backend.promises.open_for(user_id)
                if promise.due.year == year
            ],
        }

    @app.get("/api/calendar/holidays")
    def holidays(_: UserId, response: Response, year: str = "") -> list[dict]:
        """The holidays of `year`, by date; none for what is no year, or one without any
        known."""
        response.headers.update(_KEPT_A_DAY)
        if not _YEAR.fullmatch(year):
            return []
        return [{"date": day.isoformat(), "name": name} for day, name in holidays_of(int(year))]
