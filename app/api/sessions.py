"""Routes to run a practice session: the bridge between HTTP requests and the graph.

- POST /interviews/{id}/sessions  creates a session and runs the graph to its first pause
- GET  /sessions/{id}             what the session is waiting for (or the final feedback)
- POST /sessions/{id}/resume      sends your reply to the pause: a brief decision or an answer
- POST /sessions/{id}/retry       re-runs a step that failed (e.g. a Claude error)

Each call runs the graph until it pauses again (interrupt()) and returns a "view": status
(waiting / done / stopped), pending (what it is waiting for), and final_feedback at the end.
The checkpointer saves the graph's state under thread_id = session id. After each run, _run()
copies the approved brief and the end time into our own tables.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DB

from app.db.models import Document, Interview, Session
from app.db.session import get_db
from app.graph.schemas import InterviewBrief
from app.llm import LLMError

router = APIRouter(tags=["sessions"])


class SessionIn(BaseModel):
    num_questions: int | None = Field(None, ge=1, le=15)


class BriefDecision(BaseModel):
    action: Literal["approve", "edit", "revise"]
    brief: InterviewBrief | None = None  # for "edit"
    feedback: str | None = None  # for "revise"

    @model_validator(mode="after")
    def _check(self) -> "BriefDecision":
        if self.action == "edit" and self.brief is None:
            raise ValueError("'edit' needs the edited brief")
        if self.action == "revise" and not (self.feedback or "").strip():
            raise ValueError("'revise' needs feedback")
        return self


class ResumeIn(BaseModel):
    value: BriefDecision | str


# ---------- helpers ----------

def _config(session: Session) -> dict:
    return {"configurable": {"thread_id": session.thread_id}}


def _pending(snapshot) -> dict | None:
    for task in snapshot.tasks:
        for intr in task.interrupts:
            return intr.value
    return None


def _view(graph, session: Session) -> dict:
    snapshot = graph.get_state(_config(session))
    values = snapshot.values or {}
    pending = _pending(snapshot)
    if values.get("final_feedback"):
        status = "done"
    elif pending:
        status = "waiting"
    else:
        status = "stopped"  # e.g. a Claude call failed; POST /retry continues
    return {
        "session_id": str(session.id),
        "interview_id": str(session.interview_id),
        "status": status,
        "pending": pending,
        "q_index": values.get("q_index"),
        "total_questions": len(values.get("questions") or []),
        "final_feedback": values.get("final_feedback"),
    }


def _run(graph, session: Session, graph_input, db: DB) -> dict:
    """Run the graph to its next pause, then mirror results into our own tables."""
    try:
        graph.invoke(graph_input, _config(session))
    except LLMError as exc:
        raise HTTPException(502, f"Claude call failed: {exc}. POST /sessions/{session.id}/retry to try again.")

    values = graph.get_state(_config(session)).values or {}
    interview = db.get(Interview, session.interview_id)
    if values.get("brief_approved") and values.get("brief") != interview.brief:
        interview.brief = values["brief"]
        interview.brief_status = "approved"
    if values.get("final_feedback") and session.ended_at is None:
        session.ended_at = datetime.now(timezone.utc)
    db.commit()
    return _view(graph, session)


def _get_session(db: DB, session_id: uuid.UUID) -> Session:
    session = db.get(Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


# ---------- routes ----------

@router.post("/interviews/{interview_id}/sessions", status_code=201)
def start_session(interview_id: uuid.UUID, body: SessionIn, request: Request, db: DB = Depends(get_db)) -> dict:
    interview = db.get(Interview, interview_id)
    if not interview:
        raise HTTPException(404, "Interview not found")

    docs = db.scalars(
        select(Document)
        .where(or_(Document.interview_id == interview_id, Document.interview_id.is_(None)))
        .order_by(Document.created_at)
    ).all()
    if not docs:
        raise HTTPException(422, "Add at least one document (job post, email, notes or CV) first")

    session_id = uuid.uuid4()
    session = Session(id=session_id, interview_id=interview_id, thread_id=str(session_id))
    db.add(session)
    db.commit()

    approved = interview.brief_status == "approved" and interview.brief
    initial = {
        "session_id": str(session.id),
        "interview_id": str(interview.id),
        "mode": interview.mode,
        "company": interview.company,
        "role": interview.role,
        "documents": [
            {"doc_type": d.doc_type, "source": d.source, "text": d.raw_text or ""} for d in docs
        ],
        "num_questions": body.num_questions,
        "brief": interview.brief if approved else None,
        "brief_approved": bool(approved),
    }
    return _run(request.app.state.graph, session, initial, db)


@router.get("/sessions/{session_id}")
def get_session(session_id: uuid.UUID, request: Request, db: DB = Depends(get_db)) -> dict:
    return _view(request.app.state.graph, _get_session(db, session_id))


@router.post("/sessions/{session_id}/resume")
def resume_session(session_id: uuid.UUID, body: ResumeIn, request: Request, db: DB = Depends(get_db)) -> dict:
    graph = request.app.state.graph
    session = _get_session(db, session_id)
    pending = _pending(graph.get_state(_config(session)))
    if not pending:
        raise HTTPException(409, "This session is not waiting for input")

    if pending["type"] == "review_brief":
        if not isinstance(body.value, BriefDecision):
            raise HTTPException(422, "Expected a brief decision: {action: approve | edit | revise, ...}")
        value = body.value.model_dump(mode="json")
    else:  # "answer"
        if not isinstance(body.value, str) or not body.value.strip():
            raise HTTPException(422, "Expected the answer as non-empty text")
        value = body.value.strip()

    return _run(graph, session, Command(resume=value), db)


@router.post("/sessions/{session_id}/retry")
def retry_session(session_id: uuid.UUID, request: Request, db: DB = Depends(get_db)) -> dict:
    """Re-run the step that failed (e.g. a Claude error), from the last saved checkpoint."""
    graph = request.app.state.graph
    session = _get_session(db, session_id)
    snapshot = graph.get_state(_config(session))
    if _pending(snapshot) or not snapshot.next:
        raise HTTPException(409, "Nothing to retry: the session is waiting for input or finished")
    return _run(graph, session, None, db)
