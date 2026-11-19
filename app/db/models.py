"""The app's database tables, written as Python classes (SQLAlchemy "ORM models").

Each class is one table and each Mapped[...] attribute one column. Code works with objects
(`Interview(company=...)`, `db.get(Interview, some_id)`) and SQLAlchemy writes the SQL.

- Interview: one upcoming interview (company, role, mode, language) and, once you approve it,
  its brief.
- Document: a text attached to an interview (job post, email, notes). Your CV has no
  interview_id, so it is shared by all interviews.
- Session: one practice run. Its progress (questions, answers, scores) is not stored here: it is
  in LangGraph's checkpoint tables, under the session's thread_id.

Editing a class does not change the database by itself: also add a migration in migrations/versions/.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Interview(Base):
    """One upcoming interview: company, role, and the brief prepared for it."""

    __tablename__ = "interviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(200))
    interview_date: Mapped[date | None] = mapped_column(Date)
    interviewers: Mapped[list] = mapped_column(JSONB, default=list)
    mode: Mapped[str] = mapped_column(String(40), default="behavioral")
    language: Mapped[str] = mapped_column(String(5), default="en")  # en | fr | es
    brief: Mapped[dict | None] = mapped_column(JSONB)
    brief_status: Mapped[str] = mapped_column(String(20), default="none")  # none | approved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    documents: Mapped[list["Document"]] = relationship(back_populates="interview")


class Document(Base):
    """An uploaded file or pasted text. interview_id is null for the shared CV."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    interview_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    doc_type: Mapped[str] = mapped_column(String(40))  # cv | job_post | recruiter_email | notes | other
    source: Mapped[str] = mapped_column(String(300), default="pasted")
    raw_text: Mapped[str | None] = mapped_column(Text)
    s3_key: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    interview: Mapped[Interview | None] = relationship(back_populates="documents")


class Session(Base):
    """One practice run of an interview; its graph state lives under thread_id."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    interview_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str] = mapped_column(String(100), unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
