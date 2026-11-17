from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import asyncio

from eidolon.cross_chat import recent_group_context, recent_private_context


class FakeDB:
    def __init__(self, connection):
        self.connection = connection

    async def read(self, body, *args):
        return body(self.connection, *args)


def tables(connection):
    connection.executescript(
        """
        CREATE TABLE group_chats (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT, is_on INTEGER,
            created_at TEXT, last_topic_at TEXT, next_topic_at TEXT,
            dissolved_at TEXT, seen_at TEXT
        );
        CREATE TABLE group_members (group_id TEXT, character_id TEXT);
        CREATE TABLE group_topics (
            id TEXT PRIMARY KEY, group_id TEXT, user_id TEXT,
            raised_by TEXT, text TEXT, created_at TEXT, closed_at TEXT
        );
        CREATE TABLE group_messages (
            id INTEGER PRIMARY KEY, topic_id TEXT, user_id TEXT,
            speaker TEXT, text TEXT, zh TEXT, created_at TEXT
        );
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, user_id TEXT, character_id TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, conversation_id TEXT, role TEXT,
            text TEXT, ja TEXT, zh TEXT, created_at TEXT
        );
        """
    )


def test_group_context_takes_only_the_latest_five_rounds():
    import sqlite3

    now = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)
    connection = sqlite3.connect(":memory:")
    tables(connection)
    connection.execute(
        "INSERT INTO group_chats VALUES ('g', 'u', '测试群', 1, '', NULL, NULL, NULL, NULL)"
    )
    connection.execute("INSERT INTO group_members VALUES ('g', 'a')")
    connection.execute(
        "INSERT INTO group_topics VALUES ('t', 'g', 'u', 'user', '', '', NULL)"
    )
    rows = []
    for n in range(7):
        at = (now - timedelta(minutes=30 - n * 2)).isoformat()
        rows.extend([
            (n * 2 + 1, "t", "u", "user", f"第{n}轮", "", at),
            (n * 2 + 2, "t", "u", "a", f"回答{n}", f"回答{n}", at),
        ])
    connection.executemany(
        "INSERT INTO group_messages VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )
    characters = {"a": SimpleNamespace(name="A", name_zh="小A")}

    result = asyncio.run(
        recent_group_context(FakeDB(connection), "u", "a", characters, now)
    )

    assert result is not None
    assert result.startswith("# 最近半小时的群聊")
    assert "群「测试群」" in result
    assert "第2轮" in result and "第0轮" not in result
    assert "小A：回答6" in result


def test_private_context_only_keeps_chinese_lines_and_five_rounds():
    import sqlite3

    now = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)
    connection = sqlite3.connect(":memory:")
    tables(connection)
    connection.execute("INSERT INTO conversations VALUES ('c', 'u', 'a')")
    rows = []
    for n in range(7):
        at = (now - timedelta(minutes=30 - n * 2)).isoformat()
        rows.extend([
            (n * 2 + 1, "c", "user", f"第{n}轮", None, None, at),
            (n * 2 + 2, "c", "character", f"回答{n}", None, f"回答{n}", at),
        ])
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )

    result = asyncio.run(
        recent_private_context(FakeDB(connection), "u", "a", now)
    )

    assert result is not None
    assert result.startswith("# 只有你知道的私聊")
    assert "第2轮" in result and "第0轮" not in result
    assert "你：回答6" in result
    assert "不要在群里主动提起" in result
