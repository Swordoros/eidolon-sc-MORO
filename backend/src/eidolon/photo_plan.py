"""The plan for a photo a turn sends, apart from the reply's words (GLOSSARY.md「照片」).

The chat model no longer has to write a photo tag: when the judge says the user asks for
a photo and a photo is possible, a separate planning call decides whether she wants to
send one and what it is, as JSON. The photo begins from that plan before her words, and
her words are told what the system has already sent.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

from eidolon.photo_prompt import read_tag, read_tag_adult


@dataclass(frozen=True)
class PhotoPlan:
    """A photo the planning call would send (`send`), its tag and the note her words are
    told; or, when she does not want to send one, why not."""

    send: bool
    tag: str = ""
    note: str = ""
    reason: str = ""


PLANNER = """\
# 照片规划
你是照片规划器，不是角色本人。你只判断这一轮要不要发照片，以及发一张什么样的照片。

只输出一个 JSON 对象，不要写 <ja>、<zh>、<photo>，不要解释，不要 Markdown 代码块。

要发：
{
  "send": true,
  "shot": "selfie",
  "framing": "upper body",
  "place": ["practice room", "indoors", "day", "sunlight"],
  "outfit": ["white blouse", "cardigan"],
  "action": ["holding hairpin", "smile"],
  "note": "在练习室给制作人看她刚做好的簪子"
}

不想发：
{
  "send": false,
  "reason": "正在忙，手上都是材料"
}

规则：
- shot 只能是 selfie 或 other。
- framing 只能是 portrait、upper body、cowboy shot、full body；自拍只能 portrait 或 upper body。
- place、outfit、action 都是英文 tag 数组；合起来不超过 12 个。照片里只有她一个人。
- 不写发色、瞳色、发型、身材，不写画风或画师。
- 按当前时间、地点、穿着和正在聊的事来拍；制作人指定了地点或服装时，在全年龄范围内照办。
- 全年龄：不拍裸露或半裸、内衣和情趣服装、透视、走光、洗澡，也不拍性感的姿势、表情和镜头。泳装、睡衣、躺在床上可以拍。
- 如果当前是亲密关系且成人照片已开启，可以按她的意愿拍得更亲密；但不要主动越过制作人没有要求的尺度。
- 如果她不想发，send 为 false，reason 用一句中文说明她为什么推掉。
- note 是一句中文，说明这张照片是什么样子。
"""

FINAL_ASK = "只判断这一轮，按上面的格式输出 JSON。不要扮演角色，不要写台词。"


def planner_context(
    *,
    name: str,
    intro: str,
    sheet: str,
    now: str,
    doing: str | None,
    scene: str | None,
    recent: list[str],
    said: str,
    tier: str | None,
    adult: bool,
) -> str:
    """The small, self-contained context the planning call sees, apart from the roleplay
    prompt: who she is, where and when this is, what was just said."""
    lines = [
        f"# 角色\n{name}：{intro}",
        f"\n# 她的性格与说话方式\n{sheet[:3500]}",
        f"\n# 此刻\n- 现在：{now}",
    ]
    if doing:
        lines.append(doing)
    if scene:
        lines.append(f"- 当前关系场景：\n{scene}")
    if tier:
        lines.append(f"- 和制作人的关系：{tier}")
    lines.append(f"- 成人照片：{'允许' if adult else '不允许'}")
    if recent:
        lines.append("\n# 最近对话\n" + "\n".join(recent[-8:]))
    lines.append(f"\n# 制作人刚才说\n{said}")
    return "\n".join(lines)



def parse_plan(text: str, adult: bool = False) -> PhotoPlan | None:
    """The plan `text` says, or None if it is not a readable, valid plan."""
    payload = _payload(text)
    if payload is None:
        return None
    send = payload.get("send")
    if send is False:
        reason = _text(payload.get("reason")) or "这轮不想发照片"
        return PhotoPlan(False, reason=reason)
    if send is not True:
        return None
    note = _text(payload.get("note")) or "一张照片"
    tag = "\n" + "\n".join(
        [
            f"shot: {_text(payload.get('shot')) or 'selfie'}",
            f"framing: {_text(payload.get('framing')) or 'upper body'}",
            f"place: {_tags(payload.get('place'))}",
            f"outfit: {_tags(payload.get('outfit'))}",
            f"action: {_tags(payload.get('action'))}",
            f"note: {note}",
        ]
    ) + "\n"
    try:
        read = read_tag_adult(tag) if adult else read_tag(tag)
    except (TypeError, ValueError):
        return None
    if not read.place or not read.outfit or not read.action:
        return None
    return PhotoPlan(True, tag=tag, note=note)


def sent_prompt(plan: PhotoPlan) -> str:
    """What her words are told about the photo the system has already begun sending."""
    fields = _tag_fields(plan.tag)
    where = ", ".join(fields.get("place", []))
    wear = ", ".join(fields.get("outfit", []))
    doing = ", ".join(fields.get("action", []))
    return (
        "# 这一轮的照片\n"
        "你这一轮已经让系统发了一张照片给制作人：\n"
        f"{plan.note}\n"
        f"- 取景：{fields.get('shot', 'selfie')}，{fields.get('framing', 'upper body')}\n"
        f"- 地点：{where}\n"
        f"- 穿着：{wear}\n"
        f"- 动作：{doing}\n"
        "照片由系统发送，不要写 <photo> 标签。\n"
        "你的台词要和这张照片一致：不要说没拍、没发、发不了，"
        "也不要描述成另一张照片。"
    )


def refused_prompt(plan: PhotoPlan) -> str:
    """What her words are told when the plan says she does not want to send one."""
    return (
        "# 这一轮的照片\n"
        f"制作人在要照片，但你这轮不想发：{plan.reason}。\n"
        "用你自己的话推掉，不要写 <photo> 标签，也不要说已经拍了、已经发了。"
    )


def _payload(text: str) -> dict[str, Any] | None:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body, flags=re.IGNORECASE)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(body[start : end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _text(value: Any) -> str:
    return " ".join(str(value).split()) if isinstance(value, (str, int, float)) else ""


def _tags(value: Any) -> str:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        return ""
    return ", ".join(part for item in values if (part := _text(item)))


def _tag_fields(tag: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for line in tag.splitlines():
        name, colon, value = line.partition(":")
        if colon and name.strip().lower() in {"shot", "framing", "place", "outfit", "action"}:
            key = name.strip().lower()
            fields[key] = value.strip() if key in {"shot", "framing"} else [
                part.strip() for part in value.split(",") if part.strip()
            ]
    return fields
