"""Character cards: what a character is told about someone the producer names.

A card is assembled for each configured character named in what the producer just said (or
in the last few things they said), never for the character doing the talking. It has two
layers: who the named character is, from her own facts and her introductory line, which
every speaker gets alike; and how the speaker stands with her, from the speaker's own
`人物关系` section about her, which is the speaker's alone. The card is put into the prompt
as its own section, not among the references the reranker chose, so a name the producer
actually said cannot be lost to a similarity threshold; the fact the relation came from is
left out of the references, so the same words do not reach the prompt twice. After the
cards, `related` lists what those characters lately told her, from the shared and unit
memories she may recall and that still hold, so the two can talk about it as recent news.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from eidolon.characters import Character, Fact
from eidolon.embedding import EmbeddingError
from eidolon.facts import CharacterFacts
from eidolon.memory import FORGET_THRESHOLD, Memory
from eidolon.store import Message

MOST_MENTIONED = 2
"""How many named characters one card section carries at most."""
RECENT_TEXTS = 3
"""How many of the producer's latest lines are looked at for a name, the latest first."""
INTRO_CHARS = 160
"""How much of her introductory line a card keeps."""
FACT_CHARS = 180
"""How much of one fact a card keeps."""
RELATED_CHARS = 100
"""How much of one related memory a card keeps."""
RELATED_MOST = 2
"""How many related memories one card section carries at most."""
_BASIC = "基本信息"


@dataclass(frozen=True)
class Card:
    """One turn's character cards and the facts already carried by them."""

    prompt: str
    covered: frozenset[str]
    """Titles of the speaker's facts the cards quote, not to be quoted again by references."""


class CharacterCards:
    """The cards of the characters named in a turn, by their names, readings and aliases."""

    def __init__(self, characters: Iterable[Character]) -> None:
        self._characters = tuple(characters)
        self._aliases = _AliasIndex(self._characters)

    def mentioned(
        self, speaker: Character, history: Sequence[Message], text: str
    ) -> list[Character]:
        """The other characters named in `text` or the producer's latest lines, the latest
        naming first, each once, `MOST_MENTIONED` at most."""
        texts = [text]
        for message in reversed(history):
            if len(texts) >= RECENT_TEXTS:
                break
            if message.role == "user" and message.text:
                texts.append(message.text)
        found: list[Character] = []
        for chunk in texts:
            for character in self._aliases.in_text(chunk):
                if character.id == speaker.id or any(c.id == character.id for c in found):
                    continue
                found.append(character)
                if len(found) >= MOST_MENTIONED:
                    return found
        return found

    async def build(
        self,
        speaker: Character,
        mentioned: Sequence[Character],
        facts: CharacterFacts | None = None,
        query_vector: Sequence[float] | None = None,
    ) -> Card | None:
        """The prompt section for `mentioned`, each a card of who she is and how `speaker`
        stands with her; None where none are named."""
        if not mentioned:
            return None
        blocks: list[str] = []
        covered: set[str] = set()
        for other in mentioned:
            block = [f"## {_heading(other)}"]
            if other.intro:
                block.append(f"- {_brief(other.intro, INTRO_CHARS)}")
            basic = _fact_titled(other, _BASIC)
            if basic is not None:
                block.append(f"- {_first_sentences(basic.body, FACT_CHARS)}")
            relation = _relation(speaker, other)
            if relation is not None and relation.title is not None:
                covered.add(relation.title)
                block.append(
                    f"- 和你（{_spoken_name(speaker)}）：{_brief(relation.body, FACT_CHARS)}"
                )
            used = set(covered)
            if basic is not None and basic.title is not None:
                used.add(basic.title)
            relevant = await _relevant(other, facts, query_vector, used)
            if relevant is not None:
                block.append(f"- {_brief(relevant, FACT_CHARS)}")
            blocks.append("\n".join(block))
        return Card(
            "# 她提到的人\n"
            "下面是设定资料，供你把话写对；不等于她亲口知道或记得这些事。\n\n"
            + "\n\n".join(blocks),
            frozenset(covered),
        )

    def related(
        self,
        mentioned: Sequence[Character],
        memories: Sequence[Memory],
        now: datetime,
        timezone: str,
        exclude: Sequence[int] = (),
    ) -> str | None:
        """What the named characters lately told her about the producer or themselves, one
        memory each at most: those `memories` this character may recall, from the named
        one, that still hold (`FORGET_THRESHOLD`) and are shared or within the unit. None
        without any."""
        if not mentioned or not memories:
            return None
        used = set(exclude)
        lines: list[str] = []
        for other in mentioned:
            found = [
                memory for memory in memories
                if memory.source_character_id == other.id
                and memory.scope in ("shared", "unit")
                and memory.strength >= FORGET_THRESHOLD
                and memory.id not in used
            ]
            if not found:
                continue
            memory = max(found, key=lambda each: each.created_at)
            used.add(memory.id)
            lines.append(
                f"- {_ago(memory.created_at, now, timezone)}"
                f"{_spoken_name(other)}告诉过你：{_brief(memory.text, RELATED_CHARS)}"
            )
            if len(lines) >= RELATED_MOST:
                break
        if not lines:
            return None
        return (
            "# 相关内容\n"
            "这些是她们告诉过你、你也知道的事；可以当作话题自然提起，"
            "但这是转述，不是原话，也不要说成你亲眼见过。\n"
            + "\n".join(lines)
        )


async def _relevant(
    character: Character,
    facts: CharacterFacts | None,
    query_vector: Sequence[float] | None,
    used: set[str],
) -> str | None:
    """One of her own facts nearest the turn, beyond `used`; None without an embedder, a
    query, or anything left."""
    if facts is None or query_vector is None:
        return None
    try:
        found = await facts.search(query_vector, character, 3)
    except EmbeddingError:
        return None
    for reference in found:
        if reference.origin in used:
            continue
        return reference.text.split("\n", 1)[-1].strip()
    return None


def _heading(character: Character) -> str:
    reading = " / ".join(part for part in (character.name_kana, character.name_romaji) if part)
    return f"{character.name}（{reading}）" if reading else character.name


def _spoken_name(character: Character) -> str:
    return character.short_name or character.name_zh or character.name


def _fact_titled(character: Character, word: str) -> Fact | None:
    return next((fact for fact in character.facts if word in (fact.title or "")), None)


def _relation(speaker: Character, other: Character) -> Fact | None:
    """The speaker's own fact about `other`, by her name in its title."""
    for fact in speaker.facts:
        title = fact.title or ""
        if title and _mentions(title, other):
            return fact
    return None


def _mentions(text: str, character: Character) -> bool:
    normalized = _normalize(text)
    return any(alias in normalized for alias in _aliases(character))


def _ago(created_at: str, now: datetime, timezone: str) -> str:
    """How long before `now` the memory was written: today, yesterday, or how many days."""
    zone = ZoneInfo(timezone)
    told = datetime.fromisoformat(created_at).astimezone(zone)
    days = (now.astimezone(zone).date() - told.date()).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    return f"{days}天前"


def _brief(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    end = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
    if end >= limit // 2:
        return cut[: end + 1]
    return cut.rstrip("、，, ") + "…"


def _first_sentences(text: str, limit: int, most: int = 2) -> str:
    """Her first sentences, `most` at most and up to `limit`: enough for who she is without
    her whole official blurb."""
    cleaned = " ".join(text.split())
    kept = ""
    for sentence in re.split(r"(?<=[。！？])", cleaned):
        if not sentence:
            continue
        if kept and (len(kept) + len(sentence) > limit or kept.count("。") >= most):
            break
        kept += sentence
    return kept or _brief(cleaned, limit)


class _AliasIndex:
    """Every configured character's names, longest first, for finding them in a line."""

    def __init__(self, characters: Sequence[Character]) -> None:
        pairs = [
            (alias, character)
            for character in characters
            for alias in _aliases(character)
            if alias
        ]
        self._pairs = sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)

    def in_text(self, text: str) -> list[Character]:
        normalized = _normalize(text)
        hits: list[tuple[int, int, Character]] = []
        for alias, character in self._pairs:
            at = normalized.find(alias)
            if at >= 0:
                hits.append((at, -len(alias), character))
        hits.sort(key=lambda hit: (hit[0], hit[1]))
        found: list[Character] = []
        for _, _, character in hits:
            if all(c.id != character.id for c in found):
                found.append(character)
        return found


def _aliases(character: Character) -> tuple[str, ...]:
    """Her names as they may be written: full name, Chinese name, reading, romaji, and the
    given names both sides use. One character long names are left out: they match too much
    inside other words."""
    names = {
        character.name,
        character.name_zh,
        character.name_kana,
        character.name_romaji,
        character.short_name,
    }
    if (
        character.name_zh
        and character.short_name
        and _is_chinese(character.short_name)
        and len(character.name) > len(character.short_name)
    ):
        given = len(character.name) - len(character.short_name)
        if given < len(character.name_zh):
            names.add(character.name_zh[given:])
    normalized = {_normalize(name) for name in names if name}
    return tuple(alias for alias in normalized if len(alias) >= 2)


def _is_chinese(text: str) -> bool:
    return all("\u4e00" <= char <= "\u9fff" for char in text)


def _normalize(text: str) -> str:
    """A name or line for matching: no spaces, katakana as hiragana, casefolded."""
    return _hiragana(re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))).casefold()


def _hiragana(text: str) -> str:
    return "".join(
        chr(ord(char) - 0x60) if "\u30a1" <= char <= "\u30f6" else char for char in text
    )
