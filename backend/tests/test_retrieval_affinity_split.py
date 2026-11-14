"""Retrieval keeps her reply; affinity asks the same model in a second, reply-free request."""

from typing import Any

from eidolon.retrieval import ModelRetrievalJudge
from tests.test_retrieval_services import MANO, message


class RecordingJudge:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, dict]]] = []

    async def ask(self, state: dict[str, Any], questions: dict[str, dict]) -> dict[str, Any]:
        self.calls.append((state, questions))
        if "recall" in questions:
            return {
                "needs_retrieval": {"noul": 0.0},
                "recall": {"noul": 0.0},
                "about_producer": {"noul": 0.0},
            }
        return {
            "affinity": {"probabilities": {"2": 1.0}},
            "out_of_line": {"noul": 0.0},
            "manipulation": {"noul": 0.0},
            "beyond": {"noul": 0.0},
        }


async def test_retrieval_sees_the_whole_turn_but_affinity_does_not():
    model = RecordingJudge()
    judge = ModelRetrievalJudge(model)  # type: ignore[arg-type]
    history = [message("user", "old"), message("character", "おかえり")]
    need, judged, photo = await judge.judge(MANO, history, "ただいま", affinity=True)

    assert need == "nothing" and photo is False and judged is not None
    assert len(model.calls) == 2

    retrieval_state, retrieval_questions = model.calls[0]
    assert "recall" in retrieval_questions and "affinity" not in retrieval_questions
    assert retrieval_state["上文"] == ["制作人：old", "櫻木真乃：おかえり"]

    affinity_state, affinity_questions = model.calls[1]
    assert "affinity" in affinity_questions and "recall" not in affinity_questions
    assert affinity_state["上文"] == ["制作人：old"]
