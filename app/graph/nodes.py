"""The steps (nodes) of the interviewer graph, plus the routing functions that pick the next step.

Each node receives the state and returns only the keys it changes. In order:

1. prepare_brief    Sonnet reads the documents and writes the Interview Brief.
2. review_brief     pauses (interrupt) so you approve, edit or ask for changes.
3. plan_questions   Sonnet picks the main questions from the approved brief.
4. ask              Haiku says the next question, or a follow-up, in natural words.
5. answer           pauses (interrupt) until you answer.
6. evaluate_answer  Haiku scores the answer with the rubric and decides on a follow-up.
7. final_feedback   Sonnet writes the report from the transcript and the scores.

How interrupt() works: the node stops and the API returns what it is waiting for. When you
reply, the API resumes the graph with Command(resume=your_reply); the node runs again from its
start, and this time interrupt() returns your reply instead of pausing. So code placed before
interrupt() must be safe to run twice.

Sonnet (settings.model_reasoning) does the slow, careful work; Haiku (settings.model_turn) runs
each turn quickly, because in Phase 2 you will be waiting for it to speak.
"""

import json

from langgraph.types import interrupt

from app.config import get_settings
from app.graph import prompts
from app.graph.schemas import AnswerEvaluation, FinalFeedback, InterviewBrief, QuestionPlan
from app.graph.state import InterviewState
from app.llm import structured, text_reply


# ---------- helpers ----------

def _documents_block(state: InterviewState) -> str:
    parts = [
        f'<document type="{d["doc_type"]}" source="{d["source"]}">\n{d["text"].strip()}\n</document>'
        for d in state.get("documents", [])
    ]
    return "\n\n".join(parts) or "(no documents provided)"


def _brief_json(state: InterviewState) -> str:
    # sort_keys keeps the cached system prompt byte-identical across turns
    return json.dumps(state["brief"], indent=2, sort_keys=True, ensure_ascii=False)


def _language(state: InterviewState) -> str:
    return prompts.language_name(state.get("language", "en"))


def _interviewer_system(state: InterviewState) -> str:
    return prompts.render(
        "interviewer_system",
        company=state["company"],
        role=state["role"],
        language=_language(state),
        mode_guidance=prompts.mode_guidance(state["mode"]),
        brief=_brief_json(state),
    )


def _transcript_text(turns: list[dict]) -> str:
    if not turns:
        return "(the interview has not started yet)"
    label = {"interviewer": "Interviewer", "candidate": "Candidate"}
    return "\n\n".join(f"{label[t['role']]}: {t['text']}" for t in turns)


# ---------- nodes ----------

def prepare_brief(state: InterviewState) -> dict:
    s = get_settings()
    revision = ""
    if state.get("brief_feedback") and state.get("brief"):
        revision = prompts.render(
            "revise_brief",
            previous_brief=_brief_json(state),
            feedback=state["brief_feedback"],
        )
    brief = structured(
        model=s.model_reasoning,
        system=prompts.render(
            "prepare_brief",
            company=state["company"],
            role=state["role"],
            language=_language(state),
            mode_guidance=prompts.mode_guidance(state["mode"]),
        ),
        user=f"{_documents_block(state)}\n\n{revision}".strip(),
        schema=InterviewBrief,
        max_tokens=16000,
        effort="high",
        fallback=True,
    )
    return {"brief": brief.model_dump(), "brief_feedback": None, "brief_approved": False}


def review_brief(state: InterviewState) -> dict:
    """Pause until I approve, edit or ask for changes. The API validates the decision."""
    decision = interrupt({"type": "review_brief", "brief": state["brief"]})
    action = decision["action"]
    if action == "approve":
        return {"brief_approved": True}
    if action == "edit":
        edited = InterviewBrief.model_validate(decision["brief"])
        return {"brief": edited.model_dump(), "brief_approved": True}
    # "revise": go back to prepare_brief with my feedback
    return {"brief_feedback": decision["feedback"], "brief_approved": False}


def plan_questions(state: InterviewState) -> dict:
    s = get_settings()
    n = state.get("num_questions") or s.num_questions
    plan = structured(
        model=s.model_reasoning,
        system=_interviewer_system(state),
        user=prompts.render("plan_questions", num_questions=n, rubric=prompts.rubric_text(state["mode"])),
        schema=QuestionPlan,
        max_tokens=8000,
        effort="medium",
        fallback=True,
    )
    return {
        "questions": [q.model_dump() for q in plan.questions[:n]],
        "q_index": 0,
        "followups": 0,
        "followup_focus": None,
        "transcript": [],
        "evaluations": [],
    }


def ask(state: InterviewState) -> dict:
    s = get_settings()
    q_index = state["q_index"]
    question = state["questions"][q_index]
    focus = state.get("followup_focus")
    if focus:
        directive = prompts.render("ask_followup", focus=focus, question=question["text"])
    else:
        directive = prompts.render(
            "ask_question",
            question=question["text"],
            number=q_index + 1,
            total=len(state["questions"]),
        )
    text = text_reply(
        model=s.model_turn,
        system=_interviewer_system(state),
        user=prompts.render(
            "ask",
            transcript=_transcript_text(state.get("transcript", [])),
            directive=directive,
        ),
        max_tokens=2000,
        effort="low",
    )
    turn = {"role": "interviewer", "text": text, "q_index": q_index, "followup": bool(focus)}
    return {"current_question": text, "transcript": state.get("transcript", []) + [turn]}


def answer(state: InterviewState) -> dict:
    """Pause until I answer (typed now, transcribed speech in Phase 2)."""
    reply = interrupt(
        {
            "type": "answer",
            "question": state["current_question"],
            "q_index": state["q_index"],
            "total": len(state["questions"]),
            "followup": bool(state.get("followup_focus")),
        }
    )
    turn = {
        "role": "candidate",
        "text": reply,
        "q_index": state["q_index"],
        "followup": bool(state.get("followup_focus")),
    }
    return {"transcript": state["transcript"] + [turn]}


def evaluate_answer(state: InterviewState) -> dict:
    s = get_settings()
    q_index = state["q_index"]
    question_turns = [t for t in state["transcript"] if t["q_index"] == q_index]
    ev = structured(
        model=s.model_turn,
        system=_interviewer_system(state),
        user=prompts.render(
            "evaluate_answer",
            rubric=prompts.rubric_text(state["mode"]),
            planned_question=state["questions"][q_index]["text"],
            exchange=_transcript_text(question_turns),
            followups_used=state["followups"],
            max_followups=s.max_followups,
        ),
        schema=AnswerEvaluation,
        max_tokens=4000,
        effort="medium",
    )
    record = {
        "q_index": q_index,
        "followup": bool(state.get("followup_focus")),
        "question": state["current_question"],
        "answer": state["transcript"][-1]["text"],
        **ev.model_dump(),
    }
    evaluations = state.get("evaluations", []) + [record]

    if ev.follow_up and ev.follow_up_focus.strip() and state["followups"] < s.max_followups:
        return {
            "evaluations": evaluations,
            "followups": state["followups"] + 1,
            "followup_focus": ev.follow_up_focus,
        }
    return {"evaluations": evaluations, "q_index": q_index + 1, "followups": 0, "followup_focus": None}


def final_feedback(state: InterviewState) -> dict:
    s = get_settings()
    evaluations = json.dumps(
        [{k: e[k] for k in ("question", "answer", "scores", "overall", "summary")} for e in state["evaluations"]],
        indent=2,
        ensure_ascii=False,
    )
    feedback = structured(
        model=s.model_reasoning,
        system=prompts.render(
            "final_feedback",
            company=state["company"],
            role=state["role"],
            language=_language(state),
            mode_guidance=prompts.mode_guidance(state["mode"]),
            brief=_brief_json(state),
            rubric=prompts.rubric_text(state["mode"]),
        ),
        user=f"<transcript>\n{_transcript_text(state['transcript'])}\n</transcript>\n\n"
        f"<turn_evaluations>\n{evaluations}\n</turn_evaluations>",
        schema=FinalFeedback,
        max_tokens=16000,
        effort="high",
        fallback=True,
    )
    return {"final_feedback": feedback.model_dump()}


# ---------- routing ----------

def route_start(state: InterviewState) -> str:
    """Reuse an already-approved brief for a new practice session."""
    return "plan_questions" if state.get("brief_approved") and state.get("brief") else "prepare_brief"


def route_after_review(state: InterviewState) -> str:
    return "plan_questions" if state.get("brief_approved") else "prepare_brief"


def route_after_evaluate(state: InterviewState) -> str:
    if state.get("followup_focus"):
        return "ask"
    if state["q_index"] < len(state["questions"]):
        return "ask"
    return "final_feedback"
