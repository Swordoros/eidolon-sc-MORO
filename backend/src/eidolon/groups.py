"""自主群聊（多智能体）：一个用户可以开很多个群，每个群有自己的名字、成员和历史。

一个群由一条话题开始（某位成员随口抛出），之后每位成员按自己与话题的相关度决定说还是不说：
相关的可以连着说，不相关的沉默，没人接就冷场。随着对话推进，每个人「这跟我没关系、不必回」
的阈值逐步抬高，她「说过的越多、相关度越低」，所以每段对话都会自己收敛、必然结束（`MAX_TURNS`
是兜底）。她「顺带提到没在场的人」的概率也随轮次降低。

群与群之间彼此独立：一个群的历史只有它自己的话题和台词，解散一个群不会影响别的群。

对话在后台一句一句地生成，每句之间隔 `delay`（默认 10 秒到 90 秒），所以用户随时可以插话。

约每 `TOPIC_INTERVAL`（4 小时）由调度器给开着的群抛一个新话题；关掉开关只是停止调度，
已经写下的群聊、话题和消息都留着。解散（`dissolve`）把群标记为已解散：它从列表里消失，
不再有新话题，历史也不再显示（数据留着）。

用户发言与成员地位相同：插进当前对话，同样按相关度被回应，不是「必须每个人回复」的焦点。

她说的每句是日语原文 + 中文字幕，和私聊一样；提示词只带她自己的好感度档位，不把其他成员的好感度给她看。

一段对话结束后，把这段群聊里用户透露的事写进每个参与成员的记忆（`memories` 表，
`conversation_id` 为空），私聊检索记忆时同样能查到。
"""

import asyncio
import json
import logging
import random
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Sequence
from typing import Any
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from eidolon.affinity import Affinities, Judged, tier_prompt
from eidolon.affinity_judge import AffinityJudge
from eidolon.group_affinity import step as group_step
from eidolon.group_affinity import targets as group_targets
from eidolon.characters import Character, chinese_name_map
from eidolon.weather import Weathers
from eidolon.world import World
from eidolon.group_context import GroupContext
from eidolon.group_prompt import history_block
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.engine import _dump_prompt
from eidolon.llm import ChatMessage
from eidolon.memory import Memory, Memories, NewMemory, TIERS, _INSTRUCTIONS, parse_memories
from eidolon.own_keys import DeployerLease
from eidolon.photo_prompt import COMPOSITIONS, UNPEOPLED, read_picture, tag_fields
from eidolon.photos import Photos
from eidolon.producer import effective_timezone, local_date
from eidolon.schedules import Schedules, rest
from eidolon.store import Message, Store, read_profile

log = logging.getLogger(__name__)

TICK = timedelta(minutes=1)
"""How often the scheduler looks whose group is due a new topic."""

TOPIC_INTERVAL = timedelta(hours=8)
"""How long a group waits between one topic and the next: eight hours."""

TOPIC_GAP = (timedelta(hours=8), timedelta(hours=8))
"""The cooldown after a topic before the group raises another: eight hours."""

MAX_TURNS = 30
"""The most lines one topic gets before it is closed anyway: the ceiling, not the usual end."""

DEFAULT_MEMBERS = 4
"""How many members a group gets when none are picked."""

DEFAULT_DELAY = (10.0, 300.0)
"""Seconds between one line and the next: slow enough to feel live, and to leave room to cut in.
Each member has her own timer, so several may be waiting to answer at once."""

REPLY_DELAY = (8.0, 60.0)
"""The shorter timer for the members answering the user's own line: a reply to you should feel
like an answer, not like the group drifting on."""

BASE_THRESHOLD = 0.28
"""How relevant a member must be to speak at the topic's first line."""

CONTINUE_THRESHOLD = 0.72
"""How willing a member must be to answer the user's line in a topic already wound down: high,
so a stray remark does not restart a finished conversation."""

MENTION_BONUS = 0.60
"""How much naming (or plainly addressing) a member in the user's line adds to her willingness
to answer it: enough that being asked brings her in even when the line alone would not."""

THRESHOLD_STEP = 0.028
"""How much that bar rises for each line said: the longer it goes, the less anyone has to say."""

SPEAK_DECAY = 0.054
"""How much a member's relevance to the topic drops for each line she has said: having said
her piece, she has less left to add, so the conversation winds down instead of looping."""

BASE_MENTION = 0.45
"""How likely a speaker is to be told it may bring up someone not in the conversation."""

MENTION_STEP = 0.09
"""How much that falls for each line: late in a topic, no one drags in outsiders."""

MAX_LINE_CHARS = 220
"""A line longer than this is cut: the models are told to keep it short."""

MAX_NAME_CHARS = 40
"""A group's name longer than this is cut."""

RECENT_LINES = 8
"""How many of the latest lines a speaker is shown: enough to answer the conversation, short
enough not to drown it."""

PAST_LINES = 14
"""How many of the lines of the same group's earlier topics a speaker is shown: the group's
own history, which she can pick up or refer back to."""

TOPIC_RECALL = 12
"""How many of the group's earlier topics its next one is told about, to stay off them.

Without this the only material a topic had was her character sheet, and the group went round
the same three hooks for days (`.scratch/nsfw/`)."""

GROUP_OWN_MEMORIES = 3
"""How many of her own memories a speaker is shown, in a topic or a reply."""

GROUP_PUBLIC_MEMORIES = 1
"""How many shared memories, not her own, a speaker is shown besides those."""

HISTORY_TOPICS = 50
"""How many topics the page is given: the whole readable history of the group."""

SHOWN_MEMORIES = 10
"""How many of the user's memories the extractor is shown to avoid writing them again."""

MAX_MEMORY_CHARS = 200
"""A memory longer than this is cut (`memory.MAX_MEMORY_LENGTH`)."""

USER_SPEAKER = "user"
"""The speaker of a line the user said."""

QUIET = (clock_time(1), clock_time(7))
"""The producer's hours of quiet: from the first to the second nobody raises a new topic in
the group on her own (a conversation the user starts still answers)."""

QUIET_ZONE = ZoneInfo("Asia/Shanghai")
"""The producer's own clock, which the quiet hours are read in."""

_JA = re.compile(r"<ja>(.*?)</ja>", re.S)
_ZH = re.compile(r"<zh>(.*?)</zh>", re.S)

_SCHEMA = """
-- One row per group chat: its name, whether it still runs, when it last raised a topic, and
-- when it was dissolved (dissolved groups are hidden, their history kept).
CREATE TABLE IF NOT EXISTS group_chats (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    is_on INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_topic_at TEXT,
    next_topic_at TEXT,
    dissolved_at TEXT,
    seen_at TEXT
);
CREATE INDEX IF NOT EXISTS group_chats_by_user ON group_chats (user_id, created_at);
-- Who is in each group.
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL REFERENCES group_chats(id),
    character_id TEXT NOT NULL,
    PRIMARY KEY (group_id, character_id)
);
-- One row per topic raised (a conversation): what was said to raise it, by whom, and when.
CREATE TABLE IF NOT EXISTS group_topics (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES group_chats(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    raised_by TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS group_topics_by_group ON group_topics (user_id, created_at);
-- Every line of every topic.
CREATE TABLE IF NOT EXISTS group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT NOT NULL REFERENCES group_topics(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id),
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    zh TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS group_messages_by_topic ON group_messages (topic_id, id);
CREATE TABLE IF NOT EXISTS group_photos (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES group_chats(id),
    topic_id TEXT NOT NULL REFERENCES group_topics(id),
    message_id INTEGER NOT NULL UNIQUE REFERENCES group_messages(id),
    kind TEXT NOT NULL CHECK (kind IN ('topic', 'request')),
    tag TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'ready', 'failed')),
    reason TEXT,
    image BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS group_photos_by_group ON group_photos (group_id);
CREATE INDEX IF NOT EXISTS group_photos_by_topic ON group_photos (topic_id);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _at_quiet(at: datetime, zone_name: str) -> bool:
    """Whether it is the producer's quiet hours now (1:00–7:00, their own clock)."""
    local = at.astimezone(ZoneInfo(zone_name)).time()
    return QUIET[0] <= local < QUIET[1]


def _seven_am(now: datetime, zone_name: str) -> str:
    """The next 7 in the morning, in the producer's own clock, as ISO in UTC: when a group
    whose members are all asleep next raises a topic."""
    local = now.astimezone(ZoneInfo(zone_name))
    at = local.replace(hour=7, minute=0, second=0, microsecond=0)
    if local >= at:
        at += timedelta(days=1)
    return at.astimezone(UTC).isoformat()


def _soon_topic_at() -> str:
    """Eight hours from now: the cooldown before a group raises its next topic."""
    return (datetime.now(timezone.utc) + TOPIC_INTERVAL).isoformat()


def _open(conn: sqlite3.Connection) -> None:
    """Make the tables, and bring a group chat made before there could be several over: its one
    group becomes a group of its own, named 群聊, with its members and topics."""
    conn.executescript(_SCHEMA)
    if "group_id" not in _columns(conn, "group_members"):
        conn.execute("ALTER TABLE group_members RENAME TO group_members_old")
        conn.execute(
            "CREATE TABLE group_members (group_id TEXT NOT NULL, character_id TEXT NOT NULL,"
            " PRIMARY KEY (group_id, character_id))"
        )
        conn.execute(
            "INSERT INTO group_members (group_id, character_id)"
            " SELECT user_id, character_id FROM group_members_old"
        )
        conn.execute("DROP TABLE group_members_old")
    if "group_id" not in _columns(conn, "group_topics"):
        conn.execute("ALTER TABLE group_topics ADD COLUMN group_id TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE group_topics SET group_id = user_id")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS group_topics_by_group_id"
        " ON group_topics (group_id, created_at)"
    )
    if "memory_cursor" not in _columns(conn, "group_topics"):
        conn.execute("ALTER TABLE group_topics ADD COLUMN memory_cursor INTEGER NOT NULL DEFAULT 0")
        # Topics made before the cursor existed start after their last message, so old
        # history is not extracted a second time.
        conn.execute(
            "UPDATE group_topics SET memory_cursor = COALESCE("
            "(SELECT MAX(id) FROM group_messages WHERE topic_id = group_topics.id), 0)"
        )
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'groups'"
    ).fetchone():
        conn.execute(
            "INSERT OR IGNORE INTO group_chats (id, user_id, name, is_on, created_at, last_topic_at)"
            " SELECT user_id, user_id, '群聊', is_on, created_at, last_topic_at FROM groups"
        )
        conn.execute("DROP TABLE groups")
    if "zh" not in _columns(conn, "group_messages"):
        conn.execute("ALTER TABLE group_messages ADD COLUMN zh TEXT NOT NULL DEFAULT ''")
    if "seen_at" not in _columns(conn, "group_chats"):
        conn.execute("ALTER TABLE group_chats ADD COLUMN seen_at TEXT")
    if "next_topic_at" not in _columns(conn, "group_chats"):
        conn.execute("ALTER TABLE group_chats ADD COLUMN next_topic_at TEXT")
    # A group made before the random gap existed gets its own next time, so a batch of them
    # does not all speak at the same minute.
    rows = conn.execute(
        "SELECT id FROM group_chats WHERE next_topic_at IS NULL AND dissolved_at IS NULL"
    ).fetchall()
    for (group_id,) in rows:
        conn.execute(
            "UPDATE group_chats SET next_topic_at = ? WHERE id = ?",
            (_soon_topic_at(), group_id),
        )
    # A group whose next topic was scheduled under the old 2–6 hour gap waits the new
    # eight-hour cooldown from its last topic.
    rows = conn.execute(
        "SELECT id, last_topic_at, next_topic_at FROM group_chats"
        " WHERE dissolved_at IS NULL AND last_topic_at IS NOT NULL"
        " AND next_topic_at IS NOT NULL"
    ).fetchall()
    for group_id, last_at, next_at in rows:
        try:
            due = datetime.fromisoformat(last_at) + TOPIC_INTERVAL
            if datetime.fromisoformat(next_at) >= due:
                continue
        except ValueError:
            continue
        conn.execute(
            "UPDATE group_chats SET next_topic_at = ? WHERE id = ?",
            (due.isoformat(), group_id),
        )


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    intro: str
    sheet: str
    name_zh: str = ""


@dataclass(frozen=True)
class Line:
    speaker: str
    """The character's id, or `USER_SPEAKER`."""
    text: str
    """Her Japanese, or the user's own words."""
    created_at: str
    zh: str = ""
    """Her Chinese subtitle; empty for the user's lines and for hers before subtitles existed."""
    photo: dict | None = None
    """The group photo sent with it, if any: id, status and reason."""

    def shown(self, characters: dict[str, Character]) -> dict:
        if self.speaker == USER_SPEAKER:
            name, color = "你", None
        else:
            character = characters.get(self.speaker)
            name = character.name if character else self.speaker
            color = character.color if character else None
        return {"speaker": self.speaker, "name": name, "color": color, "text": self.text,
                "zh": self.zh, "created_at": self.created_at, "photo": self.photo}


@dataclass(frozen=True)
class Said:
    """One line as the model wrote it: her Japanese, its Chinese subtitle."""

    ja: str
    zh: str


class Groups:
    """Every group chat of every user, the scheduler that raises their topics, and each
    conversation it talks through in the background."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        characters: dict[str, Character],
        lease_of: Callable[[], DeployerLease],
        affinities: Affinities | None = None,
        memories: Memories | None = None,
        *,
        daily: Schedules | None = None,
        store: Store | None = None,
        world: World | None = None,
        weathers: Weathers | None = None,
        retrieval: Any | None = None,
        photos: Photos | None = None,
        affinity_judge: AffinityJudge | None = None,
        member_count: int = DEFAULT_MEMBERS,
        gap: tuple[timedelta, timedelta] = TOPIC_GAP,
        delay: tuple[float, float] = DEFAULT_DELAY,
    ) -> None:
        self._db = db
        self._clock = clock
        self._characters = characters
        self._lease_of = lease_of
        self._affinities = affinities
        self._memories = memories
        self._daily = daily
        self._store = store
        self._world = world
        self._context = GroupContext(daily, store, weathers)
        self._retrieval = retrieval
        self._photos = photos
        self._affinity_judge = affinity_judge
        self._member_count = member_count
        self._gap = gap
        self._delay = delay
        self._busy: set[str] = set()
        """Groups whose topic is being talked through right now."""
        self._last_prompts: dict[str, dict] = {}
        """The last prompt each group asked the chat model with, its speaker, for the
        development tools."""
        self._tasks: dict[str, asyncio.Task[None]] = {}
        """Each group's conversation under way, to cancel as it is dissolved or the backend closes."""
        self._task: asyncio.Task[None] | None = None
        db.prepare(_open)

    async def aclose(self) -> None:
        """Stop the conversations under way."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---- the groups themselves --------------------------------------------------------

    async def for_user(self, user_id: str) -> list[dict]:
        """The user's groups, newest first, each with a preview of its latest line."""
        return await self._db.read(self._read_list, user_id)

    def _read_list(self, db: sqlite3.Connection, user_id: str) -> list[dict]:
        rows = db.execute(
            "SELECT id, name, is_on, created_at, last_topic_at, seen_at FROM group_chats"
            " WHERE user_id = ? AND dissolved_at IS NULL ORDER BY created_at DESC, rowid DESC",
            (user_id,),
        ).fetchall()
        return [self._listed(db, row) for row in rows]

    def _listed(self, db: sqlite3.Connection, row: Sequence) -> dict:
        group_id = row[0]
        preview = None
        last_message_at = None
        latest = db.execute(
            "SELECT id FROM group_topics WHERE group_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (group_id,),
        ).fetchone()
        if latest is not None:
            line = db.execute(
                "SELECT speaker, text, zh, created_at FROM group_messages WHERE topic_id = ?"
                " ORDER BY id DESC LIMIT 1",
                (latest[0],),
            ).fetchone()
            if line is not None:
                preview = Line(line[0], line[1], line[3], line[2]).shown(self._characters)
                last_message_at = line[3]
        return {
            "id": row[0],
            "name": row[1],
            "on": bool(row[2]),
            "created_at": row[3],
            "last_topic_at": row[4],
            "last_message_at": last_message_at,
            "members": self._member_shown(db, group_id),
            "preview": preview,
            "unseen": self._unseen(db, group_id, row[5]),
        }

    def _unseen(
        self, db: sqlite3.Connection, group_id: str, seen_at: str | None
    ) -> bool:
        """Whether anyone has said anything since the page was last open."""
        since = seen_at or ""
        row = db.execute(
            "SELECT 1 FROM group_messages m JOIN group_topics t ON t.id = m.topic_id"
            " WHERE t.group_id = ? AND m.created_at > ? LIMIT 1",
            (group_id, since),
        ).fetchone()
        return row is not None

    def _member_shown(self, db: sqlite3.Connection, group_id: str) -> list[dict]:
        ids = [
            row[0]
            for row in db.execute(
                "SELECT character_id FROM group_members WHERE group_id = ? ORDER BY rowid",
                (group_id,),
            )
        ]
        return [
            {"id": member, "name": self._characters[member].name}
            for member in ids
            if member in self._characters
        ]

    async def read(self, user_id: str, group_id: str) -> dict | None:
        """One group: the members, the switch, and its whole history, oldest topic first.
        Reading it counts as having seen it, so the list stops dotting it."""
        state = await self._db.read(self._read_group, user_id, group_id)
        if state is not None:
            await self._db.write(self._mark_seen, group_id, self._clock())
        return state

    def last_prompt(self, group_id: str) -> dict | None:
        """The last prompt `group_id` asked the chat model with, and whose speech it was, for
        the development tools; None before any line since the backend started."""
        return self._last_prompts.get(group_id)

    def _mark_seen(self, db: sqlite3.Connection, group_id: str, now: datetime) -> None:
        db.execute(
            "UPDATE group_chats SET seen_at = ? WHERE id = ?", (now.isoformat(), group_id)
        )

    def _read_group(self, db: sqlite3.Connection, user_id: str, group_id: str) -> dict | None:
        group = db.execute(
            "SELECT id, name, is_on, created_at, last_topic_at FROM group_chats"
            " WHERE id = ? AND user_id = ? AND dissolved_at IS NULL",
            (group_id, user_id),
        ).fetchone()
        if group is None:
            return None
        topics = db.execute(
            "SELECT id, raised_by, text, created_at, closed_at FROM group_topics"
            " WHERE group_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (group_id, HISTORY_TOPICS),
        ).fetchall()
        history = [
            {
                "topic": {
                    "id": row[0], "raised_by": row[1], "text": row[2],
                    "created_at": row[3], "open": row[4] is None,
                },
                "lines": [
                    Line(line[0], line[1], line[2], line[3]).shown(self._characters)
                    for line in db.execute(
                        "SELECT speaker, text, created_at, zh FROM group_messages"
                        " WHERE topic_id = ? ORDER BY id",
                        (row[0],),
                    )
                ],
            }
            for row in reversed(topics)
        ]
        latest = history[-1] if history else None
        return {
            "id": group[0],
            "name": group[1],
            "on": bool(group[2]),
            "created_at": group[3],
            "last_topic_at": group[4],
            "members": self._member_shown(db, group_id),
            "topic": latest["topic"] if latest else None,
            "lines": latest["lines"] if latest else [],
            "history": history,
        }

    def _default_members(self) -> list[str]:
        return list(self._characters)[: self._member_count]

    async def create(self, user_id: str, name: str, ids: Sequence[str]) -> str | None:
        """Start a group with the chosen members (拉群) and have someone raise its first topic;
        the new group's id, or None where there is no one to put in it."""
        chosen = [cid for cid in ids if cid in self._characters] or self._default_members()
        if not chosen:
            return None
        group_id = secrets.token_urlsafe(12)
        await self._db.write(
            self._create, user_id, group_id, _clean_name(name), chosen, self._clock()
        )
        await self.raise_topic(user_id, group_id)
        return group_id

    def _create(
        self, db: sqlite3.Connection, user_id: str, group_id: str, name: str,
        ids: Sequence[str], now: datetime,
    ) -> None:
        db.execute(
            "INSERT INTO group_chats (id, user_id, name, is_on, created_at)"
            " VALUES (?, ?, ?, 1, ?)",
            (group_id, user_id, name, now.isoformat()),
        )
        for cid in ids:
            db.execute(
                "INSERT INTO group_members (group_id, character_id) VALUES (?, ?)",
                (group_id, cid),
            )

    async def rename(self, user_id: str, group_id: str, name: str) -> None:
        """Give the group a new name."""
        cleaned = _clean_name(name)
        if not cleaned:
            return
        await self._db.write(self._rename, user_id, group_id, cleaned)

    def _rename(self, db: sqlite3.Connection, user_id: str, group_id: str, name: str) -> None:
        db.execute(
            "UPDATE group_chats SET name = ? WHERE id = ? AND user_id = ? AND dissolved_at IS NULL",
            (name, group_id, user_id),
        )

    async def set_on(self, user_id: str, group_id: str, on: bool) -> None:
        """Turn the group's scheduler on or off; off only stops new topics, nothing is deleted."""
        await self._db.write(self._set_on, user_id, group_id, on)

    def _set_on(
        self, db: sqlite3.Connection, user_id: str, group_id: str, on: bool
    ) -> None:
        db.execute(
            "UPDATE group_chats SET is_on = ? WHERE id = ? AND user_id = ? AND dissolved_at IS NULL",
            (int(on), group_id, user_id),
        )

    async def set_members(self, user_id: str, group_id: str, ids: Sequence[str]) -> None:
        """Set who is in the group, those that exist; nothing for an empty or unknown list."""
        chosen = [cid for cid in ids if cid in self._characters]
        if not chosen:
            return
        await self._db.write(self._set_members, user_id, group_id, chosen)

    def _set_members(
        self, db: sqlite3.Connection, user_id: str, group_id: str, ids: Sequence[str]
    ) -> None:
        kept = db.execute(
            "SELECT 1 FROM group_chats WHERE id = ? AND user_id = ? AND dissolved_at IS NULL",
            (group_id, user_id),
        ).fetchone()
        if kept is None:
            return
        db.execute("DELETE FROM group_members WHERE group_id = ?", (group_id,))
        for cid in ids:
            db.execute(
                "INSERT INTO group_members (group_id, character_id) VALUES (?, ?)",
                (group_id, cid),
            )

    async def dissolve(self, user_id: str, group_id: str) -> None:
        """Dissolve the group (解散): it leaves the list, its topics close and its history is
        hidden. The conversations under way end with it."""
        await self._db.write(self._dissolve, user_id, group_id, self._clock())
        self._last_prompts.pop(group_id, None)
        task = self._tasks.pop(group_id, None)
        if task is not None:
            task.cancel()

    def _dissolve(
        self, db: sqlite3.Connection, user_id: str, group_id: str, now: datetime
    ) -> None:
        db.execute(
            "UPDATE group_chats SET dissolved_at = ?, is_on = 0"
            " WHERE id = ? AND user_id = ? AND dissolved_at IS NULL",
            (now.isoformat(), group_id, user_id),
        )
        db.execute(
            "UPDATE group_topics SET closed_at = ? WHERE group_id = ? AND closed_at IS NULL",
            (now.isoformat(), group_id),
        )

    # ---- the scheduler ----------------------------------------------------------------

    async def keep_going(self) -> None:
        """Raise a topic in every group whose time has come, once every `TICK`."""
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("the group chat's scheduler failed")
            await asyncio.sleep(TICK.total_seconds())

    async def _tick(self) -> None:
        now = self._clock()
        due = await self._db.read(self._due, now)
        for group_id, user_id in due:
            if group_id in self._busy:
                continue
            self._busy.add(group_id)
            try:
                await self.raise_topic(user_id, group_id)
            except Exception:
                log.exception("raising a group topic failed")
            finally:
                self._busy.discard(group_id)

    def _due(self, db: sqlite3.Connection, now: datetime) -> list[tuple[str, str]]:
        rows = db.execute(
            "SELECT id, user_id FROM group_chats WHERE is_on = 1 AND dissolved_at IS NULL"
            " AND (next_topic_at IS NULL OR next_topic_at <= ?)",
            (now.isoformat(),),
        ).fetchall()
        due: list[tuple[str, str]] = []
        for group_id, user_id in rows:
            timezone = effective_timezone(read_profile(db, user_id), None)
            if not _at_quiet(now, timezone):
                due.append((group_id, user_id))
        return due

    def _next_topic_at(self, now: datetime) -> str:
        """When this group next raises a topic on its own: after its cooldown, eight hours."""
        seconds = random.uniform(self._gap[0].total_seconds(), self._gap[1].total_seconds())
        return (now + timedelta(seconds=seconds)).isoformat()

    # ---- a topic, talked through ------------------------------------------------------

    async def raise_topic(self, user_id: str, group_id: str) -> str | None:
        """Have a member raise a topic and the others talk it through in the background; the
        topic's id, or None where the group is gone, off, has no members, or everyone is
        asleep (it is then put off until the next 7 in the morning)."""
        state = await self.read(user_id, group_id)
        if state is None or not state["on"] or not state["members"]:
            return None
        all_members = [self._members_by_id(m["id"]) for m in state["members"]]
        all_members = [m for m in all_members if m is not None]
        if not all_members:
            return None
        # Whoever is asleep does not raise a topic, nor answer one; when all of them are
        # asleep, the group is quiet until they all wake.
        now = self._clock()
        members = await self._awake(user_id, all_members, now)
        if not members:
            timezone = "Asia/Shanghai"
            if self._store is not None:
                try:
                    timezone = effective_timezone(await self._store.get_profile(user_id), None)
                except Exception:
                    log.warning("reading the producer's time for the group failed", exc_info=True)
            until = _seven_am(now, timezone)
            if self._daily is not None and self._store is not None:
                try:
                    zone = ZoneInfo(timezone)
                    local = now.astimezone(zone)
                    if (awake_at := await self._next_awake_at(user_id, all_members, local)) is not None:
                        until = awake_at.astimezone(timezone.utc).isoformat()
                except Exception:
                    log.warning("scheduling the group's next topic failed", exc_info=True)
            await self._db.write(self._put_off, group_id, until)
            return None
        recent = state["lines"][-6:]
        # What it has been talking about, latest first, so the new topic can stay off it.
        past = [entry["topic"]["text"] for entry in reversed(state["history"])][:TOPIC_RECALL]
        raiser = members[secrets.randbelow(len(members))]
        try:
            said, photo = await self._raise(
                user_id, group_id, members, raiser, recent, past, now,
                group_name=state["name"], roster=all_members,
            )
        except Exception:
            log.warning("a member could not raise a topic", exc_info=True)
            return None
        topic_id = secrets.token_urlsafe(12)
        await self._db.write(
            self._start_topic, user_id, group_id, topic_id, raiser.id, said.ja, said.zh, now,
            photo,
        )
        self._start_conversation(
            user_id, group_id, topic_id, members, said.ja, raiser.id,
            group_name=state["name"], roster=all_members,
        )
        return topic_id

    def _put_off(self, db: sqlite3.Connection, group_id: str, until: str) -> None:
        """Put the group's next topic off until `until` (ISO, UTC): everyone is asleep now."""
        db.execute(
            "UPDATE group_chats SET next_topic_at = ? WHERE id = ? AND dissolved_at IS NULL",
            (until, group_id),
        )

    async def _raise(
        self, user_id: str, group_id: str, members: Sequence[Member], raiser: Member,
        recent: Sequence[dict], past: Sequence[str], now: datetime, *,
        group_name: str, roster: Sequence[Member],
    ) -> tuple[Said, tuple[str, bytes] | None]:
        system = await self._system(user_id, raiser, group_name, roster)
        material = await self._material(user_id, raiser)
        now_told = await self._now_told(user_id, members, now)
        character = self._characters.get(raiser.id)
        allow_photo = self._photos is not None and character is not None
        pictured = [
            other for member in members
            if member.id != raiser.id
            and (other := self._characters.get(member.id)) is not None
            and other.photographed
        ]
        idols = [
            other.photo_tag for member in members
            if (other := self._characters.get(member.id)) is not None and other.photo_tag
        ]
        photo_members = "、".join(
            f"{member.id}（{member.name}）" for member in pictured
        )
        prompt = _topic_prompt(
            system, recent, self._names_block(members), past, material, now_told, allow_photo,
            photo_members,
        )
        answer = await self._ask(prompt, group_id, raiser)
        photo_tag = None
        found = _PHOTO.search(answer)
        if found is not None:
            photo_tag = found.group(1).strip()
            answer = answer[:found.start()] + answer[found.end():]
        said = _parse_line(answer) or Said("今天想聊点什么呢。", "今天想聊点什么好呢。")
        photo: tuple[str, bytes] | None = None
        if allow_photo and photo_tag and self._photos is not None and character is not None:
            try:
                fields = tag_fields(photo_tag)
                allowed = COMPOSITIONS if character.photographed else UNPEOPLED
                tag = read_picture(fields, allowed, idols=idols, pictured=pictured)
                if tag is not None:
                    image = await self._photos.picture(
                        character, tag, asyncio.get_running_loop().time() + 120, tag.stripped
                    )
                    photo = (photo_tag, image)
            except Exception:
                log.warning("a group's opening photo was not generated", exc_info=True)
        return (said if said.zh else await self._subtitle(said)), photo

    def _names_block(self, members: Sequence[Member]) -> str:
        """How her names are written in the subtitles, for the prompt.

        Those of this group spelled out one a line, as they are the ones she is talking to
        and the ones that came out wrong; everyone else named once on a line of their own, so
        that a name she brings up is not the model's to make up (`.scratch/nsfw/`).

        Written tighter than `reply_format.names_instructions`, which a conversation reads
        once a turn: a group writes a line every few seconds, and neither the header nor the
        fallback is worth that many tokens each time."""
        in_group = {
            member.id: self._characters[member.id]
            for member in members
            if member.id in self._characters
        }
        names = chinese_name_map(in_group.values())
        rest = {
            ja: zh for ja, zh in chinese_name_map(self._characters.values()).items()
            if ja not in names
        }
        lines = ["# 名字怎么译", "字幕里提到人时照下面的中文名写，不要自己音译："]
        lines += [f"- {ja} → {zh}" for ja, zh in names.items()]
        if rest:
            lines.append("其余人：" + "、".join(rest.values()))
        lines.append("同一个名字在整段里写法要一致。")
        return "\n".join(lines)

    async def _material(self, user_id: str, member: Member) -> str:
        """Her three own memories and one shared memory, for a topic or a reply.

        Her own memories stay hers; the shared one is public in the office. Other private
        and unit memories never reach her here, and the same hooks the single chat's own
        memories use (`.scratch/nsfw/`) apply."""
        if self._memories is None:
            return ""
        try:
            held = await self._memories.kept(user_id)
        except Exception:
            log.warning("reading her memories for the group failed", exc_info=True)
            return ""
        own = [m for m in held if m.source_character_id == member.id][-GROUP_OWN_MEMORIES:]
        public = [
            m for m in held
            if m.scope == "shared" and m.source_character_id != member.id
        ][-GROUP_PUBLIC_MEMORIES:]
        if not own and not public:
            return ""
        parts = ["# 你记得的事"]
        parts += [f"- {memory.text}" for memory in own] or ["（没有）"]
        if public:
            parts.append("\n# 公开的事")
            parts += [
                f"- {memory.text}（这是公开的，你听说过，不要说得像你亲眼见过）"
                for memory in public
            ]
        return "\n".join(parts)

    async def _photo_request(
        self, user_id: str, target_ids: Sequence[str], lines: Sequence[Line], text: str
    ) -> str | None:
        """The one member a photo request is for, as the judge model reads the line."""
        if not target_ids or self._photos is None or self._affinity_judge is None:
            return None
        character_id = target_ids[0]
        character = self._characters.get(character_id)
        if character is None:
            return None
        if self._affinities is not None:
            try:
                status = await self._affinities.status(user_id, character.id)
                if status is None or status.tier == "cold":
                    return None
            except Exception:
                log.warning("reading affinity for a group photo failed", exc_info=True)
                return None
        try:
            eligible = await self._db.read(self._photos.eligibility, character, user_id)
        except Exception:
            log.warning("reading photo eligibility for the group failed", exc_info=True)
            return None
        if eligible is None:
            return None
        history = [
            Message(
                0,
                "user" if line.speaker == USER_SPEAKER else "character",
                line.text if line.speaker == USER_SPEAKER else None,
                None if line.speaker == USER_SPEAKER else line.text,
                "" if line.speaker == USER_SPEAKER else (line.zh or ""),
                line.created_at,
            )
            for line in lines[-8:]
            if line.speaker in (USER_SPEAKER, character_id)
        ]
        try:
            _, asked = await self._affinity_judge.judge(
                character, history, text, affinity=False, photo=True
            )
        except Exception:
            log.warning("judging a group photo request failed", exc_info=True)
            return None
        return character.id if asked else None

    def _start_conversation(
        self, user_id: str, group_id: str, topic_id: str, members: Sequence[Member],
        topic: str, raised_by: str, *, mentioned: Sequence[str] = (),
        threshold: float = BASE_THRESHOLD, delay: tuple[float, float] | None = None,
        photo_for: str | None = None, group_name: str, roster: Sequence[Member],
    ) -> None:
        """Talk the topic through in the background, so the page can show the lines as they
        come and the user can cut in between them."""
        previous = self._tasks.pop(group_id, None)
        if previous is not None:
            previous.cancel()
        task = asyncio.create_task(
            self._converse(
                user_id, group_id, topic_id, members, topic, raised_by,
                mentioned=mentioned, threshold=threshold, delay=delay, photo_for=photo_for,
                group_name=group_name, roster=roster,
            )
        )
        self._tasks[group_id] = task

        def done(finished: "asyncio.Task[None]") -> None:
            if self._tasks.get(group_id) is finished:
                self._tasks.pop(group_id, None)

        task.add_done_callback(done)

    async def _converse(
        self, user_id: str, group_id: str, topic_id: str, members: Sequence[Member],
        topic: str, raised_by: str, *, mentioned: Sequence[str] = (),
        threshold: float = BASE_THRESHOLD, delay: tuple[float, float] | None = None,
        photo_for: str | None = None, group_name: str, roster: Sequence[Member],
    ) -> None:
        """Talk the topic through: each member who wants to answer waits her own spell, and
        whoever's runs out first speaks; the bar rises and the outsider's mention falls, until
        no one is willing or `MAX_TURNS`. `mentioned` are the members the user named or
        addressed, whose willingness is raised; `delay` is the timer's range (the shorter one
        is for answering the user)."""
        timer = delay or self._delay
        try:
            scores = await self._relevance(members, topic)
            past = await self._past(group_id, topic_id)
        except Exception:
            log.exception("reading the group's own context failed")
            scores, past = {}, []
        by_id = {member.id: member for member in members}
        last_speaker = raised_by
        spoken: dict[str, int] = {}

        def willing(member: Member) -> float:
            bonus = MENTION_BONUS if member.id in mentioned else 0.0
            return (
                scores.get(member.id, 0.0) + bonus - spoken.get(member.id, 0) * SPEAK_DECAY
            )

        # Each member who wants to answer gets her own moment: her own timer, started now.
        # A raised topic always gets at least one answer — the most willing member speaks even
        # when nobody clears the bar — so a topic is never left hanging on its first line.
        pending: dict[str, float] = {}
        try:
            others = [member for member in members if member.id != raised_by]
            willing_now = [member for member in others if willing(member) >= threshold]
            if not willing_now and others:
                willing_now = [
                    max(others, key=lambda member: willing(member))
                ]
            for member in willing_now:
                pending[member.id] = time.monotonic() + random.uniform(*timer)
            for turn in range(MAX_TURNS):
                if not pending:
                    break
                # Whoever's timer is up first speaks; when several are due, the earliest wins.
                speaker_id = min(pending, key=lambda mid: pending[mid])
                due = pending.pop(speaker_id)
                wait = due - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                speaker = by_id[speaker_id]
                said = await self._lines(topic_id)
                mention = max(0.0, BASE_MENTION - turn * MENTION_STEP)
                outsiders = [m for m in members if m.id not in {speaker.id, last_speaker}]
                now = self._clock()
                system = await self._system(user_id, speaker, group_name, roster)
                group_now = await self._context.read(user_id, group_id, members, now)
                now_told = "\n".join(group_now.lines())
                history = history_block(
                    [*past, *said[-RECENT_LINES:]], now, group_now.timezone,
                    {member.id: member.name for member in members},
                )
                reference = ""
                if self._retrieval is not None and (character := self._characters.get(speaker.id)):
                    try:
                        found = await self._retrieval.retrieve(
                            character, [], f"{topic}\n" + "\n".join(
                                line.text for line in said[-RECENT_LINES:]
                            ), memory=None,
                        )
                        reference = found.prompt or ""
                    except Exception:
                        log.warning("retrieving for the group failed", exc_info=True)
                material = await self._material(user_id, speaker)
                prompt = _say_prompt(
                    system, speaker, members, said, past, mention, outsiders,
                    self._names_block([*members, *outsiders]), now_told, material, history,
                    reference, photo_for == speaker.id,
                )
                try:
                    answer = await self._ask(prompt, group_id, speaker)
                except Exception:
                    log.warning("a member could not answer in the group", exc_info=True)
                    break
                photo_tag = None
                if photo_for == speaker.id:
                    found = _PHOTO.search(answer)
                    if found is not None:
                        photo_tag = found.group(1).strip()
                        answer = answer[:found.start()] + answer[found.end():]
                line = _parse_line(answer)
                if line is None or not line.ja:
                    # She gave nothing readable this time: pass and let the others go on,
                    # rather than ending the whole topic on one bad answer.
                    log.warning("a member gave no readable line; passing")
                    continue
                if not line.zh:
                    line = await self._subtitle(line)
                now = self._clock()
                message_id = await self._db.write(
                    self._add_line, topic_id, user_id, speaker.id, line.ja, line.zh, now
                )
                if photo_tag and self._photos is not None:
                    character = self._characters.get(speaker.id)
                    if character is not None:
                        try:
                            photo_id = self._photos.begin(character, photo_tag, user_id)
                            await self._db.write(
                                self._add_group_photo, group_id, topic_id, message_id,
                                photo_id, photo_tag, now,
                            )
                            await self._photos.recorded(photo_id)
                        except Exception:
                            log.warning("a group photo could not begin", exc_info=True)
                await self._remember(user_id, topic_id, members)
                spoken[speaker.id] = spoken.get(speaker.id, 0) + 1
                last_speaker = speaker.id
                # The bar rises with each line; whoever is no longer willing drops out.
                limit = threshold + (turn + 1) * THRESHOLD_STEP
                pending = {
                    mid: at for mid, at in pending.items()
                    if mid != speaker.id and willing(by_id[mid]) >= limit
                }
        except asyncio.CancelledError:
            raise
        finally:
            await self._close_topic(group_id, topic_id)
            await self._remember(user_id, topic_id, members)

    async def _subtitle(self, line: Said) -> Said:
        """Her line with a Chinese subtitle, asking for one when the model left `<zh>` out."""
        try:
            answer = await self._ask(
                [
                    ChatMessage(
                        "system",
                        "你是翻译。把用户给的日语翻成自然的中文口语，只输出译文，不要解释。",
                    ),
                    ChatMessage("user", line.ja),
                ]
            )
            subtitle = _clean_line(answer)
        except Exception:
            log.warning("translating a group line failed", exc_info=True)
            return line
        return Said(line.ja, subtitle) if subtitle else line

    async def _system(
        self, user_id: str, member: Member, group_name: str, roster: Sequence[Member]
    ) -> str:
        """Who she is, the world she lives in, the group she is in (its name and everyone in
        it), and only her own affinity with the producer."""
        label = f"{member.name}（中文名 {member.name_zh}）" if member.name_zh else member.name
        parts = [member.sheet]
        if not member.sheet.lstrip().startswith("# 群聊摘要"):
            parts.insert(0, f"你叫{label}。")
        profile = None
        if self._store is not None:
            try:
                profile = await self._store.get_profile(user_id)
            except Exception:
                log.warning("reading the producer for the group failed", exc_info=True)
        if self._world is not None and profile is not None:
            try:
                timezone = effective_timezone(profile, None)
                parts.append(self._world.prompt(member.name, local_date(self._clock(), timezone)))
            except Exception:
                log.warning("reading the world for the group failed", exc_info=True)
        if self._affinities is not None:
            try:
                status = await self._affinities.status(user_id, member.id)
                character = self._characters.get(member.id)
                lines = tier_prompt(
                    status,
                    character.heart if character is not None else None,
                    profile.gender == "female" if profile is not None else False,
                )
                if lines:
                    parts.extend(lines)
                    parts.append(
                        "这只影响你直接对制作人说话时的态度。对群里其他人按你自己的关系说话；"
                        "不要提好感度、不要比较谁和制作人更亲近，也不要替别人表态。"
                    )
            except Exception:
                log.warning("reading her affinity for the group failed", exc_info=True)
        others = "、".join(each.name for each in roster if each.id != member.id)
        company = f"群里有{others}，制作人也在" if others else "只有你和制作人"
        parts.append(
            f"你现在在「{group_name}」这个群里，{company}。"
            "群名只是让你知道自己在哪里，不用主动说出来。"
        )
        # 只放她自己的好感度；其他人的好感度不进入她的提示词。
        return "\n".join(parts)

    async def _asleep(self, user_id: str, character_id: str, now: datetime) -> bool:
        """Whether the character is asleep now, by her daily schedule (GLOSSARY.md「行程」);
        False without the schedules or a store to read the producer's time zone, or on
        failing, so that a group without them goes on as before."""
        if self._daily is None or self._store is None:
            return False
        try:
            zone = ZoneInfo(effective_timezone(await self._store.get_profile(user_id), None))
            local = now.astimezone(zone)
            today = await self._daily.of(user_id, character_id, local.date())
            yesterday = await self._daily.of(
                user_id, character_id, local.date() - timedelta(days=1)
            )
            return rest(today, yesterday, local) == "asleep"
        except Exception:
            log.warning("reading her sleep for the group failed", exc_info=True)
            return False

    async def _awake(self, user_id: str, members: Sequence[Member], now: datetime) -> list[Member]:
        """The members not asleep now; those whose sleep cannot be told are kept awake."""
        awake: list[Member] = []
        for member in members:
            if not await self._asleep(user_id, member.id, now):
                awake.append(member)
        return awake

    async def _now_told(self, user_id: str, members: Sequence[Member], now: datetime) -> str:
        """The time and what each member is doing now, without any producer state."""
        context = getattr(self, "_context", None) or GroupContext(
            getattr(self, "_daily", None), getattr(self, "_store", None)
        )
        group_now = await context.read(user_id, "", members, now)
        return "\n".join(group_now.lines())

    async def _member_now(self, user_id: str, character_id: str, local: datetime) -> str:
        """What she is doing now, one short phrase, by her daily schedule (GLOSSARY.md「行程」);
        the topic raiser only needs the broad stroke, not the minute-by-minute slots."""
        try:
            today = await self._daily.of(user_id, character_id, local.date())
            yesterday = await self._daily.of(
                user_id, character_id, local.date() - timedelta(days=1)
            )
        except Exception:
            log.warning("reading her schedule for the group failed", exc_info=True)
            return ""
        resting = rest(today, yesterday, local)
        if resting == "bath":
            return "在洗澡"
        if resting == "asleep":
            return "在睡觉"
        if resting == "bedtime":
            return "快到睡觉时间了"
        if today is None or not today.slots:
            return ""
        current = today.now(local.time())
        if current is not None and current.what:
            return f"现在在{current.what}"
        if local.time() < today.slots[0].start:
            return "今天的安排还没开始"
        return "现在没安排"

    async def _next_awake_at(
        self, user_id: str, members: Sequence[Member], local: datetime
    ) -> datetime | None:
        """When the last of `members` wakes, in the producer's time zone (`local`); None
        where none of their wake times can be told (no schedules)."""
        if self._daily is None:
            return None
        wakes: list[datetime] = []
        for member in members:
            schedule = await self._daily.of(user_id, member.id, local.date())
            if schedule is None or schedule.wake is None:
                schedule = await self._daily.of(
                    user_id, member.id, local.date() - timedelta(days=1)
                )
            if schedule is None or schedule.wake is None:
                continue
            wake = datetime.combine(local.date(), schedule.wake, tzinfo=local.tzinfo)
            if wake <= local:
                wake += timedelta(days=1)
            wakes.append(wake)
        return max(wakes) if wakes else None

    async def _relevance(
        self, members: Sequence[Member], topic: str
    ) -> dict[str, float]:
        """How relevant each member finds the topic, 0 to 1, one model call; none on
        failing (the topic then goes nowhere rather than guess)."""
        prompt = _relevance_prompt(members, topic)
        try:
            answer = await self._ask(prompt)
        except Exception:
            log.warning("judging the group's relevance failed", exc_info=True)
            return {}
        scores = _parse_scores(answer, [member.id for member in members])
        log.info("group relevance for %r: %s", topic[:40], scores)
        return scores

    async def _ask(
        self, messages: Sequence[ChatMessage], group_id: str | None = None,
        speaker: Member | None = None,
    ) -> str:
        """One model answer; asked twice when the first comes back empty, which happens
        now and then and would otherwise end a conversation on the spot. With `group_id`,
        the prompt is kept as the group's last one for the development tools, with `speaker`
        as whose line it was."""
        if group_id is not None:
            self._last_prompts[group_id] = {
                "prompt": _dump_prompt(messages),
                "speaker_id": speaker.id if speaker is not None else None,
                "speaker_name": speaker.name if speaker is not None else None,
            }
        for attempt in range(2):
            lease = self._lease_of()
            try:
                chunks: list[str] = []
                async for chunk in lease.keys.chat_llm.stream(messages):
                    if isinstance(chunk, str):
                        chunks.append(chunk)
                text = "".join(chunks)
            finally:
                lease.release()
            if text.strip() or attempt == 1:
                return text
        return ""

    # ---- the user's own line ----------------------------------------------------------

    async def say(self, user_id: str, group_id: str, text: str) -> None:
        """The user says something, an ordinary member's line: it goes on with the group's
        latest topic (reopened if it had wound down), or starts a new one if it is a new
        subject."""
        text = text.strip()[:MAX_LINE_CHARS]
        if not text:
            return
        state = await self.read(user_id, group_id)
        if state is None or not state["on"]:
            return
        topic = state["topic"]
        now = self._clock()
        members = [self._members_by_id(m["id"]) for m in state["members"]]
        members = [m for m in members if m is not None]
        lines = await self._lines(topic["id"]) if topic is not None else []
        last_speaker = lines[-1].speaker if lines else USER_SPEAKER
        fresh, mentioned, addressed = await self._shift(members, lines, text)
        targets = group_targets(addressed, mentioned)
        judged = await self._judge_group_affinity(user_id, targets, lines, text)
        photo_for = await self._photo_request(user_id, targets, lines, text)
        if (
            not fresh
            and not mentioned
            and not addressed
            and last_speaker != USER_SPEAKER
            and text.rstrip().endswith(("？", "?"))
        ):
            # A question with no name is still asked of whoever just spoke.
            addressed = [last_speaker]
        if topic is None or fresh:
            topic_id = secrets.token_urlsafe(12)
            await self._db.write(
                self._start_topic, user_id, group_id, topic_id, USER_SPEAKER, text, "", now
            )
            await self._apply_group_affinity(user_id, judged)
            if members:
                self._start_conversation(
                    user_id, group_id, topic_id, members, text, USER_SPEAKER,
                    threshold=BASE_THRESHOLD, photo_for=photo_for,
                    group_name=state["name"], roster=members,
                )
            return
        topic_id = topic["id"]
        await self._db.write(self._add_line, topic_id, user_id, USER_SPEAKER, text, "", now)
        await self._apply_group_affinity(user_id, judged)
        if not topic["open"]:
            await self._db.write(self._reopen, topic_id)
        if members and (self._tasks.get(group_id) is None or not topic["open"]):
            self._start_conversation(
                user_id, group_id, topic_id, members, text, last_speaker,
                mentioned=[*mentioned, *addressed], threshold=CONTINUE_THRESHOLD,
                delay=REPLY_DELAY, photo_for=photo_for,
                group_name=state["name"], roster=members,
            )

    async def _judge_group_affinity(
        self, user_id: str, target_ids: Sequence[str], lines: Sequence[Line], text: str,
    ) -> dict[str, Judged]:
        """Judge the producer's line for each target, one at a time; a failure changes none."""
        if not target_ids or self._affinities is None or not self._affinities.on:
            return {}
        if self._affinity_judge is None:
            return {}
        judged: dict[str, Judged] = {}
        for character_id in target_ids:
            character = self._characters.get(character_id)
            if character is None:
                continue
            history = [
                Message(
                    0,
                    "user" if line.speaker == USER_SPEAKER else "character",
                    line.text if line.speaker == USER_SPEAKER else None,
                    None if line.speaker == USER_SPEAKER else line.text,
                    "" if line.speaker == USER_SPEAKER else (line.zh or ""),
                    line.created_at,
                )
                for line in lines[-8:]
                if line.speaker in (USER_SPEAKER, character_id)
            ]
            try:
                found, _ = await self._affinity_judge.judge(
                    character, history, text, affinity=True, photo=False,
                )
            except Exception:
                log.warning("judging a group line's affinity failed", exc_info=True)
                continue
            if found is not None:
                judged[character_id] = found
        return judged

    async def _apply_group_affinity(self, user_id: str, judged: dict[str, Judged]) -> None:
        """Apply the group's quieter steps, inside a write of their own."""
        if not judged or self._affinities is None:
            return
        try:
            await self._db.write(self._write_group_affinity, user_id, judged)
        except Exception:
            log.warning("changing affinity after a group line failed", exc_info=True)

    def _write_group_affinity(
        self, db: sqlite3.Connection, user_id: str, judged: dict[str, Judged]
    ) -> None:
        assert self._affinities is not None
        for character_id, found in judged.items():
            self._affinities.change(db, user_id, character_id, found, step=group_step(found))

    async def _shift(
        self, members: Sequence[Member], lines: Sequence[Line], text: str
    ) -> tuple[bool, list[str], list[str]]:
        """Whether the user's line starts a new topic, which members it names, and which it is
        addressed to (the one answering, say, even unnamed), one model call; the conversation
        goes on as it was on failing."""
        try:
            answer = await self._ask(_shift_prompt(members, lines, text))
        except Exception:
            log.warning("judging the user's group line failed", exc_info=True)
            return False, [], []
        fresh, mentioned, addressed = _parse_shift(answer, members)
        log.info(
            "group line %r: new topic = %s, mentioned = %s, addressed = %s",
            text[:30], fresh, mentioned, addressed,
        )
        return fresh, mentioned, addressed

    # ---- the memory -------------------------------------------------------------------

    async def _remember(
        self, user_id: str, topic_id: str, members: Sequence[Member]
    ) -> None:
        """Write down what the user revealed since the last extraction.

        Called after a character's line, so the owner is the first character to answer the
        producer; also at the topic's end as a sweep. Only new lines are read, so the same
        words are not extracted again."""
        try:
            lines, cursor = await self._db.read(self._read_memory_lines, topic_id)
            if not lines:
                return
            if not any(line.speaker == USER_SPEAKER for line in lines):
                await self._db.write(self._advance_memory_cursor, topic_id, cursor)
                return
            owner = _owner(lines, members)
            if owner is None:
                await self._db.write(self._advance_memory_cursor, topic_id, cursor)
                return
            held = []
            if self._memories is not None:
                held = (await self._memories.kept(user_id))[-SHOWN_MEMORIES:]
            answer = await self._ask(_memory_prompt(owner, members, lines, held))
            written = parse_memories(answer)
            now = self._clock()
            if written:
                await self._db.write(
                    self._write_memories, user_id, owner, written, now, cursor, topic_id
                )
            else:
                await self._db.write(self._advance_memory_cursor, topic_id, cursor)
        except Exception:
            log.warning("writing the group's memories failed", exc_info=True)

    def _read_memory_lines(
        self, db: sqlite3.Connection, topic_id: str
    ) -> tuple[list[Line], int]:
        """The topic's lines after its memory cursor, and the cursor to advance to."""
        row = db.execute(
            "SELECT memory_cursor FROM group_topics WHERE id = ?", (topic_id,)
        ).fetchone()
        after = int(row[0]) if row is not None else 0
        rows = db.execute(
            "SELECT id, speaker, text, created_at, zh FROM group_messages"
            " WHERE topic_id = ? AND id > ? ORDER BY id",
            (topic_id, after),
        ).fetchall()
        if not rows:
            return [], after
        return [Line(row[1], row[2], row[3], row[4]) for row in rows], int(rows[-1][0])

    @staticmethod
    def _advance_memory_cursor(db: sqlite3.Connection, topic_id: str, cursor: int) -> None:
        db.execute(
            "UPDATE group_topics SET memory_cursor = ? WHERE id = ?", (cursor, topic_id)
        )

    def _write_memories(
        self,
        db: sqlite3.Connection,
        user_id: str,
        owner: Member,
        written: Sequence[NewMemory],
        now: datetime,
        cursor: int | None = None,
        topic_id: str | None = None,
    ) -> None:
        """Write what the user revealed in the group into the owner's memories, as a private
        turn's are: with the feeling and the tier's strength, kept past its maintenance then
        decaying, none of it tied to a conversation."""
        created_at = now.astimezone(timezone.utc).isoformat()
        for memory in written:
            kept = db.execute(
                "SELECT 1 FROM memories WHERE user_id = ? AND text = ?", (user_id, memory.text)
            ).fetchone()
            if kept is not None:
                continue
            strength, hold = TIERS.get(memory.tier, TIERS["medium"])
            maintain_until = (now + hold).astimezone(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO memories (user_id, source_character_id, conversation_id, text,"
                " created_at, scope, emotion, strength, maintain_until, kind)"
                " VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 'short')",
                (user_id, owner.id, memory.text, created_at, memory.scope, memory.emotion,
                 strength, maintain_until),
            )
        if cursor is not None and topic_id is not None:
            db.execute(
                "UPDATE group_topics SET memory_cursor = ? WHERE id = ?", (cursor, topic_id)
            )

    # ---- read/write bodies ------------------------------------------------------------

    def _members_by_id(self, character_id: str) -> Member | None:
        character = self._characters.get(character_id)
        if character is None:
            return None
        name_zh = getattr(character, "name_zh", None) or ""
        sheet = character.group_sheet or _sheet(character)
        return Member(character.id, character.name, character.intro, sheet, name_zh)

    async def _lines(self, topic_id: str) -> list[Line]:
        return await self._db.read(self._read_lines, topic_id)

    async def _past(self, group_id: str, topic_id: str) -> list[Line]:
        """The latest lines of this group's earlier topics: its own recent history."""
        return await self._db.read(self._read_past, group_id, topic_id)

    def _read_past(
        self, db: sqlite3.Connection, group_id: str, topic_id: str
    ) -> list[Line]:
        rows = db.execute(
            "SELECT m.speaker, m.text, m.created_at, m.zh FROM group_messages m"
            " JOIN group_topics t ON t.id = m.topic_id"
            " WHERE t.group_id = ? AND m.topic_id != ? ORDER BY m.id DESC LIMIT ?",
            (group_id, topic_id, PAST_LINES),
        ).fetchall()
        return [Line(row[0], row[1], row[2], row[3]) for row in reversed(rows)]

    def _read_lines(self, db: sqlite3.Connection, topic_id: str) -> list[Line]:
        return [
            Line(
                row[0], row[1], row[2], row[3],
                None if row[4] is None else {
                    "id": row[4], "status": row[5], "reason": row[6],
                },
            )
            for row in db.execute(
                "SELECT m.speaker, m.text, m.created_at, m.zh, p.id, p.status, p.reason"
                " FROM group_messages m LEFT JOIN group_photos p ON p.message_id = m.id"
                " WHERE m.topic_id = ? ORDER BY m.id",
                (topic_id,),
            )
        ]

    def _start_topic(
        self, db: sqlite3.Connection, user_id: str, group_id: str, topic_id: str,
        raised_by: str, text: str, zh: str, now: datetime,
        photo: tuple[str, bytes] | None = None,
    ) -> None:
        db.execute(
            "UPDATE group_chats SET last_topic_at = ?, next_topic_at = ? WHERE id = ?",
            (now.isoformat(), self._next_topic_at(now), group_id),
        )
        db.execute(
            "INSERT INTO group_topics (id, group_id, user_id, raised_by, text, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (topic_id, group_id, user_id, raised_by, text, now.isoformat()),
        )
        cursor = db.execute(
            "INSERT INTO group_messages (topic_id, user_id, speaker, text, zh, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (topic_id, user_id, raised_by, text, zh, now.isoformat()),
        )
        if photo is not None and cursor.lastrowid is not None:
            tag, image = photo
            db.execute(
                "INSERT INTO group_photos (id, group_id, topic_id, message_id, kind, tag,"
                " status, image, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'topic', ?, 'ready', ?, ?, ?)",
                (secrets.token_urlsafe(12), group_id, topic_id, cursor.lastrowid, tag,
                 image, now.isoformat(), now.isoformat()),
            )

    def _add_line(
        self, db: sqlite3.Connection, topic_id: str, user_id: str, speaker: str,
        text: str, zh: str, now: datetime,
    ) -> int:
        cursor = db.execute(
            "INSERT INTO group_messages (topic_id, user_id, speaker, text, zh, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (topic_id, user_id, speaker, text, zh, now.isoformat()),
        )
        return int(cursor.lastrowid or 0)

    def _add_group_photo(
        self, db: sqlite3.Connection, group_id: str, topic_id: str, message_id: int,
        photo_id: str, tag: str, now: datetime,
    ) -> None:
        db.execute(
            "INSERT INTO group_photos (id, group_id, topic_id, message_id, kind, tag,"
            " status, created_at, updated_at) VALUES (?, ?, ?, ?, 'request', ?, 'pending', ?, ?)",
            (photo_id, group_id, topic_id, message_id, tag, now.isoformat(), now.isoformat()),
        )

    def _reopen(self, db: sqlite3.Connection, topic_id: str) -> None:
        """Take the topic up again after it had wound down: the user spoke in it."""
        db.execute("UPDATE group_topics SET closed_at = NULL WHERE id = ?", (topic_id,))

    async def _close_topic(self, group_id: str, topic_id: str) -> None:
        now = self._clock()
        try:
            await self._db.write(self._closed, group_id, topic_id, now)
        except Exception:
            log.warning("closing the group topic failed", exc_info=True)

    def _closed(
        self, db: sqlite3.Connection, group_id: str, topic_id: str, now: datetime
    ) -> None:
        db.execute(
            "UPDATE group_topics SET closed_at = ? WHERE id = ? AND group_id = ?",
            (now.isoformat(), topic_id, group_id),
        )


# ---- the prompts ---------------------------------------------------------------------


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())[:MAX_NAME_CHARS]
    return cleaned or "群聊"


def _sheet(character: Character) -> str:
    """Her personality, cut short enough to sit in a prompt."""
    return " ".join(character.sheet.split())[:900]


def _topic_prompt(
    system: str, recent: Sequence[dict], names: str, past: Sequence[str], material: str,
    now_told: str = "", allow_photo: bool = False, photo_members: str = "",
) -> list[ChatMessage]:
    """The new-topic prompt: the group's present and history, names, the rules, then the task."""
    context = _context(recent)
    tired = "".join(f"- {text}\n" for text in past)
    user = (
        (f"# 现在\n{now_told}\n\n" if now_told else "")
        + (f"# 记忆\n{material}\n\n" if material else "")
        + (f"# 这个群最近开过的话头\n{tired}\n" if tired else "")
        + (f"# 刚说过的几句\n{context}\n\n" if context else "")
        + (
            "# 照片\n如果这个话题适合配一张照片，可以写：\n"
            "<photo>\ncomposition: selfie / posed / candid / group / scenery / food / object\n"
            "framing: portrait / upper body / cowboy shot / full body / wide shot\n"
            "place: ...\noutfit: ...\naction: ...\n"
            f"with: {photo_members}\n</photo>\n"
            "规则：只拍你自己用 selfie / posed / candid；"
            "拍群里其他人用 group，with 写成员 id，连同你最多五个人；"
            "拍景物、食物、物品用 scenery / food / object。"
            "照片只能是全年龄；不写也可以。\n\n"
            if allow_photo else ""
        )
        + names
        + "\n现在请你主动开一个新话题：像在群里随口说一句，引出你想聊的事。\n"
        "- 说一件**具体**的事：今天遇到的、看到的、听到的、想到的一个细节。"
        "不要只是感想、感慨、「今天也要加油」这种空话。\n"
        "- 话题要符合**现在**的时间和大家的处境：早上别聊放学后、晚上别聊还没发生的事，"
        "她正在上课/工作/练习时别假定她有空。\n"
        "- 上面那些话头都聊过了，别回头再说它们；也别用「说到……」「……话说回来」把上一个"
        "话题接着往下拖——这次自己另起一件不相干的事。\n"
        "- 不要问「有人在吗」，不要提「换个话题」，不要解释。\n"
        "- 一到两句，口语。\n"
        + "\n用 <ja>日语原文</ja><zh>中文字幕</zh> 输出：<ja> 里只写日语、不要混入中文；"
        "<zh> 是这句日语的中文翻译；只输出这一组标签。"
        + "\n现在直接写出你的新话题，不要解释，也不要写「新话题：」这样的前缀。"
    )
    return [ChatMessage("system", system), ChatMessage("user", user)]


def _say_prompt(
    system: str, speaker: Member, members: Sequence[Member], said: Sequence[Line],
    past: Sequence[Line], mention: float, outsiders: Sequence[Member], names: str,
    now_told: str = "", material: str = "", history: str = "", reference: str = "",
    photo_request: bool = False,
) -> list[ChatMessage]:
    """The next line's prompt: Chinese history, names, the rules, then the output format."""
    spoken = {member.id: member.name for member in members}
    recent = list(said)[-RECENT_LINES:]
    context = "\n".join(
        f"{spoken.get(line.speaker, '制作人')}：{line.zh or line.text}"
        for line in [*past, *recent]
    )
    lines = [*past, *recent]
    last = lines[-1] if lines else None
    last_told = (
        f"# 上一个人说的\n{spoken.get(last.speaker, '制作人')}：{last.zh or last.text}\n\n"
        if last is not None else ""
    )
    hint = ""
    if outsiders:
        names_out = "、".join(m.name for m in outsiders)
        if secrets.randbelow(100) / 100 < mention:
            hint = f"如果话题合适，可以顺带提到{names_out}；不合适就别硬提。"
        else:
            hint = f"这次别提到{names_out}，就事论事。"
    user = (
        (f"# 现在\n{now_told}\n\n" if now_told else "")
        + (f"# 相关资料\n{reference}\n\n" if reference else "")
        + f"群聊记录：\n{history or context}\n\n"
        + last_told
        + (f"# 记忆\n{material}\n\n" if material else "")
        + (
            "# 照片\n制作人在向你要照片。如果要发，写：\n"
            "<photo>\nshot: selfie\nplace: ...\noutfit: ...\naction: ...\n</photo>\n"
            "照片只能是全年龄、单人、你自己。\n\n"
            if photo_request else ""
        )
        + names
        + "\n你在群里，接着往下聊。这一句可以是：回应刚才某个人说的（直接用她名字或接话）、"
        "补一句你自己的看法、或者顺着这个话题聊出一个相关的小话题。"
        "一到两句，口语，像群里随口说的；不要复述别人刚说过的，不要旁白，不要每句都提问。"
        + hint
        + "\n群聊记录是中文。请用 <ja>日语原文</ja><zh>中文字幕</zh> 输出："
        "<ja> 里只写日语、不要混入中文；<zh> 是这句日语的中文翻译；只输出这一组标签。"
    )
    return [ChatMessage("system", system), ChatMessage("user", user)]


def _relevance_prompt(members: Sequence[Member], topic: str) -> list[ChatMessage]:
    lines = "\n".join(f"- {m.id}（{m.name}）：{m.intro}" for m in members)
    system = (
        "你是一个群聊的旁白。你要判断每位成员对当前话题有多感兴趣、多相关，"
        "只输出 JSON，不要解释。"
    )
    user = (
        f"话题：{topic}\n\n成员：\n{lines}\n\n"
        "给每位成员一个 0 到 1 的分数（1 表示这个话题正好戳中她、她很有话可说）。"
        "输出形如 {\"mano\": 0.0, \"hiori\": 0.0} 的 JSON，key 只用成员 id，"
        "不要带名字或括号。"
    )
    return [ChatMessage("system", system), ChatMessage("user", user)]


def _shift_prompt(
    members: Sequence[Member], lines: Sequence[Line], text: str
) -> list[ChatMessage]:
    """Whether the user's line opens a new topic, which members it names, and which it is
    addressed to, as one call."""
    names = {m.id: m.name for m in members}
    recent = list(lines)[-RECENT_LINES:]
    context = "\n".join(
        f"{names.get(line.speaker, '制作人')}：{line.text}" for line in recent
    )
    roster = "\n".join(f"- {m.id}（{m.name}" + (f" / {m.name_zh}" if m.name_zh else "") + "）" for m in members)
    system = "你在看一段群聊，判断制作人刚说的话是什么意思，只输出 JSON，不要解释。"
    user = (
        f"群聊最近的记录：\n{context or '（还没有）'}\n\n"
        f"成员：\n{roster}\n\n"
        f"制作人刚说：「{text}」\n\n"
        "判断三件事：\n"
        "1. 这句话是不是在**开一个新话题**：只有话题明显换了一个（比如从吃饭聊到学习、天气）才算；"
        "追问、补充、附和都不算。\n"
        "2. 这句话里有没有**点名某几位成员**（用名字或昵称叫了她们，比如「めぐる」「巡」）？\n"
        "3. 这句话是在**对谁说**：问句、请求、接话，一定要给出对象（即使没有点名）："
        "「那你呢？」「换你你会怎么做」就是对刚才说话的人说的；"
        "只有纯粹的附和、感叹、自言自语（比如「嗯」「原来是这样」）才写 []。\n"
        "只输出 JSON：{\"new_topic\": true 或 false, \"mentioned\": [\"<成员 id>\", ...], "
        "\"addressed\": [\"<成员 id>\", ...]}，没点名、没对象就写 []。"
    )
    return [ChatMessage("system", system), ChatMessage("user", user)]


def _parse_shift(answer: str, members: Sequence[Member]) -> tuple[bool, list[str], list[str]]:
    """The JSON in `answer` as whether the line opens a new topic, which members it names,
    and which it is addressed to (by id or by any of their names)."""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end <= start:
        return False, [], []
    try:
        raw = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return False, [], []
    if not isinstance(raw, dict):
        return False, [], []
    fresh = raw.get("new_topic") is True

    def matched(items: object) -> list[str]:
        if not isinstance(items, list):
            return []
        names = [str(item) for item in items]
        return [
            member.id
            for member in members
            if any(
                member.id == name
                or member.name == name
                or member.id in name
                or member.name in name
                for name in names
            )
        ]

    return fresh, matched(raw.get("mentioned")), matched(raw.get("addressed"))


def _memory_prompt(
    owner: Member, members: Sequence[Member], lines: Sequence[Line], held: Sequence[Memory]
) -> list[ChatMessage]:
    """What the user revealed in the group, as a private turn's extractor reads it: the
    same instructions, the group's lines for the exchange."""
    names = {m.id: m.name for m in members}
    head = ["# 已经记住的事"]
    head += [f"{n}. {m.text}" for n, m in enumerate(held, 1)] or ["（还没有）"]
    head += ["", f"# 群聊记录（{owner.name}也在场）"]
    for line in lines[-SHOWN_MEMORIES:]:
        if line.speaker == USER_SPEAKER:
            head.append(f"制作人：{line.text}")
        else:
            head.append(f"{names.get(line.speaker, line.speaker)}：{line.text}")
    head += ["", "只从制作人说的话里找值得长期记住的、关于他本人的信息；没有就输出 {\"memories\": []}。"]
    return [ChatMessage("system", _INSTRUCTIONS), ChatMessage("user", "\n".join(head))]


def _owner(lines: Sequence[Line], members: Sequence[Member]) -> Member | None:
    """The member the user was talking with: the first of them to speak after the user's
    last line; the last of them to speak, failing that."""
    by_id = {m.id: m for m in members}
    last_user = max(
        (i for i, line in enumerate(lines) if line.speaker == USER_SPEAKER), default=-1
    )
    for line in lines[last_user + 1:]:
        if line.speaker in by_id:
            return by_id[line.speaker]
    for line in reversed(lines):
        if line.speaker in by_id:
            return by_id[line.speaker]
    return None


# ---- small helpers -------------------------------------------------------------------


def _context(lines: Sequence[dict]) -> str:
    """The recent lines as the model reads them: Chinese subtitles, the user's own Chinese."""
    return "\n".join(
        f"{line.get('name') or '你'}：{line.get('zh') or line['text']}" for line in lines
    )


def _clean_line(answer: str) -> str:
    text = answer.strip().strip("「」\"'")
    text = " ".join(text.split())
    return text[:MAX_LINE_CHARS]


_PHOTO = re.compile(r"<photo>(.*?)</photo>", re.S)
"""The group's optional picture tag in a model answer."""


def _parse_line(answer: str) -> Said | None:
    """Her line as the model wrote it: the Japanese and its subtitle in `<ja>`/`<zh>`, or,
    for a model that wrote it plainly, the whole answer as the Japanese."""
    ja = _JA.search(answer)
    zh = _ZH.search(answer)
    if ja is None and zh is None:
        text = _clean_line(answer)
        return Said(text, "") if text else None
    japanese = _clean_line(ja.group(1)) if ja else ""
    subtitle = _clean_line(zh.group(1)) if zh else ""
    if not japanese:
        return None
    return Said(japanese, subtitle)


def _parse_scores(answer: str, ids: Sequence[str]) -> dict[str, float]:
    """The JSON object in `answer`, keeping only the members and clamping to 0-1. The models
    sometimes key by `id｜name` or `id（name）`; the id is taken from before the separator."""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        raw = json.loads(answer[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    by_key: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, (int, float)):
            continue
        by_key[key.strip()] = float(value)
        for separator in ("｜", "|", "（", "(", ":", "："):
            if separator in key:
                by_key.setdefault(key.split(separator)[0].strip(), float(value))
    scores: dict[str, float] = {}
    for cid in ids:
        if cid in by_key:
            scores[cid] = max(0.0, min(1.0, by_key[cid]))
    return scores
