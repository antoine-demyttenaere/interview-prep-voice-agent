"""Routes to create interviews and attach documents: pasted text or uploaded files.

- POST /interviews          body InterviewIn -> the new interview
- GET  /interviews/{id}     the interview, its brief status, its documents and their chunk counts
- POST /documents           pasted text (body DocumentIn); leave interview_id empty for your CV
- POST /documents/upload    a file (PDF, DOCX, EML, TXT, MD), sent as a multipart form
- GET  /documents/cv        your shared CV documents;  DELETE /documents/{id} removes one

Every new document is saved, then ingested: cut into chunks, embedded with Voyage and stored in
the chunks table (app/rag/ingest.py), so the interviewer can search it. If embedding fails, the
document is kept and the reply says why ("embedding_error").

InterviewIn and DocumentIn are Pydantic models: FastAPI validates each request against them, and
the allowed values (Mode, DocType, Language) come from the Literal types. `db` is a database session from
the get_db dependency; db.commit() saves the changes.
"""

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.db.models import Document, Interview
from app.db.session import get_db
from app.rag.embeddings import EmbeddingError
from app.rag.ingest import ingest_document
from app.rag.parsers import ParseError, parse_file

router = APIRouter(tags=["interviews"])

Mode = Literal["behavioral", "job_post", "technical_backend", "system_design"]
DocType = Literal["cv", "job_post", "recruiter_email", "notes", "other"]
MAX_UPLOAD_BYTES = 10_000_000
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
            {"id": str(d.id), "doc_type": d.doc_type, "source": d.source,
             "chars": len(d.raw_text or ""), "chunks": len(d.chunks)}
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


def _check_target(db: DB, interview_id: uuid.UUID | None, doc_type: str) -> None:
    if interview_id and not db.get(Interview, interview_id):
        raise HTTPException(404, "Interview not found")
    if interview_id is None and doc_type != "cv":
        raise HTTPException(422, "Only the CV can be stored without an interview_id")


def _save_and_ingest(db: DB, doc: Document) -> dict:
    """Save the document, then chunk + embed it. An embedding failure keeps the document."""
    db.add(doc)
    db.commit()
    result = {"id": str(doc.id), "interview_id": str(doc.interview_id) if doc.interview_id else None,
              "doc_type": doc.doc_type, "source": doc.source, "chars": len(doc.raw_text or "")}
    try:
        result["chunks"] = ingest_document(db, doc)
    except EmbeddingError as exc:
        db.rollback()
        result.update(chunks=0, embedding_error=f"{exc}. Run scripts/embed_documents.py once it is fixed.")
    return result


@router.post("/documents", status_code=201)
def add_document(body: DocumentIn, db: DB = Depends(get_db)) -> dict:
    """Pasted text."""
    _check_target(db, body.interview_id, body.doc_type)
    return _save_and_ingest(db, Document(
        interview_id=body.interview_id, doc_type=body.doc_type, source=body.source, raw_text=body.text,
    ))


@router.post("/documents/upload", status_code=201)
def upload_document(
    file: UploadFile = File(..., description="PDF, DOCX, EML, TXT or MD"),
    doc_type: DocType | None = Form(None, description="Guessed from the file if empty (.eml -> recruiter_email)"),
    interview_id: uuid.UUID | None = Form(None, description="Leave empty for your CV"),
    db: DB = Depends(get_db),
) -> dict:
    """An uploaded file: its text is extracted, stored, then chunked and embedded."""
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // 1_000_000} MB)")
    try:
        parsed = parse_file(file.filename or "upload.txt", data)
    except ParseError as exc:
        raise HTTPException(422, str(exc))
    doc_type = doc_type or parsed.doc_type_guess
    _check_target(db, interview_id, doc_type)
    return _save_and_ingest(db, Document(
        interview_id=interview_id, doc_type=doc_type, source=file.filename or "upload", raw_text=parsed.text,
    ))


@router.get("/documents/cv")
def list_cv(db: DB = Depends(get_db)) -> list[dict]:
    """The shared CV documents (used by every interview)."""
    docs = db.scalars(select(Document).where(Document.interview_id.is_(None)).order_by(Document.created_at)).all()
    return [{"id": str(d.id), "source": d.source, "chars": len(d.raw_text or ""), "chunks": len(d.chunks)} for d in docs]


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, db: DB = Depends(get_db)) -> None:
    """Remove a document and its chunks (e.g. an old CV). Briefs already written are not changed."""
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()
