"""The strength, decay and emotion of memories (GLOSSARY.md「记忆」): each memory written
down carries an emotion and a strength tier, whose initial strength holds for the tier's
maintenance and then decays hourly; recall orders by strength and forgets what has faded."""

import sqlite3
from datetime import UTC, datetime, timedelta

from eidolon.memory import TIERS, _decay, _expire, _reinforced, parse_memories

NOW = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)


def test_emotion_and_tier_are_parsed():
    wrote = parse_memories(
        '{"memories": [{"text": "制作人养了一只猫", "scope": "shared",'
        ' "emotion": "开心", "tier": "strong"}]}'
    )
    [memory] = wrote
    assert (memory.emotion, memory.tier) == ("开心", "strong")


def test_a_missing_emotion_and_an_unknown_tier_fall_back():
    wrote = parse_memories('{"memories": [{"text": "制作人喜欢咖啡", "tier": "史诗级"}]}')
    [memory] = wrote
    assert memory.emotion is None and memory.tier == "medium"


def test_tiers_hold_their_strength_for_their_maintenance():
    assert TIERS["strong"] == (1.0, timedelta(hours=36))
    assert TIERS["medium"] == (0.7, timedelta(hours=24))
    assert TIERS["weak"] == (0.5, timedelta(hours=12))


def test_a_mention_extends_expiry_by_thirty_percent_and_caps_it():
    natural = timedelta(hours=12)
    current = (NOW + timedelta(hours=12)).isoformat()

    once = _reinforced("short", current, NOW, natural, False)
    capped = _reinforced("short", (NOW + timedelta(hours=35)).isoformat(), NOW, natural, False)
    explicit = _reinforced("short", current, NOW, natural, True)

    assert once == NOW + timedelta(hours=15, minutes=36)
    assert capped == NOW + timedelta(hours=36)
    assert explicit == NOW + timedelta(hours=36)


def test_expiry_deletes_short_and_mid_and_keeps_long_dormant():
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, kind TEXT, status TEXT,"
        " superseded_by INTEGER, expires_at TEXT, forgotten_at TEXT)"
    )
    db.execute("CREATE TABLE memory_vectors (memory_id INTEGER)")
    db.execute(
        "INSERT INTO memories (id, kind, status, expires_at, forgotten_at) VALUES"
        " (1, 'short', 'active', ?, NULL), (2, 'mid', 'active', ?, NULL),"
        " (3, 'long', 'active', ?, NULL), (4, 'long', 'dormant', NULL, ?)",
        ((NOW - timedelta(hours=1)).isoformat(), (NOW - timedelta(hours=1)).isoformat(),
         (NOW - timedelta(hours=1)).isoformat(), (NOW - timedelta(days=91)).isoformat()),
    )

    _expire(db, NOW.isoformat(), (NOW - timedelta(days=90)).isoformat())

    assert db.execute("SELECT id FROM memories ORDER BY id").fetchall() == [(3,)]
    assert db.execute("SELECT status, forgotten_at FROM memories").fetchone() == (
        "dormant", NOW.isoformat()
    )


def test_decay_weakens_what_is_past_its_maintenance_only():
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT, strength REAL,"
        " maintain_until TEXT, kind TEXT NOT NULL DEFAULT 'short')"
    )
    db.execute(
        "INSERT INTO memories (id, text, strength, maintain_until, kind) VALUES"
        " (1, 'past', 1.0, '2026-11-01T00:00:00+00:00', 'short'),"
        " (2, 'held', 0.8, '2026-11-09T00:00:00+00:00', 'short'),"
        " (3, 'faded', 0.05, NULL, 'short'),"
        " (4, 'long', 1.0, NULL, 'long')"
    )
    db.commit()

    _decay(db, NOW.isoformat())

    strengths = dict(db.execute("SELECT text, strength FROM memories").fetchall())
    assert strengths["past"] == 0.9  # past its maintenance: decayed once
    assert strengths["held"] == 0.8  # still maintained: untouched
    assert strengths["faded"] == 0.05  # already below the threshold: left as it is
    assert strengths["long"] == 1.0  # long-term memories do not decay
