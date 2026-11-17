"""The recent relationship between a character and the producer, written once a day.

It is a summary, not a score: it never changes affinity, and the affinity in effect when a
turn is taken always wins over it. Every day at 03:00 in the producer's time zone it is
written from her memories, the memories she could recall, and everything said with her the
day before: private turns, group chats she was in, and calls. A day with nothing said keeps
a neutral summary rather than carrying yesterday's mood forward. The diary of the day just
summarised is one of the materials the summary is written from: trust and close supply its
normal pages, intimate also the private one. The summary is what the relationship prompt
carries; the diary is not added to the prompt on its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from eidolon.affinity import Status, Tier
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.llm import ChatMessage
from eidolon.live_config import LiveConfig
from eidolon.memory import Memory, Memories
from eidolon.store import Store
from eidolon.producer import effective_timezone

log = logging.getLogger(__name__)

TICK = timedelta(minutes=5)
START = time(3)
"""When the producer's day's summary is written."""
MAX_TRANSCRIPT = 24_000
"""How much of yesterday's conversation is shown to the summarizing model, in characters."""
MAX_MEMORY_LINES = 80
"""How many memories are shown to the summarizing model."""
MAX_DIARY_CHARS = 600
"""How much of one diary page is put into the prompt at most."""

State = Literal["cooling", "tense", "repairing", "neutral", "warm", "affectionate", "romantic"]
STATES: tuple[State, ...] = (
    "cooling", "tense", "repairing", "neutral", "warm", "affectionate", "romantic",
)
"""The states a relationship can be in, from the most distant to the closest."""

_ALLOWED: dict[Tier, frozenset[State]] = {
    "cold": frozenset({"cooling", "tense", "repairing", "neutral"}),
    "distant": frozenset({"cooling", "tense", "repairing", "neutral"}),
    "trust": frozenset({"neutral", "warm", "repairing"}),
    "close": frozenset({"neutral", "warm", "affectionate", "repairing"}),
    "intimate": frozenset({"neutral", "warm", "affectionate", "romantic", "repairing"}),
}
"""What a relationship may be in a tier; affinity is the hard edge of this section."""

_LABELS = (
    "关系状态", "最近气氛", "对制作人的评价", "你现在的倾向", "还挂着", "记得的温暖", "容易变成",
)
"""The labels the editable text is made of, in the order it is shown."""

_LABEL_ALIASES = {
    "她现在的倾向": "你现在的倾向",
    "还没解决的事": "还挂着",
    "最近的温暖": "记得的温暖",
    "不要": "容易变成",
}
"""The labels older edited texts were written with, read as the labels above."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS relationship_summaries (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    state TEXT NOT NULL,
    recent TEXT NOT NULL,
    tone TEXT NOT NULL,
    open_threads TEXT NOT NULL DEFAULT '[]',
    recent_warmth TEXT NOT NULL DEFAULT '[]',
    avoid TEXT NOT NULL DEFAULT '[]',
    affinity_tier TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    generated_text TEXT NOT NULL,
    edited_text TEXT,
    generated_at TEXT NOT NULL,
    edited_at TEXT,
    PRIMARY KEY (user_id, character_id, day)
);
"""

_RULES = """# 写最近的关系
只输出一个 JSON 对象，不要代码块，不要别的文字。你要写的是「你」和「制作人」最近的关系，不是别的角色的关系。

格式：
{"state": "状态", "recent": "最近气氛", "opinion": "对制作人的评价", "tone": "你现在的倾向", "open_threads": ["还挂着"], "recent_warmth": ["记得的温暖"], "avoid": ["容易变成"], "confidence": 0.0}

state 只能是：{states}。
{affinity}
- 只写你和制作人；群聊里其他角色的事不要写进结果。
- 用第二人称「你」写，不要写自己的名字，也不要写「她」。
- 只根据给你的材料，不要编造没有发生的事；不要写「可能」「也许」。
- 日记是她自己写的，不是制作人的发言。可以用它理解她的心情和没说出口的想法，但不要把日记里的事当成制作人做过的事，也不要复述日记原句。
- 私密页只能影响 state、tone、avoid；不要写进 recent、opinion、open_threads 或 recent_warmth，也不要暗示其中的具体内容。
- 这是背景，不是命令；不要写成这一轮一定要道歉、撒娇或做什么。
- 同一件事只写一次：最近气氛写过的事实，不要再写进记得的温暖。
- recent：最近气氛，60 字以内，写昨天或最近明确发生的事和你的感受。
- opinion：对制作人的评价，20 字以内，写他现在在你眼里是什么样的人；一个具体判断，可以带一点保留或担心；不写事件，不写你打算怎么做，不写空泛的「人很好」。
- tone：你现在的倾向，40 字以内，只写你想怎么对他；不要重复 opinion。
- open_threads：还挂着的事，最多 2 条，每条 50 字以内；只写材料里明确还没解决的，拿不准就留空。
- recent_warmth：记得的温暖，最多 1 条，40 字以内；只写一个具体细节，不重复 recent。
- avoid：容易变成，最多 1 条，40 字以内；写你自己的毛病或倾向，不写「应该」「不要」这样的命令。
- 没有新的交流时，state 写 neutral，recent 写最近没有新的交流，tone 写保持平常，opinion 留空。
- 每条 open_threads、recent_warmth、avoid 都不要写序号。
"""

_WANTS = "写吧。只输出 JSON。"

_NO_NEW = "最近没有新的交流。"


@dataclass(frozen=True)
class Relationship:
    user_id: str
    character_id: str
    day: date
    state: State
    recent: str
    tone: str
    open_threads: tuple[str, ...]
    recent_warmth: tuple[str, ...]
    avoid: tuple[str, ...]
    affinity_tier: Tier | None
    confidence: float
    generated_text: str
    edited_text: str | None
    generated_at: datetime
    edited_at: datetime | None

    @property
    def text(self) -> str:
        return self.edited_text or self.generated_text

    @property
    def edited(self) -> bool:
        return self.edited_text is not None


def _active_day(local: datetime) -> date:
    """The relationship in effect at `local`: the one written at 03:00 until 03:00 the next
    day. Before 03:00 the previous day's still applies."""
    return local.date() - timedelta(days=1) if local.time() < START else local.date()


def _allowed(tier: Tier | None, cooling: bool) -> frozenset[State]:
    if cooling:
        return frozenset({"cooling"})
    if tier is None:
        return frozenset({"neutral", "warm", "repairing"})
    return _ALLOWED[tier]


def _render(state: State, recent: str, opinion: str, tone: str, threads: Sequence[str],
            warmth: Sequence[str], avoid: Sequence[str]) -> str:
    lines = [f"关系状态：{state}", f"最近气氛：{recent or _NO_NEW}"]
    if opinion:
        lines.append(f"对制作人的评价：{opinion}")
    if tone:
        lines.append(f"你现在的倾向：{tone}")
    if threads:
        lines.append(f"还挂着：{'；'.join(threads)}")
    if warmth:
        lines.append(f"记得的温暖：{'；'.join(warmth)}")
    if avoid:
        lines.append(f"容易变成：{'；'.join(avoid)}")
    return "\n".join(lines)


def _parse_text(text: str) -> dict[str, str | list[str]]:
    """Read the editable labelled text; raises ValueError when it is not that format."""
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    if any(line.startswith("# 与制作人最近的关系") for line in lines):
        lines = [line for line in lines if not line.startswith("# 与制作人最近的关系")]
    found: dict[str, str | list[str]] = {}
    for line in lines:
        if not line:
            continue
        label, sep, value = line.partition("：")
        label = _LABEL_ALIASES.get(label, label)
        if not sep or label not in _LABELS:
            raise ValueError(f"无法识别的一行：{line}")
        if label in found:
            raise ValueError(f"「{label}」出现了两次")
        value = value.strip()
        if not value:
            raise ValueError(f"「{label}」不能为空")
        if label in ("还挂着", "记得的温暖", "容易变成"):
            found[label] = [part.strip() for part in value.split("；") if part.strip()]
        else:
            found[label] = value
    if "关系状态" not in found:
        raise ValueError("缺少「关系状态：」")
    if "最近气氛" not in found:
        raise ValueError("缺少「最近气氛：」")
    return found


def _from_text(text: str, tier: Tier | None, cooling: bool) -> dict[str, object]:
    parsed = _parse_text(text)
    state = parsed["关系状态"]
    if state not in STATES:
        raise ValueError(f"关系状态只能是：{'、'.join(STATES)}")
    if state not in _allowed(tier, cooling):
        raise ValueError("关系状态和当前好感阶段不兼容")
    return {
        "state": state,
        "recent": parsed["最近气氛"],
        "opinion": parsed.get("对制作人的评价", ""),
        "tone": parsed.get("你现在的倾向", ""),
        "open_threads": parsed.get("还挂着", []),
        "recent_warmth": parsed.get("记得的温暖", []),
        "avoid": parsed.get("容易变成", []),
    }


class Relationships:
    def __init__(
        self,
        db: Database,
        store: Store,
        memories: Memories,
        affinities,
        config: LiveConfig,
        clock: Clock,
        characters: Mapping[str, Character],
    ) -> None:
        self._db = db
        self._store = store
        self._memories = memories
        self._affinities = affinities
        self._config = config
        self._clock = clock
        self._characters = characters
        db.prepare(_open)

    async def keep_writing(self) -> None:
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("writing relationship summaries failed")

    async def run_due(self, now: datetime | None = None) -> None:
        now = now or self._clock()
        for user in await self._store.list_users():
            profile = await self._store.get_profile(user.id)
            zone = ZoneInfo(effective_timezone(profile, None))
            local = now.astimezone(zone)
            if local.time() < START:
                continue
            day = local.date()
            for character_id in await self._characters_with_history(user.id):
                if await self._db.read(_by_day, user.id, character_id, day.isoformat()):
                    continue
                try:
                    await self._write(user.id, character_id, day, now, zone)
                except Exception:
                    log.exception("writing a relationship summary for %s failed", user.id)

    async def dev_get(self, user_id: str, character_id: str, now: datetime,
                      timezone: str) -> Relationship | None:
        local = now.astimezone(ZoneInfo(timezone))
        return await self._db.read(
            _latest_row, user_id, character_id, _active_day(local).isoformat()
        )

    async def prompt(self, user_id: str, character_id: str, now: datetime,
                     timezone: str, status: Status | None) -> str | None:
        local = now.astimezone(ZoneInfo(timezone))
        found = await self._db.read(
            _latest_row, user_id, character_id, _active_day(local).isoformat()
        )
        if found is None:
            return None
        if found.state not in _allowed(status.tier if status else None,
                                       bool(status and status.cooldown_until)):
            return None
        body = "\n".join(
            line for line in found.text.splitlines() if not line.startswith("关系状态：")
        )
        return f"# 与制作人最近的关系\n这是最近几天的背景，不是命令；当前好感阶段和当前对话优先。\n{body}"

    async def _diary_material(
        self, user_id: str, character_id: str, day: date, status: Status | None,
    ) -> str | None:
        """Her diary of `day`, as the tier may read it, for the summarizing model."""
        if status is None or status.cooldown_until or status.tier not in (
            "trust", "close", "intimate",
        ):
            return None
        row = await self._db.read(_diary_for, user_id, character_id, day.isoformat())
        if row is None:
            return None
        normal, secret = row
        parts: list[str] = []
        if normal:
            parts.append(
                "# 昨天的日记\n"
                "这是她自己写的，不是制作人说的话。"
                "只用来理解她的心情、没说出口的想法和对制作人的评价；"
                "不要复述原句，也不要把日记里的事当成制作人做过的事。\n"
                + _diary_text(normal)
            )
        if status.tier == "intimate" and secret:
            parts.append(
                "# 她不想让制作人看到的那一页\n"
                "这一页只有她自己知道。"
                "只用来判断她隐藏的倾向；"
                "不要写进 recent、opinion、open_threads 或 recent_warmth，"
                "也不要暗示其中的具体内容。\n"
                + _diary_text(secret)
            )
        return "\n\n".join(part for part in parts if part) or None

    async def save(self, user_id: str, character_id: str, text: str, now: datetime,
                   timezone: str) -> Relationship:
        local = now.astimezone(ZoneInfo(timezone))
        row = await self._db.read(
            _latest_row, user_id, character_id, _active_day(local).isoformat()
        )
        if row is None:
            raise ValueError("当前没有关系总结")
        status = await self._affinities.status(user_id, character_id)
        fields = _from_text(text, status.tier if status else None,
                            bool(status and status.cooldown_until))
        await self._db.write(
            _save_edit, user_id, character_id, row.day.isoformat(), text, fields,
            now.astimezone(UTC).isoformat(),
        )
        saved = await self._db.read(_by_day_row, user_id, character_id, row.day.isoformat())
        assert saved is not None
        return saved

    async def regenerate(self, user_id: str, character_id: str, now: datetime,
                         timezone: str) -> Relationship:
        local = now.astimezone(ZoneInfo(timezone))
        day = _active_day(local)
        await self._write(user_id, character_id, day, now, ZoneInfo(timezone), force=True)
        found = await self._db.read(_by_day_row, user_id, character_id, day.isoformat())
        assert found is not None
        return found

    async def _write(self, user_id: str, character_id: str, day: date, now: datetime,
                     zone: ZoneInfo, force: bool = False) -> None:
        character = self._characters[character_id]
        status = await self._affinities.status(user_id, character_id)
        start = datetime.combine(day - timedelta(days=1), time(0), zone)
        end = datetime.combine(day, time(0), zone)
        transcript = await self._transcript(user_id, character_id, start, end, zone)
        diary = await self._diary_material(
            user_id, character_id, day - timedelta(days=1), status
        )
        if not transcript:
            fields = {
                "state": "neutral", "recent": _NO_NEW, "opinion": "", "tone": "保持平常",
                "open_threads": [], "recent_warmth": [], "avoid": [],
                "confidence": 1.0,
            }
        else:
            memories = await self._memory_lines(user_id, character_id, day, zone)
            previous = await self._previous(user_id, character_id, day)
            fields = await self._ask(character, status, memories, previous, transcript, diary)
        state = fields["state"]
        if state not in _allowed(status.tier if status else None,
                                  bool(status and status.cooldown_until)):
            state = "neutral"
            fields["recent"] = "最近的气氛和当前好感阶段不太一致，保持克制。"
            fields["opinion"] = ""
            fields["tone"] = "保持平常"
            fields["open_threads"] = []
            fields["recent_warmth"] = []
            fields["avoid"] = ["容易表现得比当前关系更亲近"]
        text = _render(
            state, str(fields["recent"]), str(fields.get("opinion", "")), str(fields["tone"]),
            list(fields["open_threads"]), list(fields["recent_warmth"]),
            list(fields["avoid"]),
        )
        await self._db.write(
            _keep, user_id, character_id, day.isoformat(), state,
            str(fields["recent"]), str(fields["tone"]),
            json.dumps(fields["open_threads"], ensure_ascii=False),
            json.dumps(fields["recent_warmth"], ensure_ascii=False),
            json.dumps(fields["avoid"], ensure_ascii=False),
            status.tier if status else None, float(fields.get("confidence", 0.5)),
            text, now.astimezone(UTC).isoformat(), force,
        )

    async def _ask(self, character: Character, status: Status | None,
                   memories: Sequence[str], previous: str | None,
                   transcript: str, diary: str | None = None) -> dict[str, object]:
        tier = status.tier if status else None
        cooling = bool(status and status.cooldown_until)
        affinity = (
            f"当前好感阶段：{tier or '未开启'}；"
            + ("正在冷却，只能写 cooling。" if cooling else "")
            + f"只允许这些 state：{'、'.join(sorted(_allowed(tier, cooling)))}。"
        )
        material = [
            f"# 角色\n{character.name}",
            f"# 关系约束\n{affinity}",
            f"# 昨天之前的关系\n{previous or '（没有）'}",
            "# 近三天她自己的记忆\n" + ("\n".join(memories) if memories else "（没有）"),
            *([diary] if diary else []),
            "# 昨天与制作人的全部交流\n" + transcript,
        ]
        rules = _RULES.replace("{states}", "、".join(STATES)).replace("{affinity}", affinity)
        messages = [
            ChatMessage("system", rules),
            ChatMessage("user", "\n\n".join(material) + "\n\n" + _WANTS),
        ]
        lease = self._config.lease()
        try:
            chunks: list[str] = []
            async for item in lease.keys.memory_llm.stream(messages):
                if isinstance(item, str):
                    chunks.append(item)
        finally:
            lease.release()
        answer = "".join(chunks).strip()
        answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer, flags=re.IGNORECASE)
        try:
            raw = json.loads(answer)
        except json.JSONDecodeError:
            log.warning("a relationship summary was not JSON; keeping it neutral")
            return {
                "state": "neutral", "recent": _NO_NEW, "opinion": "", "tone": "保持平常",
                "open_threads": [], "recent_warmth": [], "avoid": [], "confidence": 0.0,
            }
        return _checked(raw)

    async def _transcript(self, user_id: str, character_id: str, start: datetime,
                          end: datetime, zone: ZoneInfo) -> str:
        rows = await self._db.read(
            _private_messages, user_id, character_id,
            start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat(),
        )
        groups = await self._db.read(
            _group_messages, user_id, character_id,
            start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat(),
        )
        lines: list[tuple[str, str]] = []
        for created, role, text, ja, zh in rows:
            when = datetime.fromisoformat(created).astimezone(zone).strftime("%H:%M")
            said = zh or text or ja or ""
            who = "制作人" if role == "user" else "你"
            lines.append((created, f"[{when}] {who}：{said}"))
        for created, speaker, text, zh in groups:
            when = datetime.fromisoformat(created).astimezone(zone).strftime("%H:%M")
            name = self._characters[speaker].name if speaker in self._characters else speaker
            lines.append((created, f"[{when}] 群聊/{name}：{zh or text or ''}"))
        if not lines:
            return ""
        lines.sort(key=lambda pair: pair[0])
        text = "\n".join(line for _, line in lines)
        return text[-MAX_TRANSCRIPT:]

    async def _memory_lines(self, user_id: str, character_id: str, day: date,
                            zone: ZoneInfo) -> list[str]:
        entries = await self._memories.entries(user_id)
        start = datetime.combine(day - timedelta(days=3), time(0), zone)
        yesterday = datetime.combine(day - timedelta(days=1), time(0), zone)
        end = datetime.combine(day, time(0), zone)
        visible = await self._memories.visible(user_id, character_id)
        chosen: dict[int, Memory] = {}
        for memory in visible:
            when = datetime.fromisoformat(memory.created_at)
            if yesterday <= when < end:
                chosen[memory.id] = memory
        for memory in entries:
            if memory.source_character_id != character_id or memory.superseded_by is not None:
                continue
            when = datetime.fromisoformat(memory.created_at)
            if start <= when < end:
                chosen[memory.id] = memory
        lines = []
        for memory in sorted(chosen.values(), key=lambda m: m.created_at)[-MAX_MEMORY_LINES:]:
            source = "你自己" if memory.source_character_id == character_id else "其他人"
            lines.append(f"- [{memory.scope}/{memory.subject}/{source}] {memory.text}")
        return lines

    async def _previous(self, user_id: str, character_id: str, day: date) -> str | None:
        row = await self._db.read(
            _by_day_row, user_id, character_id, (day - timedelta(days=1)).isoformat()
        )
        return None if row is None else row.text

    async def _characters_with_history(self, user_id: str) -> list[str]:
        return await self._db.read(_characters_with_history, user_id)


def _checked(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("relationship JSON is not an object")
    state = raw.get("state")
    if state not in STATES:
        state = "neutral"

    def clip(value: object, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit].strip()

    def strings(key: str, limit: int, most: int) -> list[str]:
        value = raw.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [clip(item, limit) for item in value if clip(item, limit)][:most]

    return {
        "state": state,
        "recent": clip(raw.get("recent"), 60) or _NO_NEW,
        "opinion": clip(raw.get("opinion"), 20),
        "tone": clip(raw.get("tone"), 40),
        "open_threads": strings("open_threads", 50, 2),
        "recent_warmth": strings("recent_warmth", 40, 1),
        "avoid": strings("avoid", 40, 1),
        "confidence": min(max(float(raw.get("confidence") or 0.5), 0.0), 1.0),
    }


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _by_day(db: sqlite3.Connection, user_id: str, character_id: str, day: str) -> bool:
    return db.execute(
        "SELECT 1 FROM relationship_summaries WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day),
    ).fetchone() is not None


def _by_day_row(db: sqlite3.Connection, user_id: str, character_id: str, day: str):
    row = db.execute(
        "SELECT day, state, recent, tone, open_threads, recent_warmth, avoid,"
        " affinity_tier, confidence, generated_text, edited_text, generated_at, edited_at"
        " FROM relationship_summaries WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day),
    ).fetchone()
    return _row(user_id, character_id, row) if row else None


def _latest_row(db: sqlite3.Connection, user_id: str, character_id: str, day: str):
    row = db.execute(
        "SELECT day, state, recent, tone, open_threads, recent_warmth, avoid,"
        " affinity_tier, confidence, generated_text, edited_text, generated_at, edited_at"
        " FROM relationship_summaries WHERE user_id = ? AND character_id = ? AND day <= ?"
        " ORDER BY day DESC LIMIT 1",
        (user_id, character_id, day),
    ).fetchone()
    return _row(user_id, character_id, row) if row else None


def _row(user_id: str, character_id: str, row) -> Relationship:
    (day, state, recent, tone, threads, warmth, avoid, tier, confidence,
     generated, edited, generated_at, edited_at) = row
    return Relationship(
        user_id, character_id, date.fromisoformat(day), state, recent, tone,
        tuple(json.loads(threads)), tuple(json.loads(warmth)), tuple(json.loads(avoid)),
        tier, confidence, generated, edited, datetime.fromisoformat(generated_at),
        datetime.fromisoformat(edited_at) if edited_at else None,
    )


def _keep(db: sqlite3.Connection, user_id: str, character_id: str, day: str,
          state: str, recent: str, tone: str, threads: str, warmth: str, avoid: str,
          tier: str | None, confidence: float, text: str, generated_at: str,
          force: bool) -> None:
    if force:
        db.execute(
            "DELETE FROM relationship_summaries WHERE user_id = ? AND character_id = ? AND day = ?",
            (user_id, character_id, day),
        )
    db.execute(
        "INSERT OR IGNORE INTO relationship_summaries"
        " (user_id, character_id, day, state, recent, tone, open_threads, recent_warmth,"
        " avoid, affinity_tier, confidence, generated_text, generated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, character_id, day, state, recent, tone, threads, warmth, avoid,
         tier, confidence, text, generated_at),
    )


def _save_edit(db: sqlite3.Connection, user_id: str, character_id: str, day: str,
               text: str, fields: Mapping[str, object], edited_at: str) -> None:
    db.execute(
        "UPDATE relationship_summaries SET edited_text = ?, edited_at = ?,"
        " state = ?, recent = ?, tone = ?, open_threads = ?, recent_warmth = ?, avoid = ?"
        " WHERE user_id = ? AND character_id = ? AND day = ?",
        (
            text, edited_at, fields["state"], fields["recent"], fields["tone"],
            json.dumps(fields["open_threads"], ensure_ascii=False),
            json.dumps(fields["recent_warmth"], ensure_ascii=False),
            json.dumps(fields["avoid"], ensure_ascii=False),
            user_id, character_id, day,
        ),
    )


def _private_messages(db: sqlite3.Connection, user_id: str, character_id: str,
                      start: str, end: str):
    return db.execute(
        "SELECT m.created_at, m.role, m.text, m.ja, m.zh FROM messages m"
        " JOIN conversations c ON c.id = m.conversation_id"
        " WHERE c.user_id = ? AND c.character_id = ?"
        " AND m.created_at >= ? AND m.created_at < ? ORDER BY m.created_at",
        (user_id, character_id, start, end),
    ).fetchall()


def _group_messages(db: sqlite3.Connection, user_id: str, character_id: str,
                    start: str, end: str):
    return db.execute(
        "SELECT m.created_at, m.speaker, m.text, m.zh FROM group_messages m"
        " JOIN group_topics t ON t.id = m.topic_id"
        " JOIN group_members gm ON gm.group_id = t.group_id"
        " WHERE m.user_id = ? AND gm.character_id = ?"
        " AND m.created_at >= ? AND m.created_at < ? ORDER BY m.created_at",
        (user_id, character_id, start, end),
    ).fetchall()


def _characters_with_history(db: sqlite3.Connection, user_id: str) -> list[str]:
    return [
        row[0] for row in db.execute(
            "SELECT DISTINCT character_id FROM conversations WHERE user_id = ?"
            " UNION SELECT DISTINCT gm.character_id FROM group_members gm"
            " JOIN group_chats g ON g.id = gm.group_id WHERE g.user_id = ?",
            (user_id, user_id),
        )
    ]


def _diary_for(
    db: sqlite3.Connection, user_id: str, character_id: str, day: str,
) -> tuple[list[dict], list[dict]] | None:
    row = db.execute(
        "SELECT segments, secret FROM diary_entries"
        " WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row[0]), json.loads(row[1])


def _diary_text(segments: Sequence[dict]) -> str:
    text = "\n".join(
        str(segment["zh"]).strip()
        for segment in segments
        if str(segment.get("zh", "")).strip()
    )
    return text[:MAX_DIARY_CHARS]

