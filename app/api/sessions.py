"""Routes to run a practice session: the bridge between HTTP requests and the graph.

- POST /interviews/{id}/sessions  creates a session and runs the graph to its first pause
- GET  /sessions/{id}             what the session is waiting for (or the final feedback)
- POST /sessions/{id}/resume      sends your reply to the pause: a brief decision or an answer
- POST /sessions/{id}/retry       re-runs a step that failed (e.g. a Claude error)
- POST /sessions/{id}/end         ends the interview early: feedback on the answers given so far
- POST /sessions/{id}/turn        voice: your recorded answer in, the next question as audio out
- POST /sessions/{id}/speak       voice: the current question as audio (first question, replay)

Each call runs the graph until it pauses again (interrupt()) and returns a "view": status
(waiting / done / stopped), pending (what it is waiting for), and final_feedback at the end.
The checkpointer saves the graph's state under thread_id = session id. After each run, _run()
copies the approved brief and the end time into our own tables.

A voice turn is three steps: speech-to-text (Deepgram) -> the graph (same as a typed answer)
-> text-to-speech (ElevenLabs) for the next question. It returns the view plus what was heard,
the audio (base64 text inside the JSON) and the time each step took. The whole turn is one
LangSmith trace ("voice_turn"); the audio bytes are left out of the trace, only their size is kept.
"""

import base64
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from langgraph.types import Command
from langsmith import traceable
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DB

from app.config import get_settings
from app.db.models import Document, Interview, Session
from app.db.session import get_db
from app.graph import prompts
from app.graph.schemas import InterviewBrief
from app.llm import LLMError
from app.voice import SpeechError
from app.voice.stt import get_stt
from app.voice.tts import get_tts

router = APIRouter(tags=["sessions"])

AUDIO_DIR = Path(__file__).resolve().parents[2] / "evals" / "audio"


class SessionIn(BaseModel):
    num_questions: int | None = Field(None, ge=1, le=15)
    duration_min: int | None = Field(None, ge=10, le=180, description="Interview length shown as a countdown")
    review_brief: bool = Field(False, description="Show the existing brief for review again before the questions")


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
        "language": values.get("language", "en"),
        "ended_early": bool(values.get("ended_early")),
        # the interview's length and when it really started (questions planned), for the countdown
        "duration_min": values.get("duration_min"),
        "interview_started_at": values.get("interview_started_at"),
        # Readable criterion names for the page ("ownership" -> "Personal contribution")
        "rubric": [
            {"id": c["id"], "name": c["name"]}
            for c in prompts.load_rubric(values["mode"])["criteria"]
        ] if values.get("mode") else [],
        "transcript": values.get("transcript") or [],
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
    # Going back to the brief from an interview: start at the review, with the same brief.
    review_again = body.review_brief and interview.brief
    initial = {
        "session_id": str(session.id),
        "interview_id": str(interview.id),
        "mode": interview.mode,
        "language": interview.language,
        "company": interview.company,
        "role": interview.role,
        "documents": [
            {"doc_type": d.doc_type, "source": d.source, "text": d.raw_text or ""} for d in docs
        ],
        "num_questions": body.num_questions,
        "duration_min": body.duration_min,
        "brief": interview.brief if (approved or review_again) else None,
        "brief_approved": bool(approved) and not review_again,
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


@router.post("/sessions/{session_id}/end")
def end_session(session_id: uuid.UUID, request: Request, db: DB = Depends(get_db)) -> dict:
    """Stop now and get the final feedback on the questions answered so far."""
    graph = request.app.state.graph
    session = _get_session(db, session_id)
    snapshot = graph.get_state(_config(session))
    pending = _pending(snapshot)
    if not pending or pending["type"] != "answer":
        raise HTTPException(409, "The interview can only be ended while it waits for an answer")
    if not (snapshot.values or {}).get("evaluations"):
        raise HTTPException(409, "Answer at least one question first, or start a new interview to leave this one")
    return _run(graph, session, Command(resume={"end": True}), db)


# ---------- voice ----------

def _language(graph, session: Session) -> str:
    return (graph.get_state(_config(session)).values or {}).get("language", "en")


def _waiting_answer(graph, session: Session) -> dict:
    pending = _pending(graph.get_state(_config(session)))
    if not pending or pending["type"] != "answer":
        raise HTTPException(409, "This session is not waiting for an answer to a question")
    return pending


@traceable(
    run_type="tool",
    name="speech_to_text",
    process_inputs=lambda i: {"bytes": len(i["audio"]), "mime_type": i["mime_type"], "language": i["language"]},
)
def _transcribe(audio: bytes, mime_type: str, language: str) -> dict:
    heard = get_stt().transcribe(audio, mime_type, language)
    return {"text": heard.text, "confidence": heard.confidence}


@traceable(
    run_type="tool",
    name="text_to_speech",
    process_outputs=lambda o: {"bytes": len(o["audio"]), "mime_type": o["mime_type"]},
)
def _synthesize(text: str, language: str) -> dict:
    speech = get_tts().synthesize(text, language)
    return {"audio": speech.audio, "mime_type": speech.mime_type}


def _save_recording(session: Session, q_index: int, audio: bytes, mime_type: str) -> None:
    """With SAVE_AUDIO=true, keep the recording so it can be replayed without speaking."""
    ext = next((e for e in ("webm", "ogg", "mp4", "wav", "mpeg") if e in mime_type), "bin")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    (AUDIO_DIR / f"{session.id}_q{q_index + 1}_{stamp}.{ext}").write_bytes(audio)


@traceable(
    name="voice_turn",
    process_inputs=lambda i: {
        "session_id": str(i["session"].id),
        "bytes": len(i["audio"]),
        "mime_type": i["mime_type"],
    },
    process_outputs=lambda o: {k: v for k, v in o.items() if k != "audio"},
)
def _voice_turn(graph, session: Session, audio: bytes, mime_type: str, db: DB) -> dict:
    timings: dict[str, int] = {}
    start = perf_counter()
    language = _language(graph, session)
    pending = _waiting_answer(graph, session)
    if get_settings().save_audio:
        _save_recording(session, pending["q_index"], audio, mime_type)

    # 1. What did you say?
    try:
        heard = _transcribe(audio, mime_type, language)
    except SpeechError as exc:
        raise HTTPException(502, f"Speech-to-text failed: {exc}")
    timings["speech_to_text"] = round((perf_counter() - start) * 1000)
    if not heard["text"]:
        raise HTTPException(422, "No speech recognised. Start recording, give your answer, then stop and try again.")

    # 2. The graph scores the answer and picks the next question (same as a typed answer).
    t = perf_counter()
    view = _run(graph, session, Command(resume=heard["text"]), db)
    timings["interviewer"] = round((perf_counter() - t) * 1000)

    # 3. Say the next question out loud (nothing to say once the feedback is ready).
    audio_out, audio_error = None, None
    if view["status"] == "waiting" and view["pending"]["type"] == "answer":
        t = perf_counter()
        try:
            speech = _synthesize(view["pending"]["question"], language)
            audio_out = {"mime_type": speech["mime_type"], "base64": base64.b64encode(speech["audio"]).decode()}
        except SpeechError as exc:
            audio_error = str(exc)  # the interview goes on; the page shows the text instead
        timings["text_to_speech"] = round((perf_counter() - t) * 1000)

    timings["total"] = round((perf_counter() - start) * 1000)
    return {
        **view,
        "you_said": heard["text"],
        "stt_confidence": heard["confidence"],
        "audio": audio_out,
        "audio_error": audio_error,
        "timings_ms": timings,
    }


@router.post("/sessions/{session_id}/turn")
def voice_turn(
    session_id: uuid.UUID,
    request: Request,
    audio: UploadFile = File(..., description="Your recorded answer (webm, ogg, mp4 or wav)"),
    db: DB = Depends(get_db),
) -> dict:
    graph = request.app.state.graph
    session = _get_session(db, session_id)
    _waiting_answer(graph, session)
    data = audio.file.read()
    if not data:
        raise HTTPException(422, "The recording is empty")
    return _voice_turn(graph, session, data, audio.content_type or "", db)


@router.post("/sessions/{session_id}/speak", response_class=Response)
def speak_question(session_id: uuid.UUID, request: Request, db: DB = Depends(get_db)) -> Response:
    """The current question as audio: used for the first question, typed answers and replays."""
    graph = request.app.state.graph
    session = _get_session(db, session_id)
    pending = _waiting_answer(graph, session)
    try:
        speech = _synthesize(pending["question"], _language(graph, session))
    except SpeechError as exc:
        raise HTTPException(502, f"Text-to-speech failed: {exc}")
    return Response(content=speech["audio"], media_type=speech["mime_type"])
