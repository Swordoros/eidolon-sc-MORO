"""What a group chat knows of now: its members, their day, the weather and the clock."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from eidolon.producer import effective_timezone
from eidolon.schedules import Schedule, Schedules, rest
from eidolon.store import Store
from eidolon.weather import Weather, Weathers, weather_line


@dataclass(frozen=True)
class GroupMemberNow:
    id: str
    name: str
    activity: str
    asleep: bool


@dataclass(frozen=True)
class GroupNow:
    """The shared present of one group: no producer state, only the group's own world."""

    group_id: str
    now: datetime
    timezone: str
    members: tuple[GroupMemberNow, ...]
    weather: Weather | None = None

    def lines(self) -> list[str]:
        local = self.now.astimezone(ZoneInfo(self.timezone))
        weekday = "一二三四五六日"[local.weekday()]
        lines = [
            f"现在是 {local.year}年{local.month}月{local.day}日（星期{weekday}）"
            f"{local:%H:%M}。",
        ]
        for member in self.members:
            lines.append(f"- {member.name}：{member.activity}")
        if self.weather is not None:
            lines.append(weather_line(self.weather))
        return lines


class GroupContext:
    """Read the group's present from the daily schedules and the weather."""

    def __init__(
        self, daily: Schedules | None = None, store: Store | None = None,
        weathers: Weathers | None = None,
    ) -> None:
        self._daily = daily
        self._store = store
        self._weathers = weathers

    async def read(
        self, user_id: str, group_id: str, members: Any, now: datetime
    ) -> GroupNow:
        timezone = "Asia/Shanghai"
        if self._store is not None:
            try:
                timezone = effective_timezone(await self._store.get_profile(user_id), None)
            except Exception:
                pass
        local = now.astimezone(ZoneInfo(timezone))
        states: list[GroupMemberNow] = []
        for member in members:
            activity, asleep = "现在没安排", False
            if self._daily is not None:
                try:
                    today = await self._daily.of(user_id, member.id, local.date())
                    yesterday = await self._daily.of(
                        user_id, member.id, local.date() - timedelta(days=1)
                    )
                    activity = _activity(today, local)
                    asleep = rest(today, yesterday, local) == "asleep"
                except Exception:
                    pass
            states.append(GroupMemberNow(member.id, member.name, activity, asleep))
        weather = None
        if self._weathers is not None:
            try:
                weather = await self._weathers.at(now)
            except Exception:
                pass
        return GroupNow(group_id, now, timezone, tuple(states), weather)


def _activity(today: Schedule | None, local: datetime) -> str:
    if today is None or not today.slots:
        return "现在没安排"
    current = today.now(local.time())
    if current is not None and current.what:
        return f"现在在{current.what}"
    if local.time() < today.slots[0].start:
        return "今天的安排还没开始"
    return "现在没安排"
