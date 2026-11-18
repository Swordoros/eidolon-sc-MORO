"""The last half hour of the other kind of chat, for a turn that may use it.

A private chat may hear what was just said in the producer's most recently active group
that the character is in; a group speaker may hear what she and the producer just said in
private. Only the last five rounds are carried, only Chinese, and only the speaker of a
group sees her own private lines.
"""

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from eidolon.characters import Character
from eidolon.data_access import Database

WINDOW = timedelta(minutes=30)
"""How recently the other chat must have been active."""
ROUNDS = 5
"""How many user turns, with what followed them, are carried."""
MAX_CHARS = 600
"""How much of it is carried at most."""
LIMIT = 80
"""How many rows are read before the last rounds are picked out."""
USER = "制作人"


async def recent_group_context(
    db: Database, user_id: str, character_id: str, characters: Mapping[str, Character],
    now: datetime,
) -> str | None:
    """The last `ROUNDS` rounds of the producer's most recently active group that
    `character_id` is in and that was active within `WINDOW`, Chinese only; None if
    there is none."""
    cutoff = (now - WINDOW).isoformat()
    row = await db.read(_latest_group, user_id, character_id, cutoff)
    if row is None:
        return None
    group_id, name = row
    rows = await db.read(_group_lines, user_id, group_id, cutoff)
    lines = _rounds(rows, "user")
    if not lines:
        return None
    said: list[str] = []
    for speaker, text, zh in lines:
        if speaker == "user":
            said.append(f"制作人：{text}")
        else:
            character = characters.get(speaker)
            who = (character.name_zh or character.name) if character else speaker
            if zh:
                said.append(f"{who}：{zh}")
    if not said:
        return None
    body = _fit(f"群「{name}」：", said)
    return f"# 最近半小时的群聊\n\n{body}" if body else None


async def recent_private_context(
    db: Database, user_id: str, character_id: str, now: datetime,
) -> str | None:
    """The last `ROUNDS` rounds of the producer's private chat with `character_id` within
    `WINDOW`, Chinese only; None if there is none."""
    cutoff = (now - WINDOW).isoformat()
    rows = await db.read(_private_lines, user_id, character_id, cutoff)
    lines = _rounds(rows, "user")
    if not lines:
        return None
    said: list[str] = []
    for role, text, zh in lines:
        if role == "user":
            said.append(f"制作人：{text}")
        elif zh:
            said.append(f"你：{zh}")
    if not said:
        return None
    body = _fit("# 只有你知道的私聊", said)
    if not body:
        return None
    return (
        f"{body}\n\n"
        "这是你和制作人的私聊，群里其他人不知道。"
        "不要在群里主动提起、引用、暗示或转述；只在你的语气和态度里自然体现。"
    )


def _latest_group(
    db: sqlite3.Connection, user_id: str, character_id: str, cutoff: str,
) -> tuple[str, str] | None:
    return db.execute(
        "SELECT g.id, g.name"
        " FROM group_chats g"
        " JOIN group_members member ON member.group_id = g.id"
        " JOIN group_topics topic ON topic.group_id = g.id"
        " JOIN group_messages m ON m.topic_id = topic.id"
        " WHERE g.user_id = ? AND member.character_id = ?"
        " AND g.dissolved_at IS NULL AND m.created_at >= ?"
        " GROUP BY g.id, g.name"
        " ORDER BY MAX(m.created_at) DESC LIMIT 1",
        (user_id, character_id, cutoff),
    ).fetchone()


def _group_lines(
    db: sqlite3.Connection, user_id: str, group_id: str, cutoff: str,
) -> list[tuple[str, str, str]]:
    rows = db.execute(
        "SELECT m.speaker, m.text, m.zh"
        " FROM group_messages m"
        " JOIN group_topics topic ON topic.id = m.topic_id"
        " WHERE m.user_id = ? AND topic.group_id = ? AND m.created_at >= ?"
        " ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
        (user_id, group_id, cutoff, LIMIT),
    ).fetchall()
    return [tuple(row) for row in reversed(rows)]


def _private_lines(
    db: sqlite3.Connection, user_id: str, character_id: str, cutoff: str,
) -> list[tuple[str, str, str]]:
    rows = db.execute(
        "SELECT m.role, m.text, m.zh"
        " FROM messages m JOIN conversations c ON c.id = m.conversation_id"
        " WHERE c.user_id = ? AND c.character_id = ? AND m.created_at >= ?"
        " ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
        (user_id, character_id, cutoff, LIMIT),
    ).fetchall()
    return [tuple(row) for row in reversed(rows)]


def _rounds(rows: Sequence[tuple[str, str, str]], user: str) -> list[tuple[str, str, str]]:
    """The last `ROUNDS` user turns in `rows`, each with the lines before the next one."""
    starts = [index for index, row in enumerate(rows) if row[0] == user]
    if not starts:
        return list(rows[-ROUNDS * 2:])
    return list(rows[starts[max(0, len(starts) - ROUNDS)]:])


def _fit(header: str, lines: Sequence[str]) -> str:
    """`header` and as many of the latest `lines` as fit in `MAX_CHARS`."""
    kept: list[str] = []
    used = len(header)
    for line in reversed(lines):
        size = len(line) + (1 if kept else 0)
        if kept and used + size > MAX_CHARS:
            break
        if not kept and size > MAX_CHARS:
            kept.append(line[:MAX_CHARS])
            break
        kept.append(line)
        used += size
    if not kept:
        return ""
    return header + "\n" + "\n".join(reversed(kept))
