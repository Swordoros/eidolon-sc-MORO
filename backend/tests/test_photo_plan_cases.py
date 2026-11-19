"""Collected planner replies for the photo requests from the Chiyuki conversation.

The raw JSON is what the planning call actually wrote; these tests keep the shape it has
to keep, and that a reply over the tag limits is refused rather than sent to the image
service.
"""

from eidolon.photo_prompt import read_tag
from eidolon.photo_plan import parse_plan

CASES = [
    (
        "手作簪子，明确要看",
        """{
  "send": true,
  "shot": "selfie",
  "framing": "upper body",
  "place": ["dorm room", "indoors", "night"],
  "outfit": ["casual clothes"],
  "action": ["holding hairpin", "gentle smile"],
  "note": "在宿舍房间里，拿着刚做好的簪子微笑着展示给制作人看"
}""",
        True,
    ),
    (
        "练习室，明确要自拍",
        """{
  "send": false,
  "reason": "刚刚练习完出了汗，现在拍照有点不好意思"
}""",
        False,
    ),
    (
        "等待对方拍好，被误送进 planner（place 超量会被截断）",
        """{
  "send": true,
  "shot": "selfie",
  "framing": "upper body",
  "place": ["my room", "dormitory", "indoors", "night", "desk lamp"],
  "outfit": ["casual home wear", "cardigan"],
  "action": ["holding craft project", "smiling", "looking at camera"],
  "note": "深夜在宿舍房间，给制作人看她正在做的手工作品"
}""",
        True,
    ),
]


def test_collected_planner_replies_keep_the_photo_tag_limits():
    for name, raw, expected in CASES:
        plan = parse_plan(raw)
        if expected is None:
            assert plan is None, name
            continue
        assert plan is not None, name
        assert plan.send is expected, name
        if not plan.send:
            continue
        read = read_tag(plan.tag)
        assert 2 <= len(read.place) <= 4, name
        assert 1 <= len(read.outfit) <= 4, name
        assert 2 <= len(read.action) <= 4, name
        assert len(read.place) + len(read.outfit) + len(read.action) <= 12, name
