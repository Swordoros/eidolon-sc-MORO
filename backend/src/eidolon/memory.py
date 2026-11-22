"""Memory: what users tell the characters, kept across conversations.

After each turn the background model reads the exchange and writes down, in Chinese,
whatever the user revealed about themselves that is worth keeping. Each memory belongs
to the user and names the character it was told to, its source character. All of a
user's characters share their memories, and a character finding one told to someone
else is asked to pass it on as hearsay (ADR-0002); users never share theirs.

A turn goes `begin` → `candidates` → `find` → `write_down`. The turn's query (see
`memory_query`) is embedded once, by whoever takes the turn (`retrieval`), and the vector
serves all of them: the candidates are the user's holding memories nearest it, found
before anyone knows whether the turn needs memories at all, which only `find` reranks;
and writing down shows the background model the memories nearest it. Writing runs in
the background so it never holds up the reply; the user's next turn begins by waiting briefly for it, so what they
just said is remembered by the time they go on. A new conversation picks up where
earlier ones left off only through the memories it finds.

What a user says may update what they said before: writing it down, the background model
is shown the memories nearest the turn and says which of them the new memory supersedes
(ADR-0014). A superseded memory is neither found nor shown to the background model again,
but stays listed until the user deletes it; deleting what superseded it brings it back,
unless that was superseded in turn, by what superseded that.

Users can list their memories and delete them one by one or all at once, never add or
edit them. Listing and deleting wait briefly for what is being written down too;
whatever turns begun before a clearing write down after it is dropped, so a cleared
memory never turns up again.

Each memory is embedded as it is written down, and the vector kept until the memory is
gone; a memory whose embedding failed is embedded the next time its user's memories are
searched. After the embedding model changes, `eidolon reembed` embeds them all again.

Each turn writes down with the model it began with (see `OwnKeys.for_turn`). Without an
embedder and reranker, memory is off: turns find and write down nothing, but users can
still list and delete the memories already kept.
"""

import asyncio
import json
import logging
import math
import re
import sqlite3
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Literal, Protocol

from eidolon.activity import detached
from eidolon.characters import Character, chinese_name_map
from eidolon.clock import Clock
from eidolon.corpus import REEMBED_BATCH
from eidolon.data_access import Database
from eidolon.embedding import Document, Embedder, EmbeddingError, nearest, to_blob
from eidolon.llm import ChatLLM, ChatMessage
from eidolon.producer import effective_timezone
from eidolon.promises import Promise, Promises, parse_promise_changes, parse_promises
from eidolon.reply_format import names_instructions
from eidolon.rerank import DEFAULT_THRESHOLDS, Reranker, RerankError
from eidolon.store import Conversation, Message, Store

log = logging.getLogger(__name__)

SETTLE_TIMEOUT = 5.0
"""How long, in seconds, a turn waits for the memories of the user's previous turn to be
written before going on without them."""
SHOWN_MEMORIES = 20
"""How many of the user's holding memories nearest the turn the background model is shown,
numbered, so it neither writes them down again nor misses what the turn supersedes."""
EXCHANGE_HISTORY = 4
"""How many of the messages before the user's latest the background model reads with it."""
MAX_NEW_MEMORIES = 5
"""The most memories one turn writes down."""
MAX_MEMORY_LENGTH = 200
"""How much of a memory, in characters, is kept; longer ones are cut short."""
MAX_MOMENT = 120
"""How long what mattered to her in a turn may be, in characters; a longer one is cut."""
CANDIDATES = 12
"""How many of the user's memories nearest the turn the reranker is offered."""
MAX_FOUND = 5
"""The most memories one turn's prompt carries, after reranking and kind quotas."""
MAX_OWN = 4
"""The most of those that may be the current character's own memories."""
MAX_OTHER = 1
"""The most of those that may be memories another character told her, by scope."""
MAX_OWN_SHORT = 2
"""The short memories of her own a turn aims to carry."""
MAX_OWN_MID = 2
"""The mid memories of her own a turn aims to carry."""
LATELY = timedelta(days=14)
"""How long ago what she reaches out with as his news may have been written down."""
MAX_LATEST = 5
"""The most of his news she reaches out with, besides what is found."""
ANOTHER_IDOL = "事务所的另一位偶像"
"""Who a memory was told to, when that character has been removed from the configuration."""

TIERS: dict[str, tuple[float, timedelta]] = {
    "strong": (1.0, timedelta(hours=36)),
    "medium": (0.7, timedelta(hours=24)),
    "weak": (0.5, timedelta(hours=12)),
}
"""Each strength tier a memory may be written in (GLOSSARY.md「记忆」): its initial strength,
and how long that strength holds before it starts decaying."""

SHORT_CAP = timedelta(hours=36)
MID_LIFE = timedelta(hours=72)
LONG_LIFE = timedelta(days=7)
DORMANT_KEEP = timedelta(days=90)
SHORT_REINFORCE = 0.30
MID_REINFORCE = 0.50
LONG_REINFORCE = 0.50
"""How much of a memory's natural life a fresh mention adds, capped by kind."""


def _life_of(kind: str, maintain_until: str | None = None,
             created_at: str | None = None) -> timedelta:
    """The natural life of a memory of `kind`; a short one by how long its tier held."""
    if kind == "short" and maintain_until is not None and created_at is not None:
        try:
            return datetime.fromisoformat(maintain_until) - datetime.fromisoformat(created_at)
        except ValueError:
            pass
    return {"mid": MID_LIFE, "long": LONG_LIFE}.get(kind, TIERS["medium"][1])


def _cap_of(kind: str) -> timedelta:
    return {"short": SHORT_CAP, "mid": MID_LIFE, "long": LONG_LIFE}.get(
        kind, TIERS["medium"][1]
    )


def _reinforced(kind: str, expires_at: str | None, now: datetime,
                natural: timedelta, explicit: bool) -> datetime:
    """When a memory of `kind` is next forgotten after a mention at `now`, at most a full
    cap from `now`."""
    cap = _cap_of(kind)
    if explicit:
        return now + cap
    current = now
    if expires_at is not None:
        try:
            current = max(current, datetime.fromisoformat(expires_at))
        except ValueError:
            pass
    ratio = SHORT_REINFORCE if kind == "short" else (
        MID_REINFORCE if kind == "mid" else LONG_REINFORCE
    )
    return min(now + cap, current + natural * ratio)
DECAY_FACTOR = 0.9
"""How much of its strength a memory keeps each hour, once its maintenance is over."""
FORGET_THRESHOLD = 0.1
"""A strength at or below which a memory is no longer recalled."""
DECAY_TICK = timedelta(hours=1)
"""How often the memories' strength is decayed."""

CONSOLIDATE_AT = time(3)
"""The local hour of the producer's day at which memories are consolidated."""
CONSOLIDATE_TICK = timedelta(minutes=10)
"""How often the consolidation job looks for a user whose 03:00 has come."""
CONSOLIDATE_WINDOW = timedelta(days=30)
"""How far back the consolidation model reads short and mid memories."""
CONSOLIDATE_MOST = 40
"""How many short and mid memories one consolidation reads."""
LONG_TERM_SHOWN = 5
"""How many long memories the prompt carries, newest first."""
MAX_CONSOLIDATED = 6
"""How many mid or long memories one consolidation may write."""


class _MemoryConfig(Protocol):
    """The live configuration's lease, as consolidation needs the background model."""

    def lease(self) -> Any: ...

Scope = Literal["private", "unit", "shared"]
"""How widely a memory is shared (GLOSSARY.md「记忆」): the source character alone (`private`),
with her unit (`unit`), or with every character (`shared`)."""
SCOPES: tuple[Scope, ...] = ("private", "unit", "shared")
"""Every scope, in the order the frontend offers them."""
Subject = Literal["producer", "self", "both"]
"""What a memory is about: the producer, the source character herself, or both."""
SUBJECTS: tuple[Subject, ...] = ("producer", "self", "both")
"""Every subject, the producer's first."""
Resolution = Literal["replaced", "merged"]
"""How a memory stopped holding: replaced by a newer fact, or merged into a fuller one."""

_TRANSIENT_SELF = re.compile(
    r"正在|正准备|在准备|刚刚|刚才|现在|今天|今晚|等下|待会|明天|这周|本周"
)
"""A self memory about the current day's doings, not something to keep."""

# `memories` predates this module and keeps the columns and constraints it had in the
# store; `memory_vectors` holds each memory's embedding, per embedding model.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    source_character_id TEXT NOT NULL,
    conversation_id TEXT REFERENCES conversations(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_by INTEGER,
    emotion TEXT,
    strength REAL NOT NULL DEFAULT 1,
    maintain_until TEXT,
    kind TEXT NOT NULL DEFAULT 'short',
    subject TEXT NOT NULL DEFAULT 'producer',
    resolution TEXT,
    mention_count INTEGER NOT NULL DEFAULT 1,
    expires_at TEXT,
    last_reinforced_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    forgotten_at TEXT,
    recalled_at TEXT,
    recall_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, text)
);
-- Deleting a conversation clears this from the memories written down in it.
CREATE INDEX IF NOT EXISTS memories_by_conversation ON memories(conversation_id);
CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    PRIMARY KEY (memory_id, model)
);
CREATE INDEX IF NOT EXISTS memory_vectors_by_user ON memory_vectors(user_id, model);
CREATE TABLE IF NOT EXISTS memory_consolidations (
    user_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    last_attempt_at TEXT,
    PRIMARY KEY (user_id, character_id, day)
);
"""


@dataclass(frozen=True)
class Memory:
    """Something about a user worth keeping across conversations, written in Chinese,
    with the character it was told to (its source character)."""

    id: int
    user_id: str
    source_character_id: str
    source_character_name: str | None
    """None if that character has been removed from the configuration."""
    text: str
    created_at: str
    """When it was told, as an ISO timestamp in UTC."""
    superseded_by: int | None = None
    """The memory that superseded it, None while it holds (ADR-0014)."""
    conversation_id: str | None = None
    """The conversation it was told in; None if that is not known or no longer there."""
    scope: Scope = "private"
    """How widely it is shared (GLOSSARY.md「记忆」); private is the safe default."""
    emotion: str | None = None
    """The feeling she had about it as she wrote it down (GLOSSARY.md「记忆」); None without one."""
    strength: float = 1.0
    """How strong the memory is now, 1 at its fullest; decays each hour past its maintenance
    (`maintain_until`), and below `FORGET_THRESHOLD` it is no longer recalled."""
    maintain_until: str | None = None
    """When its strength holds until, an ISO timestamp in UTC; None if it is past, or was
    written before maintenance existed."""
    kind: str = "short"
    """short, mid or long: what consolidation has made of it (GLOSSARY.md「记忆」)."""
    subject: Subject = "producer"
    """What it is about: the producer, the source character, or both."""
    resolution: Resolution | None = None
    """Replaced, merged, or still holding."""
    mention_count: int = 1
    """How many times the user has told her this, as consolidation counts it."""
    expires_at: str | None = None
    """When it is forgotten naturally; None for a memory kept from before expiry."""
    last_reinforced_at: str | None = None
    """When the user last confirmed it."""
    status: str = "active"
    """active, dormant (a forgotten long memory), or retired."""
    forgotten_at: str | None = None
    """When it became dormant."""
    recalled_at: str | None = None
    """When it was last recalled from dormant."""
    recall_count: int = 0
    """How many times it has been recalled."""


PackageKey = int | str
"""What a data package calls one of its memories: a key within the package only."""


@dataclass(frozen=True)
class CarriedMemory:
    """A memory carried in by a data package (GLOSSARY.md「数据包」): `key` and
    `superseded_by` are keys in the package, not ids here."""

    key: PackageKey
    source_character_id: str
    text: str
    created_at: str
    superseded_by: PackageKey | None
    conversation_id: str | None
    """The conversation here it was told in, None if it is not here."""
    scope: Scope = "private"
    """How widely it is shared (GLOSSARY.md「记忆」), as the package carried it."""
    subject: Subject = "producer"
    """What it is about, as the package carried it."""
    resolution: Resolution | None = None
    """Replaced, merged, or still holding."""
    kind: str = "short"
    """short, mid or long, as the package carried it."""
    status: str = "active"
    """active or dormant, as the package carried it."""
    remaining_hours: float | None = None
    """How long it still had when exported, if it had an expiry."""
    recall_count: int = 0
    """How many times a dormant long one had been recalled."""


def add_carried(
    db: sqlite3.Connection, user_id: str, carried: Sequence[CarriedMemory],
    now: datetime | None = None,
) -> int:
    """Add the memories `carried` to the user's through `db`, in the caller's transaction;
    returns how many were new. One whose text the user already has is not added again; what
    supersedes it, or what it says supersedes it, is the one already here. The new ones are
    superseded as the package says, by new ones or ones already here; those already here are
    left as they are, their superseding too. Their source characters are kept as they are,
    here or not. They are embedded afterwards, outside the transaction (`embed_carried`)."""
    now = now or datetime.now(UTC)
    ids: dict[PackageKey, int] = {}
    new: list[CarriedMemory] = []
    for memory in carried:
        kept = db.execute(
            "SELECT id FROM memories WHERE user_id = ? AND text = ?", (user_id, memory.text)
        ).fetchone()
        if kept is not None:
            ids[memory.key] = kept[0]
            continue
        scope = "private" if memory.subject in ("self", "both") else memory.scope
        kind = memory.kind if memory.kind in ("short", "mid", "long") else "short"
        dormant = kind == "long" and memory.status == "dormant"
        if dormant:
            expires = None
        else:
            ends = memory.remaining_hours
            if ends is None or ends <= 0:
                ends = _cap_of(kind).total_seconds() / 3600
            expires = (now + timedelta(hours=ends)).astimezone(UTC).isoformat()
        cursor = db.execute(
            "INSERT INTO memories"
            " (user_id, source_character_id, conversation_id, text, created_at, scope, subject,"
            " resolution, kind, status, expires_at, last_reinforced_at, forgotten_at,"
            " recall_count)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, memory.source_character_id, memory.conversation_id, memory.text,
             memory.created_at, scope, memory.subject, memory.resolution, kind,
             "dormant" if dormant else "active", expires, now.astimezone(UTC).isoformat(),
             now.astimezone(UTC).isoformat() if dormant else None,
             memory.recall_count),
        )
        assert cursor.lastrowid is not None
        ids[memory.key] = cursor.lastrowid
        new.append(memory)
    db.executemany(
        "UPDATE memories SET superseded_by = ?, resolution = ? WHERE id = ?",
        [
            (ids[m.superseded_by], m.resolution or "replaced", ids[m.key])
            for m in new
            if m.superseded_by is not None and ids[m.superseded_by] != ids[m.key]
        ],
    )
    return len(new)


@dataclass(frozen=True)
class _MergeGroup:
    """One canonical memory a merge pass makes from new and shown memories."""

    text: str
    subject: Subject
    scope: Scope
    emotion: str | None
    tier: str
    new_ids: tuple[int, ...]
    old_ids: tuple[int, ...]


@dataclass(frozen=True)
class _Services:
    """What memory needs to be on."""

    embedder: Embedder
    reranker: Reranker
    min_relevance: Callable[[], float]


class Memories:
    """Every user's memories."""

    label = "记忆"
    unit = "条"

    def __init__(
        self,
        db: Database,
        characters: Mapping[str, Character],
        clock: Clock,
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        min_relevance: Callable[[], float] = lambda: DEFAULT_THRESHOLDS.memories,
        promises: Promises | None = None,
        share_group: Callable[[str], frozenset[str]] = lambda _: frozenset(),
        store: Store | None = None,
        config: _MemoryConfig | None = None,
    ) -> None:
        """Memories kept in `db`, next to the users and conversations they belong to; off
        unless `embedder` and `reranker` are both given, though `embedder` alone is enough to
        embed them again. A memory the reranker scores below what `min_relevance` says when
        it is found is not (see `rerank.KNOWN_THRESHOLDS`). The promises the same answer
        writes down go to `promises`, if there are any to keep. `share_group` says who, by
        id, a character's `unit` memories reach: her unit and, for the clerk, her sister
        (GLOSSARY.md「记忆」)."""
        self._db = db
        db.prepare(_open)
        self._characters = characters
        self._names = chinese_name_map(characters.values())
        self._clock = clock
        self._promises = promises
        self._share_group = share_group
        self._store = store
        self._config = config
        self._moments: list[Callable[[str, str, str, datetime], Awaitable[None]]] = []
        self._embedder = embedder
        self._services = (
            _Services(embedder, reranker, min_relevance)
            if embedder is not None and reranker is not None
            else None
        )
        self._pending: dict[str, set[asyncio.Task[None]]] = {}  # user id → writing
        self._clearings: dict[str, int] = {}  # user id → how many times cleared

    def on_moment(self, listener: Callable[[str, str, str, datetime], Awaitable[None]]) -> None:
        """Have `listener` told what the background model found mattered to her in a turn
        (`parse_moment`): the user, the character, what, and when, in their own day."""
        self._moments.append(listener)

    async def begin(
        self,
        conversation: Conversation,
        timezone: str,
        llm: ChatLLM,
        hold: Callable[[], Callable[[], None]] | None = None,
    ) -> "MemoryTurn":
        """Begin a turn of `conversation`, whose user is in `timezone`, once what their
        earlier turns are writing down is written (a while at most). The turn writes down
        with `llm`, held on to with `hold` (see `TurnLLMs.hold_background`) until written."""
        clearings = self._clearings.get(conversation.user_id, 0)
        await self._settle(conversation.user_id)
        return MemoryTurn(self, conversation, timezone, clearings, llm, hold)

    async def entries(self, user_id: str) -> list[Memory]:
        """All of the user's memories, newest first, including what their turns so far
        are writing down (unless that takes too long). Not named `list`, which would
        shadow the builtin in this class's annotations."""
        await self._settle(user_id)
        return list(reversed(await self.kept(user_id)))

    async def delete(self, user_id: str, memory_id: int) -> bool:
        """Forget one of the user's memories; False if they have no such memory."""
        await self._settle(user_id)
        return await self._db.write(_delete, user_id, memory_id)

    async def edit(self, user_id: str, memory_id: int, text: str) -> bool:
        """Replace the text of one of the user's active memories; False if they have no such
        memory. Its embedding is dropped and made again when next searched."""
        await self._settle(user_id)
        return await self._db.write(_edit, user_id, memory_id, text[:MAX_MEMORY_LENGTH])

    async def set_scope(self, user_id: str, memory_id: int, scope: Scope) -> bool:
        """Change how widely one of the user's memories is shared (GLOSSARY.md「记忆」);
        False if they have no such memory."""
        await self._settle(user_id)
        return await self._db.write(_set_scope, user_id, memory_id, scope)

    async def clear(self, user_id: str) -> None:
        """Forget all of the user's memories, including those their turns so far are still
        writing down."""
        self._clearings[user_id] = self._clearings.get(user_id, 0) + 1
        await self._settle(user_id)
        await self._db.write(_clear, user_id)

    async def forget(self, user_id: str) -> None:
        """Drop whatever the user's turns so far are still writing down, before the user
        is deleted (see `database.delete_user_data`, which deletes their memories)."""
        self._clearings[user_id] = self._clearings.get(user_id, 0) + 1
        await self._settle(user_id)

    async def decay(self) -> None:
        """Weaken every memory whose maintenance is over, once (`DECAY_FACTOR`); a memory at
        or below `FORGET_THRESHOLD` is no longer recalled (GLOSSARY.md「记忆」)."""
        await self._db.write(_decay, self._clock().astimezone(UTC).isoformat())

    async def keep_decaying(self) -> None:
        """`decay` at every `DECAY_TICK`, until cancelled."""
        while True:
            await asyncio.sleep(DECAY_TICK.total_seconds())
            try:
                await self.decay()
            except Exception:
                log.exception("decaying memories failed")

    async def keep_consolidating(self) -> None:
        """Consolidate each user's memories at 03:00 in their own time zone, until cancelled."""
        while True:
            await asyncio.sleep(CONSOLIDATE_TICK.total_seconds())
            try:
                await self.consolidate()
            except Exception:
                log.exception("consolidating memories failed")

    async def consolidate(self, now: datetime | None = None) -> None:
        """At 03:00 in each user's own day, have the background model summarize the short
        and mid memories of each character into mid and long ones, once a day."""
        if self._store is None:
            return
        now = now or self._clock()
        await self._db.write(
            _expire, now.astimezone(UTC).isoformat(),
            (now - DORMANT_KEEP).astimezone(UTC).isoformat(),
        )
        if self._config is None:
            return
        for user in await self._store.list_users():
            profile = await self._store.get_profile(user.id)
            zone = ZoneInfo(effective_timezone(profile, None))
            local = now.astimezone(zone)
            if local.time() < CONSOLIDATE_AT:
                continue
            day = local.date()
            await self._settle(user.id)
            memories = await self.kept(user.id)
            since = now - CONSOLIDATE_WINDOW
            characters = {
                m.source_character_id for m in memories
                if m.superseded_by is None and m.kind in ("short", "mid")
                and datetime.fromisoformat(m.created_at) >= since
            }
            for character_id in sorted(characters):
                run = await self._db.read(
                    _consolidation_run, user.id, character_id, day.isoformat()
                )
                if run is not None and (run[1] != "pending" or run[0] >= 5):
                    continue
                try:
                    done = await self._consolidate(user.id, character_id, memories, now, zone)
                except Exception:
                    log.warning("consolidating memories for %s failed", user.id,
                                exc_info=True)
                    done = False
                await self._db.write(
                    _record_attempt, user.id, character_id, day.isoformat(), done, now
                )

    async def _consolidate(
        self,
        user_id: str,
        character_id: str,
        memories: Sequence[Memory],
        now: datetime,
        zone: ZoneInfo,
    ) -> bool:
        """Ask the background model to summarize one character's memories; True once it has
        answered, whether or not it found anything to keep."""
        since = now - CONSOLIDATE_WINDOW
        short = [
            m for m in memories
            if m.source_character_id == character_id and m.superseded_by is None
            and m.kind in ("short", "mid")
            and m.status in ("active", "expired")
            and datetime.fromisoformat(m.created_at) >= since
        ][-CONSOLIDATE_MOST:]
        long = [
            m for m in memories
            if m.source_character_id == character_id and m.superseded_by is None
            and m.kind == "long" and m.status == "active"
        ]
        dormant_all = [
            m for m in memories
            if m.source_character_id == character_id and m.kind == "long"
            and m.status == "dormant"
        ]
        if not short:
            return True
        dormant = await self._dormant_candidates(short, dormant_all)
        shown = [*long, *short, *dormant]
        messages = _consolidation_prompt(shown, now, zone)
        lease = self._config.lease()
        try:
            answer = "".join([
                item async for item in lease.keys.memory_llm.stream(messages)
                if isinstance(item, str)
            ])
        finally:
            lease.release()
        items = _parse_consolidations(answer, shown)
        recall = _parse_recall(answer, shown)
        reinforce = _parse_reinforce(answer, shown)
        if items is None or recall is None or reinforce is None:
            return False
        if items or recall or reinforce:
            await self._db.write(
                _apply_consolidations,
                user_id, character_id, shown, items, reinforce, recall, now,
            )
        await self._db.write(
            _delete_expired, user_id, character_id, now.astimezone(UTC).isoformat()
        )
        return True

    async def _dormant_candidates(
        self, short: Sequence[Memory], dormant: Sequence[Memory]
    ) -> list[Memory]:
        """The dormant long memories nearest the short and mid ones being consolidated; the
        newest ten when reranking is off or fails."""
        if not dormant:
            return []
        if (services := self._services) is None:
            return list(dormant[-10:])
        query = "\n".join(m.text for m in short[-10:])
        try:
            best = await services.reranker.rerank(
                query, [m.text for m in dormant], len(dormant)
            )
        except RerankError:
            log.warning("finding dormant memories failed; using the newest ten", exc_info=True)
            return list(dormant[-10:])
        minimum = services.min_relevance()
        picked = [
            dormant[i] for i, score in best[:8] if score >= minimum
        ]
        return picked

    async def embed_missing(
        self,
        batch: int = REEMBED_BATCH,
        progress: Callable[[int], None] | None = None,
        *,
        user_id: str | None = None,
    ) -> int:
        """Embed every user's memories, or only those of `user_id`, that have no vector from
        the embedder given, `batch` at a time; returns how many it embedded, telling
        `progress` the count so far after each batch. Interrupted, what it finished is kept;
        a memory deleted meanwhile gets no vector."""
        embedder = self._reembedder()
        model = embedder.model
        done = 0
        after = 0
        while True:
            rows = await self._db.read(_unembedded, after, user_id, model, batch)
            if not rows:
                return done
            vectors = await embedder.embed_documents([Document(text) for _, _, text in rows])
            await self._db.write(_store_embedded, model, [
                (id, user_id, to_blob(v))
                for (id, user_id, _), v in zip(rows, vectors, strict=True)
            ])
            done += len(rows)
            after = rows[-1][0]
            if progress is not None:
                progress(done)

    def embed_carried(self, user_id: str) -> None:
        """Embed, in the background, the user's memories that have no vector yet, as those
        carried in by a data package (`add_carried`), with the deployer's embedder; those it
        fails to embed are embedded the next time the user's memories are searched. Their
        turns, listing and deleting wait briefly for it, as for what is being written down.
        Nothing to do while memory is off."""
        if self._embedder is None:
            return

        async def embed() -> None:
            try:
                await self.embed_missing(user_id=user_id)
            except Exception:
                log.warning("embedding carried memories failed; they are embedded when"
                            " searched", exc_info=True)

        task = asyncio.create_task(embed(), context=detached())
        self._hold(user_id, task)

    async def forget_other_models(self) -> int:
        """Drop every memory's vector not from the embedder given; returns how many there
        were."""
        return await self._db.write(_forget_other_models, self._reembedder().model)

    def _reembedder(self) -> Embedder:
        """The embedder given, which embedding the memories again needs."""
        if self._embedder is None:
            raise RuntimeError("embedding memories again needs the embedder they were opened with")
        return self._embedder

    async def aclose(self) -> None:
        """Let whatever is being written down finish, a while at most, then give up on it."""
        tasks = {t for pending in self._pending.values() for t in pending}
        if tasks:
            _, unfinished = await asyncio.wait(tasks, timeout=SETTLE_TIMEOUT)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*unfinished, return_exceptions=True)

    async def _settle(self, user_id: str) -> None:
        if pending := self._pending.get(user_id):
            await asyncio.wait(set(pending), timeout=SETTLE_TIMEOUT)

    async def _candidates(
        self, user_id: str, query_vector: Sequence[float], character_id: str
    ) -> list[Memory]:
        if (services := self._services) is None:
            return []
        try:
            return await self._nearest(
                services.embedder, user_id, query_vector, CANDIDATES, character_id, scope=True
            )
        except EmbeddingError:
            log.warning("embedding memories failed; replying without them", exc_info=True)
            return []

    async def _find(
        self,
        conversation: Conversation,
        timezone: str,
        query_text: str,
        candidates: Sequence[Memory],
    ) -> str | None:
        found = await self._relevant(query_text, candidates, conversation.character_id)
        character = self._characters[conversation.character_id]
        return _memories_prompt(found, character, timezone, _FOUND, self._clock()) if found else None

    async def _relevant(
        self, query_text: str, candidates: Sequence[Memory], character_id: str,
    ) -> list[Memory]:
        """As many as `MAX_FOUND` of the `candidates` relevant to `query_text`, in the
        reranker's relevance order; none if memory is off or reranking failed. Relevance
        filters (the reranker drops what is off-topic), then short and mid quotas keep a
        recent short from being pushed out by older mids. At most `MAX_OWN` may be the
        character's own, and at most `MAX_OTHER` another's; the others are never used to
        fill her unused share."""
        if (services := self._services) is None or not candidates:
            return []
        try:
            best = await services.reranker.rerank(
                query_text, [m.text for m in candidates], len(candidates)
            )
        except RerankError:
            log.warning("finding memories failed; going on without them", exc_info=True)
            return []
        min_relevance = services.min_relevance()
        relevant = [
            candidates[i] for i, score in best
            if score >= min_relevance and candidates[i].strength >= FORGET_THRESHOLD
            and candidates[i].kind != "long" and _alive(candidates[i], self._clock())
        ]
        own = [m for m in relevant if m.source_character_id == character_id]
        other = [m for m in relevant if m.source_character_id != character_id]
        short = [m for m in own if m.kind == "short"]
        mid = [m for m in own if m.kind == "mid"]
        chosen_own = [*short[:MAX_OWN_SHORT], *mid[:MAX_OWN_MID]]
        for memory in own:
            if len(chosen_own) >= MAX_OWN:
                break
            if memory not in chosen_own:
                chosen_own.append(memory)
        chosen = [*chosen_own[:MAX_OWN], *other[:MAX_OTHER]]
        position = {id(memory): n for n, memory in enumerate(relevant)}
        chosen.sort(key=lambda memory: position.get(id(memory), len(relevant)))
        return chosen[:MAX_FOUND]

    async def for_reaching_out(
        self,
        conversation: Conversation,
        timezone: str,
        last_turn: str | None,
        posting: bool = False,
    ) -> str | None:
        """The prompt section carrying the memories she reaches out with in `conversation`,
        whose user is in `timezone` (GLOSSARY.md「搭话」), or, `posting`, posts a moment with
        (GLOSSARY.md「朋友圈」), told only to echo them: of those the user told her herself
        that still hold, those relevant to `last_turn`, as a turn finds them (`last_turn_query`),
        then as many as `MAX_LATEST` of the latest written down within `LATELY`, each once;
        None if there are none, or memory is off."""
        if (services := self._services) is None:
            return None
        user_id, character_id = conversation.user_id, conversation.character_id
        await self._settle(user_id)
        found: list[Memory] = []
        if last_turn is not None:
            try:
                query_vector = await services.embedder.embed_query(last_turn)
                candidates = await self._nearest(
                    services.embedder, user_id, query_vector, CANDIDATES, character_id
                )
            except EmbeddingError:
                log.warning("embedding memories failed; reaching out with the latest only",
                            exc_info=True)
                candidates = []
            found = await self._relevant(last_turn, candidates, conversation.character_id)
        now = self._clock()
        since = (now - LATELY).astimezone(UTC)
        latest = [
            m for m in _holding(await self.kept(user_id), character_id)
            if datetime.fromisoformat(m.created_at) >= since and m.kind != "long"
            and _alive(m, self._clock())
        ][-MAX_LATEST:]
        ids = {m.id for m in found}
        recalled = [*found, *(m for m in reversed(latest) if m.id not in ids)]
        character = self._characters[character_id]
        return (
            _memories_prompt(
                recalled, character, timezone, _POSTING if posting else _REACHING_OUT, now
            )
            if recalled else None
        )

    def _visible(self, memories: Sequence[Memory], character_id: str) -> list[Memory]:
        """Those of `memories` that still hold and `character_id` may recall: her own
        whatever the scope, another's only where shared, or unit and among who the source
        shares it with."""
        visible = []
        for memory in memories:
            if memory.superseded_by is not None or not _alive(memory, self._clock()):
                continue
            if memory.source_character_id == character_id or memory.scope == "shared":
                visible.append(memory)
            elif memory.scope == "unit":
                if character_id in self._share_group(memory.source_character_id):
                    visible.append(memory)
        return visible

    async def _nearest(
        self,
        embedder: Embedder,
        user_id: str,
        query_vector: Sequence[float],
        limit: int,
        character_id: str | None = None,
        *,
        scope: bool = False,
    ) -> list[Memory]:
        """As many as `limit` of the user's holding memories nearest a query embedded with
        `embedder`, nearest first; only those told to `character_id` if given, or, `scope`,
        those `character_id` may recall by scope. Raises EmbeddingError, embedding those not
        embedded yet."""
        memories, stored = await self._db.read(
            self._kept_with_vectors, user_id, embedder.model
        )
        vectors = await self._vectors(embedder, user_id, memories, stored)
        holding = (
            self._visible(memories, character_id) if scope else _holding(memories, character_id)
        )
        found = await asyncio.to_thread(
            nearest, [vectors[m.id] for m in holding], query_vector, limit
        )
        return [holding[i] for i, _ in found]

    async def _shown(
        self,
        embedder: Embedder,
        user_id: str,
        character_id: str,
        query_text: str,
        query_vector: Sequence[float] | None,
    ) -> list[Memory]:
        """The memories the background model is shown: those `character_id` may recall
        (`_visible`) nearest `query_text`, embedded unless it already was (`query_vector`),
        or while that cannot be embedded, the latest of them. It is shown no memory it
        cannot recall, so it never supersedes one told to someone else beyond its scope
        (ADR-0014 with scope, ADR-0002)."""
        try:
            if query_vector is None:
                query_vector = await embedder.embed_query(query_text)
            return await self._nearest(
                embedder, user_id, query_vector, SHOWN_MEMORIES, character_id, scope=True
            )
        except EmbeddingError:
            log.warning("embedding the turn failed; the background model is shown the latest"
                        " memories", exc_info=True)
            return self._visible(await self.kept(user_id), character_id)[-SHOWN_MEMORIES:]

    def _write_down(
        self,
        conversation: Conversation,
        timezone: str,
        clearings: int,
        llm: ChatLLM,
        hold: Callable[[], Callable[[], None]] | None,
        history: Sequence[Message],
        text: str,
        reply: Message,
        query_vector: Sequence[float] | None,
    ) -> None:
        if (services := self._services) is None:
            return
        now = self._clock().astimezone(ZoneInfo(timezone))
        release = hold() if hold is not None else None
        task = asyncio.create_task(
            self._write(
                services, llm, conversation, clearings, history, text, reply, query_vector, now,
                timezone,
            ),
            context=detached(),  # after the turn, not part of it (see `activity`)
        )
        self._hold(conversation.user_id, task)
        if release is not None:
            task.add_done_callback(lambda _: release())

    def _hold(self, user_id: str, task: asyncio.Task[None]) -> None:
        """Have the user's turns, listing and deleting wait (a while) for `task`."""
        pending = self._pending.setdefault(user_id, set())
        pending.add(task)

        def done(task: asyncio.Task[None]) -> None:
            pending.discard(task)
            if not pending and self._pending.get(user_id) is pending:
                del self._pending[user_id]

        task.add_done_callback(done)

    async def _write(
        self,
        services: _Services,
        llm: ChatLLM,
        conversation: Conversation,
        clearings: int,
        history: Sequence[Message],
        text: str,
        reply: Message,
        query_vector: Sequence[float] | None,
        now: datetime,
        timezone: str,
    ) -> None:
        user_id = conversation.user_id
        try:
            character = self._characters[conversation.character_id]
            shown = await self._shown(
                services.embedder, user_id, conversation.character_id,
                memory_query(history, text), query_vector,
            )
            standing = (
                await self._promises.standing(user_id, conversation.character_id)
                if self._promises is not None else []
            )
            share = [
                self._characters[cid].name
                for cid in character.memory_share
                if cid in self._characters
            ]
            unit_hint = (
                f"你的「群组」（unit 记忆的可见范围）是：{'、'.join(share)}。" if share else None
            )
            prompt = _extraction_prompt(
                character, history, text, reply, shown, now, standing,
                timezone=timezone, unit_hint=unit_hint, names=self._names,
            )
            answer = "".join([
                chunk async for chunk in llm.stream(prompt)
                if isinstance(chunk, str)
            ])
            written = parse_memories(answer)
            if written is None:
                log.warning(  # its length only: the answer is about the user
                    "background model wrote no memories JSON (%d characters) in conversation %s",
                    len(answer), conversation.id,
                )
                return
            said = [part for part in (reply.zh, reply.ja) if part]

            def worth_keeping(memory: NewMemory) -> bool:
                if memory.evidence is None:
                    return False
                if memory.subject == "producer":
                    return memory.evidence in text
                return (
                    any(memory.evidence in part for part in said)
                    and _TRANSIENT_SELF.search(memory.text) is None
                )

            written = [memory for memory in written if worth_keeping(memory)]
            if self._promises is not None:
                changes = parse_promise_changes(answer, standing)
                if changes:
                    await self._promises.change(user_id, changes, now)
                made = parse_promises(answer)
                if made:
                    await self._promises.record(user_id, conversation.character_id, made, now)
            moment = parse_moment(answer)
            if moment is not None:
                for listener in self._moments:
                    try:
                        await listener(user_id, conversation.character_id, moment, now)
                    except Exception:
                        log.exception("telling of a moment in %s failed", conversation.id)
            if self._clearings.get(user_id, 0) != clearings:
                return  # cleared since the turn began
            added = await self._db.write(self._add, conversation, written, shown, now)
            if added and self._needs_merge(added, shown):
                added = await self._merge(llm, conversation, added, shown, now)
        except Exception:
            log.warning("writing memories failed in conversation %s", conversation.id,
                        exc_info=True)
            return
        try:
            if added:
                model = services.embedder.model
                stored = await self._db.read(_stored_vectors, user_id, model)
                await self._vectors(services.embedder, user_id, added, stored, forget=False)
        except EmbeddingError:
            log.warning("embedding new memories failed; they are embedded when searched",
                        exc_info=True)

    @staticmethod
    def _needs_merge(added: Sequence[Memory], shown: Sequence[Memory]) -> bool:
        """New memories worth asking the merge model about: several this turn that look
        alike, or one that looks like a memory already held."""
        if len(added) >= 2 and any(
            a.subject != b.subject or _similar(a.text, b.text)
            for i, a in enumerate(added) for b in added[i + 1:]
        ):
            return True
        return any(
            a.subject != b.subject or _similar(a.text, b.text)
            for a in added for b in shown
        )

    async def _merge(
        self,
        llm: ChatLLM,
        conversation: Conversation,
        added: Sequence[Memory],
        shown: Sequence[Memory],
        now: datetime,
    ) -> list[Memory]:
        """Ask the background model which new memories are views of one event, and write
        one canonical memory for each such group instead of keeping the views apart."""
        try:
            answer = "".join([
                chunk async for chunk in llm.stream(
                    _merge_prompt(added, shown, now, conversation.character_id, self._names)
                ) if isinstance(chunk, str)
            ])
            groups = _parse_merge_groups(answer, added, shown, conversation.character_id)
            if not groups:
                return list(added)
            made = await self._db.write(_merge_groups, conversation, added, shown, groups, now)
        except Exception:
            log.warning("merging memories failed in conversation %s", conversation.id,
                        exc_info=True)
            return list(added)
        if not made:
            return list(added)
        merged_new = {id for group in groups for id in group.new_ids}
        kept = {m.id: m for m in await self.kept(conversation.user_id)}
        result = [m for m in added if m.id not in merged_new]
        result += [kept[id] for id in made if id in kept]
        return result

    async def kept(self, user_id: str) -> list[Memory]:
        """All of the user's memories, oldest first, as they are now: not waiting for what
        is being written down (`entries` does)."""
        return await self._db.read(self.read_kept, user_id)

    async def visible(self, user_id: str, character_id: str) -> list[Memory]:
        """The user's memories `character_id` may recall, by the same scope rules as a turn."""
        return self._visible(await self.kept(user_id), character_id)

    async def long_term(
        self, user_id: str, character_id: str, timezone: str, now: datetime
    ) -> str | None:
        """The prompt section carrying the newest long memories she keeps, at most
        `LONG_TERM_SHOWN`, or None if there are none."""
        await self._settle(user_id)
        now = now or self._clock()
        long = [
            memory for memory in self._visible(await self.kept(user_id), character_id)
            if memory.kind == "long" and _alive(memory, now)
        ]
        long.sort(
            key=lambda memory: (
                memory.last_reinforced_at or memory.created_at, memory.id
            ),
            reverse=True,
        )
        long = long[:LONG_TERM_SHOWN]
        if not long:
            return None
        return _long_term_prompt(long, timezone, now)

    def read_kept(self, db: sqlite3.Connection, user_id: str) -> list[Memory]:
        """What `kept` reads, read through `db`: for a data package too."""
        rows = db.execute(
            "SELECT id, user_id, source_character_id, text, created_at, superseded_by,"
            " conversation_id, scope, emotion, strength, maintain_until, kind, subject,"
            " resolution, mention_count, expires_at, last_reinforced_at, status,"
            " forgotten_at, recalled_at, recall_count"
            " FROM memories WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        return [self._memory(*row) for row in rows]

    def _kept_with_vectors(
        self, db: sqlite3.Connection, user_id: str, model: str
    ) -> tuple[list[Memory], dict[int, bytes]]:
        """All of the user's memories, and their vectors stored for `model`, as they were at
        one moment."""
        return self.read_kept(db, user_id), _stored_vectors(db, user_id, model)

    def _memory(
        self, id: int, user_id: str, source_character_id: str, text: str, created_at: str,
        superseded_by: int | None = None, conversation_id: str | None = None,
        scope: Scope = "shared", emotion: str | None = None, strength: float = 1.0,
        maintain_until: str | None = None, kind: str = "short",
        subject: Subject = "producer", resolution: Resolution | None = None,
        mention_count: int = 1, expires_at: str | None = None,
        last_reinforced_at: str | None = None, status: str = "active",
        forgotten_at: str | None = None, recalled_at: str | None = None,
        recall_count: int = 0,
    ) -> Memory:
        source = self._characters.get(source_character_id)
        return Memory(
            id, user_id, source_character_id, (source.name_zh or source.name) if source else None, text,
            created_at, superseded_by, conversation_id, scope, emotion, strength,
            maintain_until, kind, subject, resolution, mention_count, expires_at,
            last_reinforced_at, status, forgotten_at, recalled_at, recall_count,
        )

    def _add(
        self,
        db: sqlite3.Connection,
        conversation: Conversation,
        written: Sequence["NewMemory"],
        shown: Sequence[Memory],
        now: datetime,
    ) -> list[Memory]:
        """Remember what the user told the conversation's character just `now`, each new
        memory superseding those it names among the memories the background model was
        `shown` that still hold; returns the memories that are new. One the user already
        has is left out, or if superseded, holds again and supersedes what it names."""
        added = []
        user_id = conversation.user_id
        created_at = now.astimezone(UTC).isoformat()
        for memory in written:
            strength, hold = TIERS.get(memory.tier, TIERS["medium"])
            maintain_until = (now + hold).astimezone(UTC).isoformat()
            expires = now + hold
            scope = "private" if memory.subject in ("self", "both") else memory.scope
            kept = db.execute(
                "SELECT id, superseded_by IS NOT NULL, kind, maintain_until, created_at,"
                " expires_at, status FROM memories WHERE user_id = ? AND text = ?",
                (user_id, memory.text),
            ).fetchone()
            if kept is None:
                # Its conversation may have been deleted since the turn (GLOSSARY.md
                # 「历史对话」): the memory is kept all the same, saying where no longer.
                cursor = db.execute(
                    "INSERT INTO memories"
                    " (user_id, source_character_id, conversation_id, text, created_at, scope,"
                    " emotion, strength, maintain_until, kind, subject, mention_count,"
                    " expires_at, last_reinforced_at, status)"
                    " VALUES (?, ?, (SELECT id FROM conversations WHERE id = ?), ?, ?, ?, ?, ?, ?,"
                    " 'short', ?, 1, ?, ?, 'active')",
                    (user_id, conversation.character_id, conversation.id, memory.text,
                     created_at, scope, memory.emotion, strength, maintain_until,
                     memory.subject, expires.astimezone(UTC).isoformat(),
                     created_at),
                )
                assert cursor.lastrowid is not None
                id = cursor.lastrowid
                added.append(self._memory(
                    id, user_id, conversation.character_id, memory.text, created_at,
                    scope=scope, emotion=memory.emotion, strength=strength,
                    maintain_until=maintain_until, kind="short", subject=memory.subject,
                    expires_at=expires.astimezone(UTC).isoformat(),
                    last_reinforced_at=created_at,
                ))
            else:
                id, superseded, kind, kept_until, kept_at, kept_expires, status = kept
                if not superseded and status != "active":
                    continue  # dormant: only the 03:00 consolidation may recall it
                life = _life_of(kind or "short", kept_until, kept_at)
                renewed = _reinforced(
                    kind or "short", kept_expires, now, life, False
                ).astimezone(UTC).isoformat()
                if not superseded:
                    db.execute(
                        "UPDATE memories SET mention_count = mention_count + 1,"
                        " expires_at = ?, last_reinforced_at = ? WHERE id = ?",
                        (renewed, created_at, id),
                    )
                    continue  # already remembered, but said again
                # Revived: it holds again, refreshed as its tier says and with the emotion
                # it was written with this turn.
                db.execute(
                    "UPDATE memories SET superseded_by = NULL, resolution = NULL, emotion = ?,"
                    " strength = ?, maintain_until = ?, scope = ?,"
                    " mention_count = mention_count + 1, expires_at = ?,"
                    " last_reinforced_at = ?, status = 'active', forgotten_at = NULL"
                    " WHERE id = ?",
                    (memory.emotion, strength, maintain_until, scope, renewed,
                     created_at, id),
                )
            # Only what still holds: a memory deleted or superseded since it was shown
            # is left as it is.
            db.executemany(
                "UPDATE memories SET superseded_by = ?, resolution = 'replaced'"
                " WHERE id = ? AND user_id = ? AND superseded_by IS NULL",
                [
                    (id, shown[n - 1].id, user_id)
                    for n in memory.supersedes
                    if 1 <= n <= len(shown) and shown[n - 1].id != id
                    and shown[n - 1].subject == memory.subject
                ],
            )
        return added


    async def _vectors(
        self,
        embedder: Embedder,
        user_id: str,
        memories: list[Memory],
        stored: dict[int, bytes],
        forget: bool = True,
    ) -> dict[int, bytes]:
        """The vector of each memory, of those `stored` for the embedder's model, embedding
        those not yet stored and, if `memories` are all the user's, forgetting those of
        memories that are gone. A memory deleted while it was being embedded gets no
        vector."""
        model = embedder.model
        stored = dict(stored)
        missing = [m for m in memories if m.id not in stored]
        stale = stored.keys() - {m.id for m in memories} if forget else set()
        if missing:
            embedded = await embedder.embed_documents([Document(m.text) for m in missing])
            stored.update(zip((m.id for m in missing), map(to_blob, embedded), strict=True))
        if missing or stale:
            await self._db.write(
                _store_vectors, user_id, model, [(m.id, stored[m.id]) for m in missing],
                stale,
            )
        return stored


def _backfill_legacy(db: sqlite3.Connection) -> None:
    """Give memories kept before expiry a natural one, and drop the already faded short and
    mid ones. Runs once: anything with `expires_at` is left as it is."""
    now = datetime.now(UTC)
    rows = db.execute(
        "SELECT id, COALESCE(kind, 'short'), COALESCE(strength, 1), created_at"
        " FROM memories WHERE expires_at IS NULL"
    ).fetchall()
    for memory_id, kind, strength, created_at in rows:
        if kind in ("short", "mid") and strength <= FORGET_THRESHOLD:
            db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            db.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory_id,))
            continue
        if kind == "long":
            expires = now + LONG_LIFE
        else:
            hours = math.log(FORGET_THRESHOLD / strength) / math.log(DECAY_FACTOR)
            cap = _cap_of(kind).total_seconds() / 3600
            expires = now + timedelta(hours=max(1.0, min(hours, cap)))
        db.execute(
            "UPDATE memories SET expires_at = ?, last_reinforced_at = ?,"
            " status = 'active' WHERE id = ?",
            (expires.astimezone(UTC).isoformat(), created_at or now.astimezone(UTC).isoformat(),
             memory_id),
        )


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(memories)")}
    if "superseded_by" not in columns:  # kept from before superseding: all hold
        db.execute("ALTER TABLE memories ADD COLUMN superseded_by INTEGER")
    if "scope" not in columns:  # kept from before scopes: all shared
        db.execute("ALTER TABLE memories ADD COLUMN scope TEXT NOT NULL DEFAULT 'shared'")
    if "subject" not in columns:  # kept from before subjects: all about the producer
        db.execute("ALTER TABLE memories ADD COLUMN subject TEXT NOT NULL DEFAULT 'producer'")
    if "resolution" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN resolution TEXT")
    db.execute("UPDATE memories SET resolution = 'replaced'"
               " WHERE superseded_by IS NOT NULL AND resolution IS NULL")
    db.execute("UPDATE memories SET scope = 'private' WHERE subject IN ('self', 'both')")
    if "emotion" not in columns:  # kept from before emotions: none written
        db.execute("ALTER TABLE memories ADD COLUMN emotion TEXT")
    if "strength" not in columns:  # kept from before strengths: all fullest
        db.execute("ALTER TABLE memories ADD COLUMN strength REAL NOT NULL DEFAULT 1")
    if "maintain_until" not in columns:  # none kept a maintenance before
        db.execute("ALTER TABLE memories ADD COLUMN maintain_until TEXT")
    if "kind" not in columns:  # kept from before kinds: all short
        db.execute("ALTER TABLE memories ADD COLUMN kind TEXT NOT NULL DEFAULT 'short'")
    if "mention_count" not in columns:  # kept from before counting: each said once
        db.execute(
            "ALTER TABLE memories ADD COLUMN mention_count INTEGER NOT NULL DEFAULT 1"
        )
    if "expires_at" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN expires_at TEXT")
    if "last_reinforced_at" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN last_reinforced_at TEXT")
    if "status" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if "forgotten_at" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN forgotten_at TEXT")
    if "recalled_at" not in columns:
        db.execute("ALTER TABLE memories ADD COLUMN recalled_at TEXT")
    if "recall_count" not in columns:
        db.execute(
            "ALTER TABLE memories ADD COLUMN recall_count INTEGER NOT NULL DEFAULT 0"
        )
    runs = {row[1] for row in db.execute("PRAGMA table_info(memory_consolidations)")}
    if "attempts" not in runs:
        db.execute(
            "ALTER TABLE memory_consolidations ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
        )
    if "state" not in runs:
        db.execute(
            "ALTER TABLE memory_consolidations ADD COLUMN state TEXT NOT NULL DEFAULT 'done'"
        )
    if "last_attempt_at" not in runs:
        db.execute("ALTER TABLE memory_consolidations ADD COLUMN last_attempt_at TEXT")
    _backfill_legacy(db)


def _consolidation_run(
    db: sqlite3.Connection, user_id: str, character_id: str, day: str
) -> tuple[int, str] | None:
    row = db.execute(
        "SELECT attempts, state FROM memory_consolidations"
        " WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day),
    ).fetchone()
    return (row[0], row[1]) if row is not None else None


def _record_attempt(
    db: sqlite3.Connection, user_id: str, character_id: str, day: str,
    success: bool, now: datetime,
) -> None:
    """Record one consolidation attempt: done on success, failed after the fifth failure."""
    db.execute(
        "INSERT INTO memory_consolidations"
        " (user_id, character_id, day, attempts, state, last_attempt_at)"
        " VALUES (?, ?, ?, 1, ?, ?)"
        " ON CONFLICT(user_id, character_id, day) DO UPDATE SET"
        " attempts = memory_consolidations.attempts + 1,"
        " state = CASE WHEN ? THEN 'done'"
        "   WHEN memory_consolidations.attempts + 1 >= 5 THEN 'failed'"
        "   ELSE 'pending' END,"
        " last_attempt_at = excluded.last_attempt_at",
        (user_id, character_id, day, "done" if success else "pending",
         now.astimezone(UTC).isoformat(), success),
    )


_CONSOLIDATION_RULES = """\
你在整理一位角色关于制作人的长期记忆。只使用下面列出的记忆，只能改写、合并和概括，\
不能添加原文里没有的事实。
- short 是一次性的近况；mid 是重复出现、已经有轮廓的事；long 是已经稳定、以后也会记得的事。
- 把同一主题的几条 short/mid 合并成一条 mid；把已经稳定、值得长期记住的合并成一条 long。
- 至少要有证据才升级：mid 至少两条相关记忆或一条被提及两次；long 至少三次提及，或明显跨了几天。
- 不要升级一次性的状态、天气、临时情绪、当天就会过去的事。
- 同一条记忆不能被拆成两条；不同 subject 不能合并。
- scope 只能和来源一样或更严格（private 比 unit 严格，unit 比 shared 严格）。
- 已有 long 不要重复写；要合并它时，把它写进 sources。
- 如果待整理记忆再次确认了某条已有 long，写 reinforce：{"id": long 编号, "sources": [确认它的编号], "explicit": 是否明确要求记住}。
- 已淡忘的 long 只能通过 recall 恢复：待整理记忆明确是同一件事时，写 {"id": 编号,\
 "sources": [待整理记忆编号]}；只是话题相近不要恢复。
- 没有值得合并、升级或回忆的，就输出空列表。
只输出 JSON：
{"reinforce": [{"id": 编号, "sources": [编号], "explicit": false}],
 "recall": [{"id": 编号, "sources": [编号]}],
 "consolidate": [{"kind": "mid|long", "text": "……", "sources": [编号],\
 "subject": "producer|self|both", "scope": "private|unit|shared"}]}
"""


def _consolidation_prompt(
    shown: Sequence[Memory], now: datetime, zone: ZoneInfo
) -> list[ChatMessage]:
    """What the consolidation model reads: the long memories already kept, then the short
    and mid ones, and the dormant long ones it may recall."""
    active_long = [m for m in shown if m.kind == "long" and m.status == "active"]
    short = [m for m in shown if m.kind != "long"]
    dormant = [m for m in shown if m.kind == "long" and m.status == "dormant"]
    lines = ["# 已有的长期记忆"]
    lines += [f"{n}. {m.text}" for n, m in enumerate(active_long, 1)] or ["（还没有）"]
    lines += ["", "# 待整理记忆"]
    offset = len(active_long)
    lines += [
        f"{offset + n}. [{m.kind}，提及 {m.mention_count} 次，"
        f"{datetime.fromisoformat(m.created_at).astimezone(zone).date().isoformat()}，"
        f"{m.subject}] {m.text}"
        for n, m in enumerate(short, 1)
    ] or ["（没有）"]
    if dormant:
        lines += ["", "# 已淡忘的长期记忆"]
        lines += [
            f"{offset + len(short) + n}. [想起过 {m.recall_count} 次] {m.text}"
            for n, m in enumerate(dormant, 1)
        ]
    return [
        ChatMessage("system", _CONSOLIDATION_RULES),
        ChatMessage("user", "\n".join(lines)),
    ]


def _parse_consolidations(
    answer: str, shown: Sequence[Memory]
) -> list[dict[str, object]] | None:
    """The mid and long memories the model wrote, checked against what it was shown."""
    start, end = answer.find("{"), answer.rfind("}")
    try:
        raw = json.loads(answer[start:end + 1]) if start >= 0 else None
    except (ValueError, TypeError):
        log.warning("consolidation was not the JSON asked for")
        return None
    entries = raw.get("consolidate") if isinstance(raw, dict) else None
    if entries is None:
        return []
    if not isinstance(entries, list):
        return None
    rank = {"private": 0, "unit": 1, "shared": 2}
    made: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or len(made) >= MAX_CONSOLIDATED:
            continue
        kind, text, numbers = entry.get("kind"), entry.get("text"), entry.get("sources")
        if kind not in ("mid", "long") or not isinstance(text, str):
            continue
        text = text.strip()[:MAX_MEMORY_LENGTH]
        if not text or text in seen or not isinstance(numbers, list):
            continue
        sources = [
            shown[n - 1] for n in numbers
            if type(n) is int and 1 <= n <= len(shown)
        ]
        if not sources or len({m.id for m in sources}) != len(sources):
            continue
        if any(m.status not in ("active", "expired") for m in sources):
            continue
        if any(m.kind == "long" and m.status != "active" for m in sources):
            continue
        subjects = {m.subject for m in sources}
        if len(subjects) != 1:
            continue
        subject = subjects.pop()
        count = sum(m.mention_count for m in sources)
        if (kind == "mid" and count < 2) or (kind == "long" and count < 3):
            continue
        base = min(rank[m.scope] for m in sources)
        proposed = rank.get(str(entry.get("scope")), base)
        scope = next(name for name, value in rank.items() if value == min(base, proposed))
        if subject in ("self", "both"):
            scope = "private"
        seen.add(text)
        made.append({
            "kind": kind, "text": text, "sources": [m.id for m in sources],
            "subject": subject, "scope": scope, "mention_count": count,
        })
    return made


def _parse_recall(answer: str, shown: Sequence[Memory]) -> list[dict[str, object]] | None:
    """The dormant long memories the model says the turn brought back."""
    start, end = answer.find("{"), answer.rfind("}")
    try:
        raw = json.loads(answer[start:end + 1]) if start >= 0 else None
    except (ValueError, TypeError):
        return None
    entries = raw.get("recall") if isinstance(raw, dict) else None
    if entries is None:
        return []
    if not isinstance(entries, list):
        return None
    made: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        number, numbers = entry.get("id"), entry.get("sources")
        if type(number) is not int or not (1 <= number <= len(shown)):
            continue
        target = shown[number - 1]
        if target.kind != "long" or target.status != "dormant":
            continue
        if not isinstance(numbers, list):
            continue
        sources = [
            shown[n - 1] for n in numbers
            if type(n) is int and 1 <= n <= len(shown)
        ]
        if not sources or len({m.id for m in sources}) != len(sources):
            continue
        if any(m.status != "active" or m.kind == "long" for m in sources):
            continue
        made.append({"id": target.id, "sources": [m.id for m in sources]})
    return made


def _parse_reinforce(
    answer: str, shown: Sequence[Memory]
) -> list[dict[str, object]] | None:
    """The active long memories the model says the turn confirmed again."""
    start, end = answer.find("{"), answer.rfind("}")
    try:
        raw = json.loads(answer[start:end + 1]) if start >= 0 else None
    except (ValueError, TypeError):
        return None
    entries = raw.get("reinforce") if isinstance(raw, dict) else None
    if entries is None:
        return []
    if not isinstance(entries, list):
        return None
    made: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        number, numbers = entry.get("id"), entry.get("sources")
        if type(number) is not int or not (1 <= number <= len(shown)):
            continue
        target = shown[number - 1]
        if target.kind != "long" or target.status != "active":
            continue
        if not isinstance(numbers, list):
            continue
        sources = [
            shown[n - 1] for n in numbers
            if type(n) is int and 1 <= n <= len(shown)
        ]
        if not sources or len({m.id for m in sources}) != len(sources):
            continue
        if any(m.kind == "long" or m.status not in ("active", "expired") for m in sources):
            continue
        made.append({
            "id": target.id,
            "sources": [m.id for m in sources],
            "explicit": entry.get("explicit") is True,
        })
    return made


def _apply_consolidations(
    db: sqlite3.Connection,
    user_id: str,
    character_id: str,
    shown: Sequence[Memory],
    items: Sequence[dict[str, object]],
    reinforce: Sequence[dict[str, object]],
    recall: Sequence[dict[str, object]],
    now: datetime,
) -> None:
    """Write the consolidated memories, reinforce active long ones, recall dormant long
    ones, and mark what they absorbed as merged."""
    for item in reinforce:
        target = db.execute(
            "SELECT id, kind, maintain_until, created_at, expires_at FROM memories"
            " WHERE id = ? AND user_id = ? AND kind = 'long' AND status = 'active'",
            (int(item["id"]), user_id),
        ).fetchone()
        if target is None:
            continue
        ids = [int(id) for id in item["sources"]]
        held = db.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ? AND superseded_by IS NULL"
            " AND status IN ('active', 'expired') AND kind != 'long'"
            f" AND id IN ({','.join('?' * len(ids))})",
            (user_id, *ids),
        ).fetchone()[0]
        if held != len(ids):
            continue
        life = _life_of(str(target[1]), target[2], target[3])
        renewed = _reinforced(
            str(target[1]), target[4], now, life, bool(item["explicit"])
        ).astimezone(UTC).isoformat()
        db.execute(
            "UPDATE memories SET expires_at = ?, last_reinforced_at = ?,"
            " mention_count = mention_count + ? WHERE id = ?",
            (renewed, now.astimezone(UTC).isoformat(), len(ids), int(item["id"])),
        )
        db.executemany(
            "UPDATE memories SET superseded_by = ?, resolution = 'merged'"
            " WHERE id = ? AND user_id = ? AND superseded_by IS NULL",
            [(int(item["id"]), id_, user_id) for id_ in ids],
        )
    for item in recall:
        ids = [int(id) for id in item["sources"]]
        target = db.execute(
            "SELECT id FROM memories WHERE id = ? AND user_id = ? AND kind = 'long'"
            " AND status = 'dormant'",
            (int(item["id"]), user_id),
        ).fetchone()
        if target is None:
            continue
        held = db.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ? AND superseded_by IS NULL"
            " AND status = 'active' AND kind != 'long'"
            f" AND id IN ({','.join('?' * len(ids))})",
            (user_id, *ids),
        ).fetchone()[0]
        if held != len(ids):
            continue
        db.execute(
            "UPDATE memories SET status = 'active', expires_at = ?,"
            " last_reinforced_at = ?, recalled_at = ?, recall_count = recall_count + 1,"
            " forgotten_at = NULL WHERE id = ?",
            ((now + LONG_LIFE).astimezone(UTC).isoformat(),
             now.astimezone(UTC).isoformat(), now.astimezone(UTC).isoformat(),
             int(item["id"])),
        )
        db.executemany(
            "UPDATE memories SET superseded_by = ?, resolution = 'merged'"
            " WHERE id = ? AND user_id = ? AND superseded_by IS NULL",
            [(int(item["id"]), id_, user_id) for id_ in ids],
        )
    for item in items:
        text = str(item["text"])
        if db.execute(
            "SELECT 1 FROM memories WHERE user_id = ? AND text = ?", (user_id, text)
        ).fetchone() is not None:
            continue
        ids = [int(id) for id in item["sources"]]
        held = db.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ? AND superseded_by IS NULL"
            f" AND id IN ({','.join('?' * len(ids))})",
            (user_id, *ids),
        ).fetchone()[0]
        if held != len(ids):
            continue
        cursor = db.execute(
            "INSERT INTO memories"
            " (user_id, source_character_id, conversation_id, text, created_at, scope,"
            " strength, maintain_until, kind, subject, mention_count, expires_at,"
            " last_reinforced_at, status)"
            " VALUES (?, ?, NULL, ?, ?, ?, 1, NULL, ?, ?, ?, ?, ?, 'active')",
            (user_id, character_id, text, now.astimezone(UTC).isoformat(),
             item["scope"], item["kind"], item["subject"], item["mention_count"],
             (now + _cap_of(str(item["kind"]))).astimezone(UTC).isoformat(),
             now.astimezone(UTC).isoformat()),
        )
        assert cursor.lastrowid is not None
        new_id = cursor.lastrowid
        db.executemany(
            "UPDATE memories SET superseded_by = ?, resolution = 'merged'"
            " WHERE id = ? AND user_id = ? AND superseded_by IS NULL",
            [(new_id, id_, user_id) for id_ in ids],
        )


def _edit(db: sqlite3.Connection, user_id: str, memory_id: int, text: str) -> bool:
    """Replace an active memory's text and drop its stale embedding."""
    changed = db.execute(
        "UPDATE memories SET text = ? WHERE id = ? AND user_id = ?"
        " AND superseded_by IS NULL AND status = 'active'",
        (text, memory_id, user_id),
    ).rowcount
    if not changed:
        return False
    db.execute("DELETE FROM memory_vectors WHERE memory_id = ?", (memory_id,))
    return True


def _delete(db: sqlite3.Connection, user_id: str, memory_id: int) -> bool:
    """Delete the memory, what it superseded superseded instead by what superseded it, or
    holding again if nothing did."""
    deleted = db.execute(
        "SELECT superseded_by FROM memories WHERE id = ? AND user_id = ?",
        (memory_id, user_id),
    ).fetchone()
    if deleted is None:
        return False
    [superseded_by] = deleted
    db.execute(
        "UPDATE memories SET superseded_by = ?,"
        " resolution = CASE WHEN ? IS NULL THEN NULL ELSE resolution END"
        " WHERE superseded_by = ? AND user_id = ?",
        (superseded_by, superseded_by, memory_id, user_id),
    )
    db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    db.execute(
        "DELETE FROM memory_vectors WHERE user_id = ? AND memory_id = ?", (user_id, memory_id)
    )
    return True


def _set_scope(db: sqlite3.Connection, user_id: str, memory_id: int, scope: Scope) -> bool:
    """Change how widely the memory is shared; False if the user has no such memory."""
    cursor = db.execute(
        "UPDATE memories SET scope = ? WHERE id = ? AND user_id = ?"
        " AND subject NOT IN ('self', 'both')",
        (scope, memory_id, user_id),
    )
    return cursor.rowcount > 0


def _clear(db: sqlite3.Connection, user_id: str) -> None:
    db.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM memory_vectors WHERE user_id = ?", (user_id,))


def _decay(db: sqlite3.Connection, now: str) -> None:
    """Weaken the memories whose maintenance is over at `now`, once (`DECAY_FACTOR`)."""
    db.execute(
        "UPDATE memories SET strength = strength * ?"
        " WHERE (maintain_until IS NULL OR maintain_until < ?) AND strength > ?"
        " AND COALESCE(kind, 'short') = 'short'",
        (DECAY_FACTOR, now, FORGET_THRESHOLD),
    )


def _expire(db: sqlite3.Connection, now: str, forgotten_before: str) -> None:
    """Mark short and mid memories past their expiry, make expired long ones dormant, and
    delete long memories dormant for longer than their keep."""
    db.execute(
        "UPDATE memories SET status = 'expired', forgotten_at = ?"
        " WHERE COALESCE(kind, 'short') IN ('short', 'mid')"
        " AND status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
        (now, now),
    )
    db.execute(
        "UPDATE memories SET status = 'dormant', forgotten_at = ?"
        " WHERE kind = 'long' AND status = 'active' AND superseded_by IS NULL"
        " AND expires_at IS NOT NULL AND expires_at <= ?",
        (now, now),
    )
    db.execute(
        "DELETE FROM memories WHERE kind = 'long' AND status = 'dormant'"
        " AND forgotten_at IS NOT NULL AND forgotten_at <= ?",
        (forgotten_before,),
    )
    db.execute(
        "DELETE FROM memory_vectors WHERE memory_id NOT IN (SELECT id FROM memories)"
    )


def _delete_expired(
    db: sqlite3.Connection, user_id: str, character_id: str, now: str
) -> None:
    """Hard-delete the short and mid memories of one character that were handled by a
    successful consolidation."""
    db.execute(
        "DELETE FROM memories WHERE user_id = ? AND source_character_id = ?"
        " AND COALESCE(kind, 'short') IN ('short', 'mid')"
        " AND (status = 'expired' OR (expires_at IS NOT NULL AND expires_at <= ?))",
        (user_id, character_id, now),
    )
    db.execute(
        "DELETE FROM memory_vectors WHERE memory_id NOT IN (SELECT id FROM memories)"
    )


def _unembedded(
    db: sqlite3.Connection, after: int, user_id: str | None, model: str, batch: int
) -> list[tuple[int, str, str]]:
    """As many as `batch` of every user's memories, or only those of `user_id`, after the
    id `after`, with no vector for `model`: each one's id, user and text."""
    return db.execute(
        "SELECT id, user_id, text FROM memories m WHERE id > ?"
        " AND (? IS NULL OR user_id = ?) AND NOT EXISTS"
        " (SELECT 1 FROM memory_vectors v WHERE v.memory_id = m.id AND v.model = ?)"
        " ORDER BY id LIMIT ?",
        (after, user_id, user_id, model, batch),
    ).fetchall()


def _store_embedded(
    db: sqlite3.Connection, model: str, embedded: Sequence[tuple[int, str, bytes]]
) -> None:
    """Store the vectors for `model` of the memories `embedded`, each with its user, but of
    those deleted meanwhile."""
    db.executemany(
        "INSERT OR REPLACE INTO memory_vectors (memory_id, user_id, model, embedding)"
        " SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM memories WHERE id = ?)",
        [(id, user_id, model, vector, id) for id, user_id, vector in embedded],
    )


def _forget_other_models(db: sqlite3.Connection, model: str) -> int:
    return db.execute("DELETE FROM memory_vectors WHERE model != ?", (model,)).rowcount


def _stored_vectors(db: sqlite3.Connection, user_id: str, model: str) -> dict[int, bytes]:
    """The vector of each of the user's memories stored for `model`."""
    return dict(
        db.execute(
            "SELECT memory_id, embedding FROM memory_vectors WHERE user_id = ? AND model = ?",
            (user_id, model),
        ).fetchall()
    )


def _store_vectors(
    db: sqlite3.Connection,
    user_id: str,
    model: str,
    added: Sequence[tuple[int, bytes]],
    stale: Iterable[int],
) -> None:
    """Store the `added` vectors for `model`, but of memories deleted meanwhile, and forget
    those of the `stale` memories."""
    _store_embedded(db, model, [(memory_id, user_id, vector) for memory_id, vector in added])
    db.executemany(
        "DELETE FROM memory_vectors WHERE memory_id = ? AND model = ?",
        [(memory_id, model) for memory_id in stale],
    )


def _merge_groups(
    db: sqlite3.Connection,
    conversation: Conversation,
    added: Sequence[Memory],
    shown: Sequence[Memory],
    groups: Sequence[_MergeGroup],
    now: datetime,
) -> list[int]:
    """Write one canonical memory per group and mark its members merged; returns the
    canonical ids written, skipping groups whose members changed or whose text exists."""
    user_id = conversation.user_id
    current = {
        m[0]: m for m in db.execute(
            "SELECT id, text, subject, scope, source_character_id, superseded_by, mention_count"
            " FROM memories WHERE user_id = ?", (user_id,),
        ).fetchall()
    }
    made: list[int] = []
    for group in groups:
        members = [*group.new_ids, *group.old_ids]
        if len(members) < 2:
            continue
        held = [current.get(id) for id in members]
        if any(row is None or row[5] is not None for row in held):
            continue
        if any(row[4] != conversation.character_id for row in held):
            continue
        if db.execute("SELECT 1 FROM memories WHERE user_id = ? AND text = ?",
                      (user_id, group.text)).fetchone() is not None:
            continue
        strength, hold = TIERS.get(group.tier, TIERS["medium"])
        mentions = sum(int(row[6]) for row in held)
        cursor = db.execute(
            "INSERT INTO memories"
            " (user_id, source_character_id, conversation_id, text, created_at, scope,"
            " emotion, strength, maintain_until, kind, subject, resolution, mention_count,"
            " expires_at, last_reinforced_at, status)"
            " VALUES (?, ?, (SELECT id FROM conversations WHERE id = ?), ?, ?, ?, ?, ?, ?,"
            " 'short', ?, NULL, ?, ?, ?, 'active')",
            (user_id, conversation.character_id, conversation.id, group.text,
             now.astimezone(UTC).isoformat(), group.scope, group.emotion, strength,
             (now + hold).astimezone(UTC).isoformat(), group.subject, mentions,
             (now + hold).astimezone(UTC).isoformat(),
             now.astimezone(UTC).isoformat()),
        )
        assert cursor.lastrowid is not None
        canonical = cursor.lastrowid
        db.executemany(
            "UPDATE memories SET superseded_by = ?, resolution = 'merged'"
            " WHERE id = ? AND user_id = ? AND superseded_by IS NULL",
            [(canonical, id, user_id) for id in members],
        )
        made.append(canonical)
        current[canonical] = (canonical, group.text, group.subject, group.scope,
                              conversation.character_id, None, mentions)
    return made

class MemoryTurn:
    """One turn's use of the user's memories; see `Memories.begin`."""

    def __init__(
        self,
        memories: Memories,
        conversation: Conversation,
        timezone: str,
        clearings: int,
        llm: ChatLLM,
        hold: Callable[[], Callable[[], None]] | None = None,
    ) -> None:
        self._memories = memories
        self._conversation = conversation
        self._timezone = timezone
        self._clearings = clearings
        self._llm = llm
        self._hold = hold

    async def candidates(self, query_vector: Sequence[float]) -> list[Memory]:
        """As many as `CANDIDATES` of the user's holding memories nearest the turn's query
        (see `memory_query`) embedded with the memories' embedder, nearest first, for
        `find`; none if memory is off or the memories not embedded yet cannot be."""
        return await self._memories._candidates(
            self._conversation.user_id, query_vector, self._conversation.character_id
        )

    async def visible(self) -> list[Memory]:
        """The user's memories this conversation's character may recall, by the same scope
        rules as a turn: her own, the shared ones, and the unit ones she is in the group of."""
        return await self._memories.visible(
            self._conversation.user_id, self._conversation.character_id
        )

    async def long_term(self) -> str | None:
        """The prompt section carrying the stable memories she keeps, if any."""
        return await self._memories.long_term(
            self._conversation.user_id, self._conversation.character_id,
            self._timezone, self._memories._clock(),
        )

    @property
    def timezone(self) -> str:
        """The producer's time zone this turn is read in."""
        return self._timezone

    async def find(self, query: str, candidates: Sequence[Memory]) -> str | None:
        """The prompt section carrying those of the `candidates` relevant to `query`, the
        turn's query, most relevant first, each labelled with who it was told to and to be
        passed on as hearsay when that was someone else; None if none are relevant, memory
        is off or reranking failed."""
        return await self._memories._find(self._conversation, self._timezone, query, candidates)

    def write_down(
        self,
        history: Sequence[Message],
        text: str,
        reply: Message,
        query_vector: Sequence[float] | None = None,
    ) -> None:
        """Start writing down what the user revealed in this turn: `text`, said after
        `history` and answered with `reply`; `query_vector` is the turn's query embedded
        already, if it was, so it is not embedded again."""
        self._memories._write_down(
            self._conversation, self._timezone, self._clearings, self._llm, self._hold,
            history, text, reply, query_vector,
        )


_MERGE_INSTRUCTIONS = """\
你负责判断几条记忆是不是同一次事件，并在是的时候合成一条完整记忆。

只合并真正指向同一件事的条目：
- 同一件事的两个视角，例如「她带了特产回来」和「制作人收到了特产」；
- 同一次见面、同一次约定、同一次礼物；
- 一条只说了事件的一部分，另一条补全了它。

不要合并：
- 只是话题相近、地点相近、人物相近，但其实是两件事；
- 一条是旧事实、另一条是更新后的事实（那是替换，不是合并）；
- 互相矛盾的说法；
- 不同来源角色的记忆；
- 不同用户的记忆。

合并后的文本要是一条自然、完整、第三人称的中文陈述，包含双方视角，但不要加原文没有的细节。
subject 只能是 producer、self、both：
- 只关于制作人写 producer；
- 只关于她自己写 self；
- 同时关于两个人写 both。
scope 只能是 private、unit、shared；涉及 self 或 both 时必须是 private。
emotion 用一两个词；tier 是 strong、medium、weak。

只输出 JSON：
{"memories": [{"text": "合并后的完整陈述", "subject": "both", "scope": "private",
"emotion": "开心", "tier": "strong", "new": [1, 2], "old": [3]}]}
new 是下面 N 开头的编号，old 是 O 开头的编号；没有可合并的就输出 {"memories": []}。
"""


def _merge_prompt(
    added: Sequence[Memory],
    shown: Sequence[Memory],
    now: datetime,
    character_id: str,
    names: Mapping[str, str],
) -> list[ChatMessage]:
    """The prompt asking the background model which of the new and held memories are
    views of one event; `new` and `old` are numbered from 1 as the answer names them."""
    lines = [f"今天是 {_date(now)}。", f"来源角色：{names.get(character_id, character_id)}。", ""]
    lines += ["# 本轮新写下的记忆"]
    lines += [
        f"N{n}. subject={m.subject} scope={m.scope} text={m.text}"
        for n, m in enumerate(added, 1)
    ] or ["（没有）"]
    lines += ["", "# 已经记着的相关记忆"]
    lines += [
        f"O{n}. subject={m.subject} scope={m.scope} created={m.created_at[:10]} text={m.text}"
        for n, m in enumerate(shown, 1)
    ] or ["（没有）"]
    return [ChatMessage("system", _MERGE_INSTRUCTIONS), ChatMessage("user", "\n".join(lines))]


def _parse_merge_groups(
    answer: str, added: Sequence[Memory], shown: Sequence[Memory], character_id: str
) -> list[_MergeGroup]:
    """Read the merge model's groups, keeping only valid, same-source, holding members."""
    items = _array(answer, "memories")
    if not isinstance(items, list):
        return []
    added_by = {n: m for n, m in enumerate(added, 1)}
    shown_by = {n: m for n, m in enumerate(shown, 1)}
    used: set[int] = set()
    groups: list[_MergeGroup] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        new_ids = [n for n in item.get("new", []) if type(n) is int and n in added_by]
        old_ids = [n for n in item.get("old", []) if type(n) is int and n in shown_by]
        new_members = [added_by[n] for n in new_ids if added_by[n].id not in used]
        old_members = [shown_by[n] for n in old_ids if shown_by[n].id not in used]
        members = [*new_members, *old_members]
        if not isinstance(text, str) or not text.strip() or len(members) < 2:
            continue
        if not new_members:  # a merge this turn always starts from what was just written
            continue
        if any(m.source_character_id != character_id for m in members):
            continue
        if any(m.superseded_by is not None for m in members):
            continue
        subjects = {m.subject for m in members}
        if "both" in subjects or ("producer" in subjects and "self" in subjects):
            subject: Subject = "both"
        elif "self" in subjects:
            subject = "self"
        else:
            subject = "producer"
        scope: Scope
        if subject in ("self", "both") or any(m.scope == "private" for m in members):
            scope = "private"
        else:
            scope = item.get("scope") if item.get("scope") in SCOPES else members[0].scope
        emotion = item.get("emotion")
        tier = item.get("tier") if item.get("tier") in TIERS else "medium"
        used.update(m.id for m in members)
        groups.append(_MergeGroup(
            text.strip()[:MAX_MEMORY_LENGTH], subject, scope,
            emotion if isinstance(emotion, str) and emotion.strip() else members[0].emotion,
            tier, tuple(m.id for m in new_members), tuple(m.id for m in old_members),
        ))
    return groups


def _similar(a: str, b: str) -> bool:
    """Whether two memory texts share enough character pairs to be worth asking about."""
    def grams(text: str) -> set[str]:
        clean = "".join(ch for ch in text if not ch.isspace())
        return {clean[i:i + 2] for i in range(len(clean) - 1)} or {clean}

    left, right = grams(a), grams(b)
    return bool(left and right) and len(left & right) / len(left | right) >= 0.45


_INSTRUCTIONS = """\
你负责替《偶像大师 闪耀色彩》283 事务所的偶像们（还有事务员）记住两类事：制作人的事，以及这位角色自己的事。制作人就是正在和她们对话的用户。

读下面的对话。制作人最新的一句话是制作人主题的唯一来源；这位角色最新的回复是她自己主题的唯一来源。
今天早些时候的对话只用于理解「那件事」「他」之类的指代，不能从那里提取新记忆；旧日期的内容也不要写成今天发生的事。

只记过后仍有意义的事。
制作人主题：制作人自己透露的个人情况、喜好、经历、计划、身边的人和宠物、希望偶像记住的事；\
会持续几天、朋友过后会问起的近况，例如生病、考试、面试、搬家、出差。
她自己主题：她明确说过的自己的经历、稳定的偏好、自己的计划、对某件事持续的感受；只限以后对话仍有意义的事。
不要记的：当天就会过去的状态（例如累、困、饿、无聊、心情不好）和一时的情绪；寒暄；原作剧情和角色设定；\
演戏、玩笑、幻想、梦里说的话；模型推断出来、她没有明确说过的事；制作人向偶像提的问题。
她自己主题尤其不要记正在做、今天要做、明天要做的事，例如「正在准备明天的资料」「今天和日花约好做饭」\
「刚刚收到糖果」「现在在事务所工作」「明天要交资料」「这周很忙」；这些不是长期记忆。
同一句话里还说了值得记的事时，只记那件事、不记一时的状态。

每条记忆是一句能单独看懂的中文第三人称陈述。
- 制作人主题以「制作人」开头，例如「制作人养了一只叫小白的猫」。
- 她自己主题以这位角色的中文名开头，例如「角色名小时候在祖母家见过这种花」，实际写她的名字；不要用「我」，因为这条记忆可能被别的角色看到。
- 每条记忆都必须带 evidence，写原样连续的一段话，不要改写：
  - 制作人主题从制作人最新一句话里摘；
  - 她自己主题从她最新回复里摘。
  写不出原话就不要写这条记忆。
提到「明天」「下周六」这样的相对时间时，换算成具体日期写下来。

「已经记住的事」按编号列出了和这轮对话最相关的记忆，每条前面的 [producer] 或 [self] 是它的主题，写之前先对照：
- 和已经记住的一样的，不要再写。
- 更新或修正了已经记住的事（例如搬了家、换了工作），写一条单独看也完整的新记忆，写清从什么变成了什么，\
例如「制作人从大阪搬到了东京」，并在 supersedes 里写上被它取代的记忆的编号，可以写多个。
- 只在同 subject 之间取代：制作人主题不要取代自己主题，自己主题也不要取代制作人主题。
- 补充了已经记住的事的细节（例如说出了猫的名字），写一条把细节合进去的完整记忆，取代原来那条。
- 一件事只变了一部分的，新记忆要把仍然成立的部分也写进去，例如原来记着养了一只猫和一只狗、\
现在狗去世了，就写「制作人的狗去世了，现在还养着那只猫」，取代原来那条。
- 两件事可以同时成立的（例如喜欢喝咖啡，最近也开始喝茶），新的照常写，不取代旧的。

除了记忆，还要记下制作人的**约定**：制作人明确说了要在某一天为她做一件事时（给她一样东西、带她去某个地方、\
那天要来找她），把这件事和那一天写进 promises。只认制作人明确说出口的日子，或者能从制作人说的「明天」「后天」\
「下周六」和今天算出来的日子；「以后」「有机会再说」这种没有日子的不算。text 写制作人答应做什么，说明白是什么事，\
像「给她一块布丁」「带她去吃拉面」这样，不要以「制作人」开头；due 写那一天，格式 YYYY-MM-DD。\
「这周末」「月底前」这种在一段日子里做的，due 写最后那天，from 写最早那天（从今天起就写今天）；只有一天的不写 from。\
一件已经过去的日子上的约定不写。每条约定还要写 kind，只能是 call、gift、meeting、work、other：\
call 是打电话、语音或视频通话；gift 是给她、送她、买给她一样具体的东西；meeting 是和她见面、一起外出、一起吃饭、\
一起看电影、来接她；work 是在工作上帮她，但不一定见面；拿不准就写 other。见面即使是为了工作，也写 meeting。
「# 已经记着的约定」按编号列着已经记下的约定：同一件事不要再写进 promises，换个说法也是同一件。\
制作人明确说其中哪一件改到别的日子，写进 promise_changes：{"id": 编号, "due": "YYYY-MM-DD"}；\
明确说不做了、取消了，写 {"id": 编号, "cancel": true}。只是没提起、还没做，都不算改期或取消。

每条记忆还要标 scope，判断作为来源偶像的她愿意让谁知道。
先看 subject：subject 是 self 或 both 时，scope 固定写 private，不能写 unit 或 shared。
subject 是 producer 时：

- private（私密）：只属于她和制作人之间、她不愿意让任何人知道的事。
  例如：秘密的约定、私下的约会、接吻或身体接触、制作人夸她让她害羞到不想转述、\
只有两个人懂的称呼或暗号。
  普通的工作、行程、事务所日常、制作人今天做了什么，都不算 private。

- unit（群组）：她会主动跟同组合的伙伴分享、但不会到处说的事。
  例如：制作人今天在事务所加班到很晚、制作人最近在准备新企划、\
制作人今天来看了她们练习、制作人买了零食放在休息室、制作人下周要带她们去拍摄。
  普通的工作近况、制作人最近的安排、事务所里发生的事，先考虑 unit。

- shared（共享）：她愿意让所有偶像都知道的事。
  例如：制作人在公开活动上被表扬、制作人负责的企划上了新闻、\
制作人喜欢咖啡这种没什么敏感度的事实、公开的行程或成绩。

判断顺序：
1. 秘密、亲密、害羞、只属于两个人的感情 → private
2. 公开场合的成绩、新闻、谁都可以知道的事实 → shared
3. 其余普通工作、日常、安排、近况 → unit
拿不准 scope 时写 unit；subject 是 self 或 both 时仍然只能写 private。

每条记忆还要标 emotion、tier 和 subject：
- emotion 是她记下这件事时的感情，用一两个词（开心、难过、生气、担心、怀念、平静…）；没有明显感情就写「平静」。
- tier 是这件事对她的重要程度：strong（强，她会记很久、很当回事）、medium（中，一般的重要）、weak（弱，小事、随口一提）。\
拿不准就写 medium。
- subject 只能是 producer（只关于制作人）、self（只关于她自己）或 both（同时关于两个人）。

只输出 JSON，格式为 {"memories": [{"text": "……", "subject": "producer|self|both", "evidence": "原话……", \
"supersedes": [编号], "scope": "shared|unit|private", "emotion": "开心", "tier": "strong|medium|weak"}], \
"promises": [{"text": "……", "due": "YYYY-MM-DD", "kind": "call|gift|meeting|work|other"}], \
"promise_changes": [], "moment": null}，不取代任何记忆时 supersedes 写 []，拿不准 scope 时写 unit；\
没有约定时 promises 写 []；没有改期或取消的约定时 promise_changes 写 []；没有值得写进日记的事时 moment 写 null；\
都没有就输出 {"memories": [], "promises": [], "promise_changes": [], "moment": null}。

# 日记
这一轮里如果发生了对她来说**特别重要**、她今晚会想写进日记的事——制作人第一次对她说了某句话、\
两个人一起决定了什么、她被深深打动了或者被伤到了——就在 moment 里用一句话写下来，以她的名字开头，\
例如「冬优子第一次听制作人说她不用一直逞强」。大多数回合都没有这样的事，写 null；平常的闲聊、\
普通的开心不算。"""


def _extraction_prompt(
    character: Character,
    history: Sequence[Message],
    text: str,
    reply: Message,
    shown: Sequence[Memory],
    now: datetime,
    promised: Sequence[Promise] = (),
    timezone: str = "Asia/Tokyo",
    unit_hint: str | None = None,
    names: Mapping[str, str] | None = None,
) -> list[ChatMessage]:
    # 星期几也写上：约定（promises）里「下周六」这类说法要靠它算出是哪一天。
    weekday = "一二三四五六日"[now.weekday()]
    name = character.name_zh or character.name
    zone = ZoneInfo(timezone)
    today = now.astimezone(zone).date()
    lines = [
        f"今天是 {_date(now)}，星期{weekday}。",
        f"这位角色是：{name}。",
        f"她自己的记忆要用「{name}」开头，不要用「我」。",
    ]
    if unit_hint:
        lines.append(unit_hint)
    lines += ["", "# 已经记住的事"]
    lines += [
        f"{n}. [{m.subject}] {m.text}" for n, m in enumerate(shown, 1)
    ] or ["（还没有）"]
    lines += ["", "# 已经记着的约定"]
    lines += [f"{n}. [{p.kind}] {p.due.isoformat()}：{p.text}" for n, p in enumerate(promised, 1)] or ["（还没有）"]
    lines += ["", "# 最近的对话（只用于理解指代，不是记忆来源）"]
    earlier = 0
    for message in history[-EXCHANGE_HISTORY:]:
        said_at = datetime.fromisoformat(message.created_at).astimezone(zone)
        earlier += 1
        when = said_at.strftime("%H:%M")
        day = _relative_day(said_at.date(), today) or _date(said_at)
        if message.role == "user":
            lines.append(f"（{day} {when}，上下文，不是来源）制作人：{message.text}")
        else:
            lines.append(f"（{day} {when}，上下文，不是来源）{character.name}：{_said(message)}")
    if not earlier:
        lines.append("（没有）")
    lines += [f"# 最新的一句话（producer 记忆的唯一来源）\n制作人：{text}"]
    lines += [f"# 最新的一条回复（self 记忆的唯一来源）\n{character.name}：{_said(reply)}"]
    instructions = (
        f"{names_instructions(names, where='记忆')}\n\n{_INSTRUCTIONS}"
        if names else _INSTRUCTIONS
    )
    return [ChatMessage("system", instructions), ChatMessage("user", "\n".join(lines))]


def memory_query(history: Sequence[Message], text: str) -> str:
    """What memories relevant to `text`, said after `history`, are searched and reranked
    with, and references and recalled episodes too (`retrieval`): the character's reply
    before it, which says what 「它」 or 「那件事」 is, then `text`."""
    previous = next((m for m in reversed(history) if m.role == "character"), None)
    return joined_query(_said(previous) if previous else None, text)


def last_turn_query(messages: Sequence[Message]) -> str | None:
    """What the memories she reaches out with are found with (`Memories.for_reaching_out`):
    the last turn of `messages`, what the user said, then her reply as people see it; None
    before the first."""
    said = next((i for i in reversed(range(len(messages))) if messages[i].role == "user"), None)
    if said is None:
        return None
    replies = [_said(m) for m in messages[said + 1 :] if m.role == "character"]
    return "\n".join([messages[said].text or "", *replies])


def joined_query(previous_reply: str | None, text: str) -> str:
    """`memory_query` of `text` said after `previous_reply`, as people see it, if any."""
    return f"{previous_reply}\n{text}" if previous_reply else text


def _said(reply: Message) -> str:
    reply = reply.without_cues()
    return reply.zh or reply.ja or ""


@dataclass(frozen=True)
class NewMemory:
    """A memory the background model wrote down."""

    text: str
    supersedes: tuple[int, ...] = ()
    """The numbers it was shown the memories this one supersedes under, counting from 1;
    any it was not shown are left for `Memories._add` to ignore."""
    scope: Scope = "private"
    """How widely it is shared (GLOSSARY.md「记忆」), as the background model judged it."""
    emotion: str | None = None
    """The feeling she had about it (GLOSSARY.md「记忆」); None without one."""
    tier: str = "medium"
    """How strong it is: strong, medium or weak (`TIERS`, GLOSSARY.md「记忆」)."""
    subject: Subject = "producer"
    """What it is about: the producer, or the source character herself."""
    evidence: str | None = None
    """The words of her reply that show a self memory; checked before it is kept."""


def parse_memories(answer: str) -> list[NewMemory] | None:
    """The memories in the background model's answer; None if it is not the JSON asked for.
    A memory written as text alone, as before superseding, supersedes nothing; what it
    supersedes is read as whole numbers only."""
    start, end = answer.find("{"), answer.rfind("}")
    try:
        memories = json.loads(answer[start : end + 1])["memories"] if start >= 0 else None
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(memories, list):
        return None
    written: dict[str, NewMemory] = {}
    for memory in memories:
        if isinstance(memory, dict):
            text, supersedes, scope = memory.get("text"), memory.get("supersedes"), memory.get("scope")
            emotion, tier = memory.get("emotion"), memory.get("tier")
            subject, evidence = memory.get("subject"), memory.get("evidence")
        else:
            text, supersedes, scope = memory, None, "shared"
            emotion, tier, subject, evidence = None, "medium", "producer", None
        if not isinstance(text, str) or not (text := text.strip()[:MAX_MEMORY_LENGTH]):
            continue
        numbers = supersedes if isinstance(supersedes, list) else []
        subject = subject if subject in SUBJECTS else "producer"
        if scope in SCOPES:
            chosen = scope
        else:
            chosen = "unit" if subject == "producer" else "private"
            log.warning(
                "memory scope %r is not one of %s; using %s for %r",
                scope, SCOPES, chosen, text[:30],
            )
        written.setdefault(text, NewMemory(
            text, tuple(dict.fromkeys(n for n in numbers if type(n) is int)),
            chosen,
            emotion if isinstance(emotion, str) and emotion.strip() else None,
            tier if tier in TIERS else "medium",
            subject,
            evidence.strip()[:MAX_MEMORY_LENGTH]
            if isinstance(evidence, str) and evidence.strip() else None,
        ))
    return list(written.values())[:MAX_NEW_MEMORIES]


def parse_moment(answer: str) -> str | None:
    """What the background model found mattered to her in the turn, for her diary
    (GLOSSARY.md「日记」); None where it found nothing, or wrote it in a way that cannot be
    read."""
    written = _array(answer, "moment")
    if not isinstance(written, str) or not (said := written.strip()):
        return None
    return said[:MAX_MOMENT]


def _array(answer: str, key: str) -> object:
    """The value under `key` of the background model's answer, read on its own; None where
    the answer is not the JSON asked for."""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0:
        return None
    try:
        written = json.loads(answer[start : end + 1])
    except ValueError:
        return None
    return written.get(key) if isinstance(written, dict) else None


_FOUND = (
    "下面是制作人以前说过的、和这句话可能有关的事，按相关程度排列，和话题无关的可以忽略。"
    "像平时记得这些事一样自然地提起，不要逐条复述。"
)
_REACHING_OUT = (
    "下面是制作人以前对你说过的事：先是和你们上次聊的可能有关的，再是制作人这两周的近况。"
    "挑一两件自然地提起，不要逐条复述。"
)

_POSTING = (
    "下面是制作人以前对你说过的事：先是和你们上次聊的可能有关的，再是制作人这两周的近况。"
    "朋友圈大家都看得到：最多隐约呼应一件，不要复述，他私下告诉你的事不要点明。"
)


def _holding(memories: Sequence[Memory], character_id: str | None = None) -> list[Memory]:
    """Those of `memories` that still hold, only those told to `character_id` if given."""
    return [
        m for m in memories
        if m.superseded_by is None and character_id in (None, m.source_character_id)
    ]


def _alive(memory: Memory, now: datetime) -> bool:
    """Whether `memory` is still active at `now`."""
    if memory.status != "active":
        return False
    if memory.expires_at is None:
        return True
    try:
        return datetime.fromisoformat(memory.expires_at) > now
    except ValueError:
        return True


def _remaining_hours(memory: Memory, now: datetime) -> float | None:
    """How long until `memory` is forgotten, or None while it has no expiry."""
    if memory.status == "dormant":
        return 0.0
    if memory.expires_at is None:
        return None
    try:
        return max(0.0, (datetime.fromisoformat(memory.expires_at) - now).total_seconds() / 3600)
    except ValueError:
        return None


_RELATIVE_DAYS: dict[int, str] = {
    -6: "六天前",
    -5: "五天前",
    -4: "四天前",
    -3: "三天前",
    -2: "前天",
    -1: "昨天",
    0: "今天",
    1: "明天",
    2: "后天",
    3: "三天后",
    4: "四天后",
    5: "五天后",
    6: "六天后",
}
"""How a day within a week of today is said in a prompt, by how many days away it is: the
absolute date is kept for anything further, which the producer's own day would not make
clearer."""

_WRITTEN_DATE = re.compile(
    r"(?<!\d)(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})(?!\d)\s*[日号]?"
)
"""A date as the background model writes one down: 2026年11月9日, 2026-11-09 or 2026/11/9."""


def _relative_day(day: date, today: date) -> str | None:
    """How `day` is said relative to `today`, or None for a day more than a week away."""
    return _RELATIVE_DAYS.get((day - today).days)


def _relative_dates(text: str, today: date) -> str:
    """`text` with every written date within a week of `today` said as how long ago or
    ahead it is, the rest left as written."""

    def written(match: re.Match[str]) -> str:
        try:
            day = date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            return match.group(0)
        return _relative_day(day, today) or match.group(0)

    return _WRITTEN_DATE.sub(written, text)


_SELF_OPENING = (
    "下面是她自己以前说过的、和这句话可能有关的事，按相关程度排列，和话题无关的可以忽略。"
    "像平时记得这些事一样自然地提起，不要逐条复述。"
)
_BOTH_OPENING = (
    "下面是制作人和她共同经历过、和这句话可能有关的事，按相关程度排列，和话题无关的可以忽略。"
    "像平时记得这些事一样自然地提起，不要逐条复述。"
)


_LONG_TERM = (
    "下面是你已经稳定记住的事。像平时就知道一样自然，不要逐条复述，也不要强调自己记得。"
)


def _long_term_prompt(memories: Sequence[Memory], timezone: str, now: datetime) -> str:
    """The prompt section carrying the stable memories she keeps, newest first."""
    today = now.astimezone(ZoneInfo(timezone)).date()
    lines = ["# 长期记得的事", _LONG_TERM]
    lines += [f"- {_relative_dates(memory.text, today)}" for memory in memories]
    return "\n".join(lines)


def _memories_prompt(
    memories: Sequence[Memory], character: Character, timezone: str, opening: str,
    now: datetime,
) -> str:
    """The prompt section carrying `memories`, under a heading for each subject and with its
    dates within a week of `now`, the producer's, said as how long ago or ahead they are."""
    today = now.astimezone(ZoneInfo(timezone)).date()
    sections: list[str] = []
    for subject, heading, text_opening in (
        ("producer", "# 关于制作人的记忆", opening),
        ("self", "# 关于她自己的记忆", _SELF_OPENING),
        ("both", "# 你们共同的记忆", _BOTH_OPENING),
    ):
        group = [m for m in memories if m.subject == subject]
        if not group:
            continue
        lines = [heading, text_opening]
        for memory in group:
            told = datetime.fromisoformat(memory.created_at).astimezone(ZoneInfo(timezone))
            when = _relative_day(told.date(), today) or _date(told)
            text = _relative_dates(memory.text, today)
            name = memory.source_character_name or ANOTHER_IDOL
            if subject == "producer":
                if memory.source_character_id == character.id:
                    lines.append(f"- {text}（{when}，制作人对你说的）")
                else:
                    lines.append(
                        f"- {text}（{when}，制作人对{name}说的；你知道这件事，"
                        "但不要把它说成你亲历的，需要提及时自然带过）"
                    )
            elif subject == "both":
                if memory.source_character_id == character.id:
                    lines.append(f"- {text}（{when}，是你们一起经历的）")
                else:
                    lines.append(
                        f"- {text}（{when}，是{name}和制作人一起经历的；你知道这件事，"
                        "但不要声称自己也在场，需要提及时自然带过）"
                    )
            elif memory.source_character_id == character.id:
                lines.append(f"- {text}（{when}，是你自己说的）")
            else:
                lines.append(
                    f"- {text}（{when}，是{name}自己说的；你知道她说过这件事，"
                    "不要当成你自己的经历，需要提及时自然带过）"
                )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _date(moment: datetime) -> str:
    return f"{moment.year}年{moment.month}月{moment.day}日"


def posting_memories_prompt(
    memories: Sequence[Memory], character: Character, timezone: str, now: datetime
) -> str:
    """The prompt section carrying `memories` as she posts a moment with them, in the order
    given, the user in `timezone` and `now`, the producer's: as `Memories.for_reaching_out`
    writes it, `posting`."""
    return _memories_prompt(memories, character, timezone, _POSTING, now)
