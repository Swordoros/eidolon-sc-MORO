"""The plan for a photo a turn sends, apart from the reply's words (GLOSSARY.md「照片」).

The chat model no longer has to write a photo tag: when the judge says the user asks for
a photo and a photo is possible, a separate planning call decides whether she wants to
send one and what it is, as JSON. The photo begins from that plan before her words, and
her words are told what the system has already sent.
"""

import json
import re
from collections.abc import Sequence
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
  "place": ["practice room", "indoors", "day"],
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
- place 1–3 个英文 tag，outfit 1–4 个，action 2–4 个；合起来不超过 12 个。照片里只有她一个人。
- 不写发色、瞳色、发型、身材，不写画风或画师。
- 按当前时间、地点、穿着和正在聊的事来拍；制作人指定了地点或服装时，在全年龄范围内照办。
- 全年龄：不拍裸露或半裸、内衣和情趣服装、透视、走光、洗澡，也不拍性感的姿势、表情和镜头。泳装、睡衣、躺在床上可以拍。
- 如果当前是亲密关系且成人照片已开启，可以按她的意愿拍得更亲密；但不要主动越过制作人没有要求的尺度。
- 如果她不想发，send 为 false，reason 用一句中文说明她为什么推掉。
- note 是一句中文，说明这张照片是什么样子。
"""

FINAL_ASK = "只判断这一轮，按上面的格式输出 JSON。不要扮演角色，不要写台词。"


TRIP_PLANNER = """\
# 照片规划：外景
你是照片规划器，不是角色本人。你只判断这次外景要不要发照片，以及发一张什么样的照片。
只输出一个 JSON 对象，不要写 <ja>、<zh>、<photo>，不要解释，不要 Markdown 代码块。

要发：
{
  "send": true,
  "shot": "selfie",
  "framing": "upper body",
  "place": ["Hiroshima", "outdoors", "day"],
  "outfit": ["white blouse", "cardigan"],
  "action": ["smiling", "looking at camera"],
  "group": "no",
  "others": "",
  "scene": "",
  "words": "",
  "words_on": "",
  "note": "在广岛出外景，空下来时给制作人拍的自拍"
}

合照时：
{
  "send": true,
  "shot": "other",
  "framing": "cowboy shot",
  "place": ["Hiroshima", "outdoors", "day"],
  "outfit": ["white blouse", "cardigan"],
  "action": ["smiling", "standing with them"],
  "group": "yes",
  "others": "asahi: navy coat, holding camera / fuyuko: yellow hoodie, waving",
  "scene": "the three of them standing close together in front of the location",
  "note": "在广岛和朝日、冬优子一起拍的合照"
}

不想发：
{
  "send": false,
  "reason": "现在正忙着，没空拍"
}

规则：
- 同行者只是可选，不要因为她们在就默认合照。可以只拍自己；想分享现场和大家的氛围时才合照。
- group 只能是 yes 或 no；只有 yes 时写 others，格式是「英文 id: 2–4 个英文 tag / 英文 id: 2–4 个英文 tag」。
- 合照要拍得近一点：两个人 framing 最远写 cowboy shot，三个人以上写 upper body；不要拍全身远景。
- 单人照里只有她一个人；合照里只有她和同行者，没有别的人。
- shot 只能是 selfie 或 other；framing 只能是 portrait、upper body、cowboy shot、full body；自拍只能选 portrait 或 upper body。
- place 1–3 个英文 tag；outfit 1–4 个；action 2–4 个；place、outfit、action 合起来不超过 12 个。
- scene 一句简短英文，写画面怎么摆；只写画面里真的有的东西，不用比喻，不写否定。颜色用纯颜色词。
- words 可以不写；有字时 words_on 写英文位置。
- 不写发色、瞳色、发型、身材，也不写画风或画师。
- 照片只有全年龄的内容。
- note 是一句中文，说明这张照片是什么样子。
- 不要提制作人也在照片里；这次外景照片只拍她和同行者。
"""

TRIP_FINAL_ASK = "只判断这次外景，按上面的格式输出 JSON。不要扮演角色，不要写台词。"



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
    place: str | None = None,
    companions: Sequence[str] = (),
    all_together: bool = False,
) -> str:
    """The small, self-contained context the planning call sees, apart from the roleplay
    prompt: who she is, where and when this is, what was just said; and, for a trip photo,
    `place` and the `companions` there with her."""
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
    if place:
        lines.append(f"- 现在在外景：{place}")
    if companions:
        lines.append("- 同行者：" + "、".join(companions))
        lines.append(
            "- 同行者只是可选：可以拍单人，也可以合照；"
            + ("这次一定要拍合照。" if all_together else "不要默认拍合照。")
        )
    lines.append(f"- 成人照片：{'允许' if adult else '不允许'}")
    if recent:
        lines.append("\n# 最近对话\n" + "\n".join(recent[-8:]))
    if said:
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
    place = _items(payload.get("place"))[:3]
    outfit = _items(payload.get("outfit"))[:4]
    action = _items(payload.get("action"))[:4]
    if not place or not outfit or len(action) < 2:
        return None
    if len(place) + len(outfit) + len(action) > 12:
        return None
    group = _text(payload.get("group")).lower() == "yes"
    others = _text(payload.get("others"))
    scene = _text(payload.get("scene"))
    words = _text(payload.get("words"))
    words_on = _text(payload.get("words_on"))
    if group and not others:
        return None
    lines = [
        f"shot: {_text(payload.get('shot')) or 'selfie'}",
        f"framing: {_text(payload.get('framing')) or 'upper body'}",
        f"place: {', '.join(place)}",
        f"outfit: {', '.join(outfit)}",
        f"action: {', '.join(action)}",
    ]
    if group:
        lines += ["group: yes", f"others: {others}"]
    if scene:
        lines.append(f"scene: {scene}")
    if words:
        lines.append(f"words: {words}")
        if words_on:
            lines.append(f"words_on: {words_on}")
    lines.append(f"note: {note}")
    tag = "\n" + "\n".join(lines) + "\n"
    try:
        read = read_tag_adult(tag) if adult else read_tag(tag)
    except (TypeError, ValueError):
        return None
    if not (1 <= len(read.place) <= 3):
        return None
    if not (1 <= len(read.outfit) <= 4):
        return None
    if not (2 <= len(read.action) <= 4):
        return None
    if len(read.place) + len(read.outfit) + len(read.action) > 12:
        return None
    return PhotoPlan(True, tag=tag, note=note)


def sent_prompt(plan: PhotoPlan) -> str:
    """What her words are told about the photo the system has already begun sending."""
    fields = _tag_fields(plan.tag)
    where = ", ".join(fields.get("place", []))
    wear = ", ".join(fields.get("outfit", []))
    doing = ", ".join(fields.get("action", []))
    lines = [
        "# 这一轮的照片",
        "你这一轮已经让系统发了一张照片给制作人：",
        plan.note,
        f"- 取景：{fields.get('shot', 'selfie')}，{fields.get('framing', 'upper body')}",
        f"- 地点：{where}",
        f"- 穿着：{wear}",
        f"- 动作：{doing}",
    ]
    if fields.get("group") == "yes":
        lines.append(f"- 合照：{fields.get('others', '')}")
    if fields.get("scene"):
        lines.append(f"- 画面：{fields['scene']}")
    lines += [
        "照片由系统发送，不要写 <photo> 标签。",
        "你的台词要和这张照片一致：不要说没拍、没发、发不了，",
        "也不要描述成另一张照片。",
    ]
    return "\n".join(lines)


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


def _items(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, list):
        values = value
    else:
        return []
    return [part for item in values if (part := _text(item))]


def _tag_fields(tag: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for line in tag.splitlines():
        name, colon, value = line.partition(":")
        if not colon:
            continue
        key = name.strip().lower()
        if key in {"shot", "framing", "group", "others", "scene", "words", "words_on"}:
            fields[key] = value.strip()
        elif key in {"place", "outfit", "action"}:
            fields[key] = [part.strip() for part in value.split(",") if part.strip()]
    return fields
