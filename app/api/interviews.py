"""Routes to create interviews and attach documents: pasted text or uploaded files.

- POST /interviews          body InterviewIn -> the new interview
- GET  /interviews/{id}     the interview, its brief status, its documents and their chunk counts
- PATCH /interviews/{id}    change company, role, type or language (the brief is then rewritten)
- POST /documents           pasted text (body DocumentIn); leave interview_id empty for your CV
- POST /documents/upload    a file (PDF, DOCX, EML, TXT, MD), sent as a multipart form
- POST /documents/from-url  a link (job post, company page): the server reads the page's text
- GET  /documents/cv        your CV documents (shared by your interviews);  DELETE /documents/{id} removes one

Everything is per user: `user` (from current_user, app/auth/deps.py) is the logged-in account.
New interviews and documents get its user_id, and an id that belongs to someone else answers
404, exactly like an id that doesn't exist (app/auth/ownership.py).

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

from app.auth.deps import current_user
from app.auth.ownership import owned_document, owned_interview
from app.db.models import Document, Interview, User
from app.db.session import get_db
from app.rag.embeddings import EmbeddingError
from app.rag.ingest import ingest_document
from app.rag.parsers import ParseError, parse_file
from app.rag.webpage import fetch_page

router = APIRouter(tags=["interviews"])

Mode = Literal["behavioral", "job_post", "technical_backend", "system_design"]
DocType = Literal["cv", "job_post", "recruiter_email", "notes", "web_page", "other"]
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
    interview_id: uuid.UUID | None = Field(None, description="Leave empty for the CV, shared by all your interviews")
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
             "chars": len(d.raw_text or ""), "chunks": len(d.chunks),
             # pasted texts come back in full, so the page can put them back in the form
             "pasted": d.source.startswith("pasted"),
             "text": d.raw_text if d.source.startswith("pasted") else None}
            for d in i.documents
        ],
    }


class InterviewUpdate(BaseModel):
    """Only the fields you send are changed."""

    company: str | None = None
    role: str | None = None
    mode: Mode | None = None
    language: Language | None = None


@router.post("/interviews", status_code=201)
def create_interview(body: InterviewIn, db: DB = Depends(get_db), user: User = Depends(current_user)) -> dict:
    interview = Interview(**body.model_dump(), user_id=user.id)
    db.add(interview)
    db.commit()
    db.refresh(interview)
    return interview_out(interview)


@router.patch("/interviews/{interview_id}")
def update_interview(interview_id: uuid.UUID, body: InterviewUpdate, db: DB = Depends(get_db),
                     user: User = Depends(current_user)) -> dict:
    """Change an interview (e.g. after going back from its brief). Its brief must be written
    again, so an approved brief is reset."""
    interview = owned_interview(db, user, interview_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(interview, field, value)
    interview.brief, interview.brief_status = None, "none"
    db.commit()
    db.refresh(interview)
    return interview_out(interview)


@router.get("/interviews/{interview_id}")
def get_interview(interview_id: uuid.UUID, db: DB = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return interview_out(owned_interview(db, user, interview_id))


def _check_target(db: DB, user: User, interview_id: uuid.UUID | None, doc_type: str) -> None:
    """A document can only be attached to one of your own interviews (or be your CV)."""
    if interview_id:
        owned_interview(db, user, interview_id)
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
def add_document(body: DocumentIn, db: DB = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Pasted text."""
    _check_target(db, user, body.interview_id, body.doc_type)
    return _save_and_ingest(db, Document(
        user_id=user.id, interview_id=body.interview_id, doc_type=body.doc_type, source=body.source, raw_text=body.text,
    ))


@router.post("/documents/upload", status_code=201)
def upload_document(
    file: UploadFile = File(..., description="PDF, DOCX, EML, TXT or MD"),
    doc_type: DocType | None = Form(None, description="Guessed from the file if empty (.eml -> recruiter_email)"),
    interview_id: uuid.UUID | None = Form(None, description="Leave empty for your CV"),
    db: DB = Depends(get_db),
    user: User = Depends(current_user),
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
    _check_target(db, user, interview_id, doc_type)
    return _save_and_ingest(db, Document(
        user_id=user.id, interview_id=interview_id, doc_type=doc_type, source=file.filename or "upload", raw_text=parsed.text,
    ))


class LinkIn(BaseModel):
    interview_id: uuid.UUID
    url: str = Field(min_length=8, description="A job post or company page, starting with http(s)://")
    doc_type: DocType = "web_page"


@router.post("/documents/from-url", status_code=201)
def add_document_from_url(body: LinkIn, db: DB = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """A web page: the server downloads it, keeps its readable text, then chunks and embeds it."""
    _check_target(db, user, body.interview_id, body.doc_type)
    try:
        title, text = fetch_page(body.url)
    except ParseError as exc:
        raise HTTPException(422, str(exc))
    header = f"{title}\n{body.url}\n\n" if title else f"{body.url}\n\n"
    return _save_and_ingest(db, Document(
        user_id=user.id, interview_id=body.interview_id, doc_type=body.doc_type, source=body.url[:300], raw_text=header + text,
    ))


@router.get("/documents/cv")
def list_cv(db: DB = Depends(get_db), user: User = Depends(current_user)) -> list[dict]:
    """Your CV documents (used by every one of your interviews)."""
    docs = db.scalars(
        select(Document)
        .where(Document.user_id == user.id, Document.interview_id.is_(None))
        .order_by(Document.created_at)
    ).all()
    return [{"id": str(d.id), "source": d.source, "chars": len(d.raw_text or ""), "chunks": len(d.chunks)} for d in docs]


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: uuid.UUID, db: DB = Depends(get_db), user: User = Depends(current_user)) -> None:
    """Remove a document and its chunks (e.g. an old CV). Briefs already written are not changed."""
    db.delete(owned_document(db, user, document_id))
    db.commit()
