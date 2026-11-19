"""Routes to create interviews and attach documents (pasted text for now; file uploads in Phase 3).

- POST /interviews        body InterviewIn -> the new interview
- GET  /interviews/{id}   the interview, its brief status and its documents
- POST /documents         body DocumentIn; leave interview_id empty for your CV (shared)

InterviewIn and DocumentIn are Pydantic models: FastAPI validates each request against them, and
the allowed values (Mode, DocType, Language) come from the Literal types. `db` is a database session from
the get_db dependency; db.commit() saves the changes.
"""

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DB

from app.db.models import Document, Interview
from app.db.session import get_db

router = APIRouter(tags=["interviews"])

Mode = Literal["behavioral", "job_post"]  # technical_backend, system_design: Phase 3
DocType = Literal["cv", "job_post", "recruiter_email", "notes", "other"]
Language = Literal["en", "fr", "es"]  # interview, voice, brief and feedback language


class InterviewIn(BaseModel):
    company: str
    role: str
    interview_date: date | None = None
    interviewers: list[str] = Field(default_factory=list)
    mode: Mode = "behavioral"
    language: Language = "en"


class DocumentIn(BaseModel):
    interview_id: uuid.UUID | None = Field(None, description="Leave empty for the CV, shared by all interviews")
    doc_type: DocType
    source: str = "pasted"
    text: str = Field(min_length=1)


def interview_out(i: Interview) -> dict:
    return {
        "id": str(i.id),
        "company": i.company,
        "role": i.role,
        "interview_date": i.interview_date.isoformat() if i.interview_date else None,
        "interviewers": i.interviewers,
        "mode": i.mode,
        "language": i.language,
        "brief_status": i.brief_status,
        "brief": i.brief,
        "documents": [
            {"id": str(d.id), "doc_type": d.doc_type, "source": d.source, "chars": len(d.raw_text or "")}
            for d in i.documents
        ],
    } 


@router.post("/interviews", status_code=201)
def create_interview(body: InterviewIn, db: DB = Depends(get_db)) -> dict:
    interview = Interview(**body.model_dump())
    db.add(interview)
    db.commit()
    db.refresh(interview)
    return interview_out(interview)


@router.get("/interviews/{interview_id}")
def get_interview(interview_id: uuid.UUID, db: DB = Depends(get_db)) -> dict:
    interview = db.get(Interview, interview_id)
    if not interview:
        raise HTTPException(404, "Interview not found")
    return interview_out(interview)


@router.post("/documents", status_code=201)
def add_document(body: DocumentIn, db: DB = Depends(get_db)) -> dict:
    if body.interview_id and not db.get(Interview, body.interview_id):
        raise HTTPException(404, "Interview not found")
    if body.interview_id is None and body.doc_type != "cv":
        raise HTTPException(422, "Only the CV can be stored without an interview_id")
    doc = Document(
        interview_id=body.interview_id,
        doc_type=body.doc_type,
        source=body.source,
        raw_text=body.text,
    )
    db.add(doc)
    db.commit()
    return {"id": str(doc.id), "interview_id": str(doc.interview_id) if doc.interview_id else None}
