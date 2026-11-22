"""Japan's national holidays and the days around them, for a day's schedule.

The dates are the Cabinet Office's published 国民の祝日 for 2025–2027, including
振替休日 and 国民の休日. A year is added once its dates are published, not guessed from
the weekday rules, as the equinoxes and one-off moves are not predictable.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

import holidays

DayKind = Literal["workday", "weekend", "holiday"]

HOLIDAYS: dict[str, str] = {
    # 2025
    "2025-01-01": "元日",
    "2025-01-13": "成人の日",
    "2025-02-11": "建国記念の日",
    "2025-02-23": "天皇誕生日",
    "2025-02-24": "振替休日",
    "2025-03-20": "春分の日",
    "2025-04-29": "昭和の日",
    "2025-05-03": "憲法記念日",
    "2025-05-04": "みどりの日",
    "2025-05-05": "こどもの日",
    "2025-05-06": "振替休日",
    "2025-07-21": "海の日",
    "2025-08-11": "山の日",
    "2025-09-15": "敬老の日",
    "2025-09-23": "秋分の日",
    "2025-10-13": "スポーツの日",
    "2025-11-03": "文化の日",
    "2025-11-23": "勤労感謝の日",
    "2025-11-24": "振替休日",
    # 2026
    "2026-01-01": "元日",
    "2026-01-12": "成人の日",
    "2026-02-11": "建国記念の日",
    "2026-02-23": "天皇誕生日",
    "2026-03-20": "春分の日",
    "2026-04-29": "昭和の日",
    "2026-05-03": "憲法記念日",
    "2026-05-04": "みどりの日",
    "2026-05-05": "こどもの日",
    "2026-05-06": "振替休日",
    "2026-07-20": "海の日",
    "2026-08-11": "山の日",
    "2026-09-21": "敬老の日",
    "2026-09-22": "国民の休日",
    "2026-09-23": "秋分の日",
    "2026-10-12": "スポーツの日",
    "2026-11-03": "文化の日",
    "2026-11-23": "勤労感謝の日",
    # 2027
    "2027-01-01": "元日",
    "2027-01-11": "成人の日",
    "2027-02-11": "建国記念の日",
    "2027-02-23": "天皇誕生日",
    "2027-03-21": "春分の日",
    "2027-03-22": "振替休日",
    "2027-04-29": "昭和の日",
    "2027-05-03": "憲法記念日",
    "2027-05-04": "みどりの日",
    "2027-05-05": "こどもの日",
    "2027-07-19": "海の日",
    "2027-08-11": "山の日",
    "2027-09-20": "敬老の日",
    "2027-09-23": "秋分の日",
    "2027-10-11": "スポーツの日",
    "2027-11-03": "文化の日",
    "2027-11-23": "勤労感謝の日",
}
"""National holidays and the 振替休日 / 国民の休日 that belong to them."""

HOLIDAY_HINTS: dict[str, str] = {
    "元日": "初詣、新年问候、和家人或朋友轻松度过",
    "成人の日": "和已成年的朋友见面，或者安静地休息",
    "建国記念の日": "休息或轻松外出",
    "天皇誕生日": "轻松外出或休息",
    "春分の日": "扫墓、散步、感受季节",
    "昭和の日": "外出或休息",
    "憲法記念日": "休息或轻松外出",
    "みどりの日": "公园、自然、散步",
    "こどもの日": "和家人、孩子有关的事，或者休息",
    "海の日": "海边、水边，或者室内休息",
    "山の日": "山、郊外，或者休息",
    "敬老の日": "和家人、长辈有关的事",
    "秋分の日": "扫墓、散步、感受季节",
    "スポーツの日": "运动、看比赛，或者休息",
    "文化の日": "展览、书、电影、文化活动",
    "勤労感謝の日": "感谢、休息、和家人吃饭",
    "振替休日": "当作普通休息日，不要硬套节日活动",
    "国民の休日": "当作普通休息日，不要硬套节日活动",
}
"""What a holiday may be about, without inventing an original-story event."""


def holiday_name(day: date) -> str | None:
    """The day's Japanese holiday name, or None."""
    return dict(holidays_of(day.year)).get(day)


@lru_cache(maxsize=16)
def holidays_of(year: int) -> tuple[tuple[date, str], ...]:
    """The holidays of `year`, by date, each with its name. The published 2025–2027
    dictionary is used first; the library answers other years."""
    found = [
        (date.fromisoformat(day), name)
        for day, name in HOLIDAYS.items()
        if day.startswith(f"{year:04d}-")
    ]
    if found:
        return tuple(sorted(found))
    try:
        by_day = holidays.country_holidays("JP", years=year, language="ja")
    except (NotImplementedError, ValueError, OverflowError):
        return ()
    return tuple(sorted(by_day.items()))


def holiday_on(day: date) -> str | None:
    """The name of the holiday `day` is, if it is one."""
    return holiday_name(day)


def day_kind(day: date) -> DayKind:
    """Workday, weekend, or a Japanese holiday. A holiday takes precedence over a weekend."""
    if holiday_name(day) is not None:
        return "holiday"
    return "weekend" if day.weekday() >= 5 else "workday"


def holiday_hint(day: date) -> str | None:
    """What the day's holiday may be spent on, or None when it is not a holiday."""
    name = holiday_name(day)
    return None if name is None else HOLIDAY_HINTS.get(name)
