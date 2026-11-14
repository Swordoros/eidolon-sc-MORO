"""The affinity level question's default and its ordinary-care examples."""

from eidolon.affinity_judge import LEVEL, questions_for
from tests.test_retrieval_services import MANO


def test_the_level_question_starts_from_neutral_and_puts_ordinary_care_at_three():
    instructions = questions_for(MANO)[LEVEL]["instructions"]

    assert "普通对话当作 level 2" in instructions
    assert "多数回合" in instructions
    assert "普通而真诚的关心" in instructions
    assert "是 3" in instructions
    assert "不确定时选 2" in instructions
    assert "不是命中就封顶或封底的触发词" in instructions
