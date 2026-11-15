from eidolon.affinity import Judged
from eidolon.group_affinity import MAX_TARGETS, step, targets


def test_group_steps_collapse_the_private_levels():
    assert step(Judged(0)) == -0.1
    assert step(Judged(1)) == -0.1
    assert step(Judged(2)) == 0.0
    assert step(Judged(3)) == 0.1
    assert step(Judged(4)) == 0.1


def test_group_out_of_line_is_five_and_manipulation_never_raises():
    assert step(Judged(0, out_of_line=True)) == -5.0
    assert step(Judged(4, manipulation=True)) == 0.0
    assert step(Judged(0, manipulation=True)) == -0.1


def test_group_targets_prefer_addressed_then_mentioned():
    assert targets(["mano"], ["hiori"]) == ["mano"]
    assert targets([], ["hiori", "mano"]) == ["hiori", "mano"]
    assert targets([], []) == []


def test_group_targets_are_capped():
    assert len(targets(["a", "b", "c"], [])) == MAX_TARGETS
