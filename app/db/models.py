"""The app's database tables, written as Python classes (SQLAlchemy "ORM models").

Each class is one table and each Mapped[...] attribute one column. Code works with objects
(`Interview(company=...)`, `db.get(Interview, some_id)`) and SQLAlchemy writes the SQL.

- Interview: one upcoming interview (company, role, mode, language) and, once you approve it,
  its brief. It belongs to one user (user_id).
- Document: a text attached to an interview (job post, email, notes). A CV has no
  interview_id, so it is shared by all the interviews of its user (user_id).
- Chunk: a small piece of a document with its embedding (a vector column from pgvector), used
  to find the passages that match a question. interview_id and user_id are copied from the
  document, so a search can filter on them without a join.
- QuestionBankItem: a seeded technical / system design question, with its embedding.
- Session: one practice run. Its progress (questions, answers, scores) is not stored here: it is
  in LangGraph's checkpoint tables, under the session's thread_id.
- User: one account, known by its email (you log in with a code sent by email; an admin can
  also have a password, Phase 4.8). LoginSession: one logged-in browser or CLI run; its token lives in a cookie and only a
  hash of it is stored here (app/auth/ uses both). Not to be confused with Session above, which
  is a practice run.
- EmailCode: one 6-digit code sent by email to log in (or to confirm deleting an account); only
  its HMAC is stored (app/auth/codes.py).
- AuthAttempt: one code sent or one wrong code, counted by app/auth/limits.py to slow down
  guessing and inbox flooding.
- Usage: one paid API call (Claude, Deepgram, ElevenLabs, Voyage) with what it used and cost,
  written by app/costs/usage.py and added up by app/costs/budget.py.
- QuestionAudio: the spoken audio of one question in one session, so a replay costs nothing.
- AccessRequest: someone asking for more practices / interviews ("Request more"), and what was
  granted (scripts/manage_users.py grant).

Who owns what: interviews, documents and chunks have a user_id; sessions belong to their
interview. app/auth/ownership.py checks it on every request (someone else's row answers 404).

Editing a class does not change the database by itself: also add a migration in migrations/versions/.
"""

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Interview(Base):
    """One upcoming interview: company, role, and the brief prepared for it."""

    __tablename__ = "interviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    """An uploaded file or pasted text. interview_id is null for a CV (shared by its user's interviews)."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    interview_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    doc_type: Mapped[str] = mapped_column(String(40))  # cv | job_post | recruiter_email | notes | other
    source: Mapped[str] = mapped_column(String(300), default="pasted")
    raw_text: Mapped[str | None] = mapped_column(Text)
    s3_key: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    interview: Mapped[Interview | None] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", passive_deletes=True)


class Chunk(Base):
    """A searchable piece of a document: its text and its embedding (1024 numbers)."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    interview_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # copied from the document
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)  # same
    chunk_index: Mapped[int]  # position in the document: 0, 1, 2...
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))
    # "metadata" is a reserved name in SQLAlchemy classes, so the attribute is called meta
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)  # doc_type, source

    document: Mapped[Document] = relationship(back_populates="chunks")


class QuestionBankItem(Base):
    """A seeded question for the technical modes (from question_bank/*.yaml), with its embedding."""

    __tablename__ = "question_bank"
    __table_args__ = (UniqueConstraint("mode", "text"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(40), index=True)  # technical_backend | system_design
    topic: Mapped[str] = mapped_column(String(100))
    difficulty: Mapped[str] = mapped_column(String(20))  # easy | medium | hard
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))


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
    # Cost control (Phase 4.5): when the questions were planned (the interview really started,
    # counted by the "interviews per day" limit), and how many brief revisions were asked for.
    questions_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    brief_revisions: Mapped[int] = mapped_column(Integer, default=0)
    # "practice" (short: 3 questions, Haiku feedback; a new account's first session) or "interview"
    kind: Mapped[str] = mapped_column(String(20), default="interview")


class User(Base):
    """One account. The email is stored lowercased, so "Ana@X.com" and "ana@x.com" are the same."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # first code login
    password_hash: Mapped[str | None] = mapped_column(String(200))  # admins only (Phase 4.8): Argon2
    # How many sessions of each kind this person may start in all (Phase 4.7; admins have no
    # quota). They never reset by themselves: manage_users.py grant adds more.
    practice_quota: Mapped[int] = mapped_column(Integer, default=1)
    interview_quota: Mapped[int] = mapped_column(Integer, default=2)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # set = can't log in
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LoginSession(Base):
    """One login: the cookie holds a random token, this row holds its SHA-256 hash.

    Deleting the row logs that browser out at once. A row past expires_at no longer counts.
    """

    __tablename__ = "login_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship()


class EmailCode(Base):
    """A login code sent by email. Valid until expires_at, for at most code_max_tries tries, once."""

    __tablename__ = "email_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), index=True)  # lowercased
    purpose: Mapped[str] = mapped_column(String(20))  # login | delete_account
    code_hash: Mapped[str] = mapped_column(String(64))  # HMAC-SHA-256 with SECRET_KEY
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # set = can't be used again


class AuthAttempt(Base):
    """A code sent (kind "code_sent") or a wrong code (kind "login_failed")."""

    __tablename__ = "auth_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(320))
    ip: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Usage(Base):
    """One paid API call: who it was for, what it used (quantity of unit) and what it cost.

    user_id and session_id become empty if the account or session is deleted, but the row stays,
    so the month's spend is still right. Rows with no user are the server's own work (seeding
    the question bank, evaluations, the smoke test).
    """

    __tablename__ = "usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # claude | deepgram | elevenlabs | voyage
    model: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(20))  # tokens | seconds | characters
    quantity: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)  # e.g. Claude's four token counts
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))


class QuestionAudio(Base):
    """The interviewer's audio for one question text in one session (deleted with the session)."""

    __tablename__ = "question_audio"
    __table_args__ = (UniqueConstraint("session_id", "text_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    text_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 of the voice settings + language + text
    mime_type: Mapped[str] = mapped_column(String(50))
    audio: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccessRequest(Base):
    """A request for more practices or interviews; handled_at is set when the admin grants it."""

    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    practices_added: Mapped[int] = mapped_column(Integer, default=0)
    interviews_added: Mapped[int] = mapped_column(Integer, default=0)
