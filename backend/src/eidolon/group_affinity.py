"""Affinity's small, explicit part in a group chat.

The group uses the same affinity as a private turn: one value per user and character,
the same tiers, daily gains, recovery and cooldown. Its turns are quieter: a turn that
made her happy or unhappy moves it by 0.1, and one that was out of line takes 5 off.
Only the producer's own line is judged, and only the members it was addressed to (or,
when none was named, the ones it mentioned). A character's line never changes affinity.
"""

from __future__ import annotations

from collections.abc import Iterable

from eidolon.affinity import NO_EFFECT, Judged

GROUP_STEP = 0.1
"""How much an ordinary happy or unhappy group line moves affinity."""

GROUP_OUT_OF_LINE_STEP = -5.0
"""How much an out-of-line group line takes off affinity."""

MAX_TARGETS = 2
"""How many members one producer line may change at most."""


def targets(addressed: Iterable[str], mentioned: Iterable[str]) -> list[str]:
    """Who a producer's group line changes: those addressed, else those mentioned, at most
    `MAX_TARGETS`, in the order given."""
    chosen: list[str] = []
    candidates = [*addressed] if addressed else [*mentioned]
    for character_id in candidates:
        if character_id not in chosen:
            chosen.append(character_id)
        if len(chosen) >= MAX_TARGETS:
            break
    return chosen


def step(judged: Judged) -> float:
    """The group's step for `judged`: out of line -5, happy +0.1, unhappy -0.1, else 0.

    The five private levels collapse in a group: either it was happy, unhappy, or neither.
    Her days' larger penalties and the considerate bonus stay private.
    """
    if judged.out_of_line:
        return GROUP_OUT_OF_LINE_STEP
    if judged.manipulation and judged.level > NO_EFFECT:
        return 0.0
    if judged.level <= 1:
        return -GROUP_STEP
    if judged.level >= 3:
        return GROUP_STEP
    return 0.0
