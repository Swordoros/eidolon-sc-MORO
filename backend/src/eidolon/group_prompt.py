"""The group chat's prompt pieces, kept apart from its scheduling and storage."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from eidolon.producer import history_stamp


def history_block(
    lines: Sequence, now: datetime, timezone: str, names: Mapping[str, str]
) -> str:
    """The group's recent lines as one block: relative time, speaker, Chinese subtitle.

    The history is Chinese, like the single chat's; the output format still asks for Japanese.
    No producer profile, affinity or producer memories: these are characters talking among
    themselves.
    """
    rendered: list[str] = []
    for line in lines:
        speaker = "制作人" if line.speaker == "user" else names.get(line.speaker, line.speaker)
        stamp = history_stamp(line.created_at, now, timezone)
        rendered.append(f"{stamp} {speaker}：{line.zh or line.text}")
    return "\n".join(rendered)
