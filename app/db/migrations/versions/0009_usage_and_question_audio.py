"""Migration 0009: cost control tables (Phase 4.5).

- usage: one row per paid API call (provider, model, what it used, its cost in dollars, and
  the user and session it was for). app/costs/budget.py adds the rows up against the budgets.
  When an account or a session is deleted, its rows stay but lose the link (ON DELETE SET
  NULL), so the month's spend stays right.
- question_audio: the interviewer's audio of each question, per session, so replaying a
  question doesn't pay ElevenLabs again. Deleted with its session (CASCADE).
- sessions.questions_started_at: when the questions were planned, i.e. when the interview really
  started (the "interviews per day" limit counts these). sessions.brief_revisions: how many times
  changes to the brief were asked for (capped per session).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="SET NULL")),
    )
    op.create_index("ix_usage_created_at", "usage", ["created_at"])
    op.create_index("ix_usage_user_id", "usage", ["user_id"])

    op.create_table(
        "question_audio",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.Column("audio", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "text_hash"),
    )

    op.add_column("sessions", sa.Column("questions_started_at", sa.DateTime(timezone=True)))
    op.add_column("sessions", sa.Column("brief_revisions", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("sessions", "brief_revisions")
    op.drop_column("sessions", "questions_started_at")
    op.drop_table("question_audio")
    op.drop_table("usage")
