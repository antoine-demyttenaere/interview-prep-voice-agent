"""Ownership checks: load a row only if it belongs to the logged-in user, else answer 404.

Used by every route in app/api/interviews.py and app/api/sessions.py, right after
current_user (app/auth/deps.py) has said who is calling:

    interview = owned_interview(db, user, interview_id)

- owned_interview / owned_document: the row's user_id must be the caller's.
- owned_session: a session has no user_id; it belongs to its interview, so the check goes
  through the interview (a join).

Why 404 and not 403 ("forbidden"): a 403 would confirm that the id exists. With 404, someone
else's interview looks exactly like one that was never created, so ids can't be probed.
Keeping these checks in one place means a new route only has to call one of them.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.db.models import Document, Interview, Session, User


def owned_interview(db: DB, user: User, interview_id: uuid.UUID) -> Interview:
    interview = db.get(Interview, interview_id)
    if not interview or interview.user_id != user.id:
        raise HTTPException(404, "Interview not found")
    return interview


def owned_document(db: DB, user: User, document_id: uuid.UUID) -> Document:
    doc = db.get(Document, document_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(404, "Document not found")
    return doc


def owned_session(db: DB, user: User, session_id: uuid.UUID) -> Session:
    session = db.scalar(
        select(Session).join(Interview, Session.interview_id == Interview.id)
        .where(Session.id == session_id, Interview.user_id == user.id)
    )
    if not session:
        raise HTTPException(404, "Session not found")
    return session
