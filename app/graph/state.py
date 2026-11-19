"""The graph's state: the one dictionary that every node reads from and writes to.

A TypedDict only describes the keys and their types (for readers and the editor); at run time
the state is a plain dict. Nodes never change it in place: they return a dict of the keys they
changed, and LangGraph merges it in. After every node the checkpointer saves the whole state,
so a session can stop and resume at any point.

total=False means keys may be missing (final_feedback exists only at the end), which is why
nodes read optional keys with state.get("key").
"""

from typing import NotRequired, TypedDict


class DocumentInput(TypedDict):
    doc_type: str
    source: str
    text: str


class Turn(TypedDict):
    role: str  # "interviewer" | "candidate"
    text: str
    q_index: int
    followup: bool
    sources: NotRequired[list[str]]  # interviewer turns: documents the question drew on


class InterviewState(TypedDict, total=False):
    # Set when the session starts
    session_id: str
    interview_id: str
    mode: str  # behavioral | job_post | technical_backend | system_design
    language: str  # en | fr | es (missing in sessions started before Phase 2 = "en")
    company: str
    role: str
    documents: list[DocumentInput]
    num_questions: int

    # Brief, and my review of it
    brief: dict | None
    brief_feedback: str | None  # change request from the review step
    brief_approved: bool

    # Interview loop
    questions: list[dict]  # PlannedQuestion dicts
    q_index: int  # current main question
    followups: int  # follow-ups asked on the current question
    followup_focus: str | None  # set when the next ask is a follow-up
    current_question: str
    transcript: list[Turn]
    evaluations: list[dict]  # one per answer: AnswerEvaluation + question/answer

    # End
    final_feedback: dict | None
