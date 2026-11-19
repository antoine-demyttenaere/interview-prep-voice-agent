"""Shared test helpers: a fake Claude, so tests run free, fast and without an API key.

pytest loads conftest.py automatically. Any test that takes the `fake` argument gets a
FakeClaude swapped into app/graph/nodes.py in place of the real structured() / text_reply().
"""

import pytest

from app.graph import nodes
from app.graph.schemas import (
    AnswerEvaluation,
    CriterionScore,
    FinalFeedback,
    InterviewBrief,
    PlannedQuestion,
    QuestionPlan,
)

BRIEF = InterviewBrief(
    company_summary="Fake Co makes fakes.",
    role_requirements=["Python"],
    interview_format="One hour",
    interviewers=[],
    cv_gaps=["none"],
    focus_areas=["ownership"],
    probable_questions=["Tell me about a failure"],
)


class FakeClaude:
    """Stands in for app.llm.structured / text_reply. Follows up when an answer says 'vague'."""

    def __init__(self):
        self.calls: list[str] = []
        self.systems: list[str] = []  # system prompts sent, to check the language

    def structured(self, *, schema, user, system, **_):
        self.calls.append(schema.__name__)
        self.systems.append(system)
        if schema is InterviewBrief:
            return BRIEF
        if schema is QuestionPlan:
            return QuestionPlan(
                questions=[PlannedQuestion(text=f"Q{i}", topic="t", rationale="r") for i in range(1, 4)]
            )
        if schema is AnswerEvaluation:
            last_answer = user.rsplit("Candidate:", 1)[-1].split("</exchange>", 1)[0]
            vague = "vague" in last_answer
            return AnswerEvaluation(
                scores=[CriterionScore(criterion="structure", score=2 if vague else 4, note="n")],
                overall=2 if vague else 4,
                summary="s",
                follow_up=vague,
                follow_up_focus="give details" if vague else "",
            )
        if schema is FinalFeedback:
            return FinalFeedback(
                overall_score=3, summary="ok", rubric_scores=[], per_question=[], strengths=["a"], fixes=["b"]
            )
        raise AssertionError(schema)

    def text_reply(self, *, user, **_):
        self.calls.append("ask")
        return "follow-up?" if "needs a follow-up" in user else "next question?"


@pytest.fixture
def fake(monkeypatch):
    f = FakeClaude()
    monkeypatch.setattr(nodes, "structured", f.structured)
    monkeypatch.setattr(nodes, "text_reply", f.text_reply)
    return f
