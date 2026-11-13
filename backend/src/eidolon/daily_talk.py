"""The first talk of a day in one of her work or life slots (GLOSSARY.md「行程」):
what the producer says to her while her day has her at work or living, once a day.

A day's first user turn taken while she is in a slot of her schedule raises her affinity
by `SLOT_TALK_STEP`, however long the turn is and whether or not it is judged to be kind:
the reward is for the producer coming to find her where her day has her, not for what he
says. It is kept apart from turns' daily gains, and never raises her out of a cooldown.

It is claimed only when the turn is recorded, in the transaction that records it, so a
turn that fails or is rejected leaves the day's one talk unclaimed. A user's first talk of
the day claim is kept by `user_id`, `character_id` and `day`: a second talk, in that slot
or another, changes nothing.
"""

import sqlite3
from datetime import UTC, date, time

from eidolon.affinity import Affinities
from eidolon.clock import Clock
from eidolon.data_access import Database

SCHEMA = """
-- The day the producer first came to find her while her schedule had her working or
-- living, and the slot it was in.
CREATE TABLE IF NOT EXISTS daily_talk_bonuses (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    slot_start TEXT NOT NULL,
    counted_at TEXT NOT NULL,
    PRIMARY KEY (user_id, character_id, day)
);
"""


class DailyTalkBonuses:
    """The producer's first talk of a day in one of her work or life slots, kept in `db`."""

    def __init__(self, db: Database, affinities: Affinities, clock: Clock) -> None:
        self._db = db
        self._affinities = affinities
        self._clock = clock
        db.prepare(_open)

    def claim(
        self, db: sqlite3.Connection, user_id: str, character_id: str, day: date, slot: time
    ) -> bool:
        """Raise her affinity for the day's first talk in a slot, through `db`, in the
        transaction the turn is recorded in: whether it was the first, and so whether it
        rose. A day already claimed changes nothing."""
        inserted = db.execute(
            "INSERT OR IGNORE INTO daily_talk_bonuses"
            " (user_id, character_id, day, slot_start, counted_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                user_id,
                character_id,
                day.isoformat(),
                slot.strftime("%H:%M"),
                self._clock().astimezone(UTC).isoformat(),
            ),
        ).rowcount
        if not inserted:
            return False
        self._affinities.slot_talk(db, user_id, character_id)
        return True


def _open(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
