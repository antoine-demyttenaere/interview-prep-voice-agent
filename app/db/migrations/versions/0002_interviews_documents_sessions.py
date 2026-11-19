"""Migration 0002: create the interviews, documents and sessions tables.

The columns match the classes in app/db/models.py. down_revision = "0001" chains this migration
after the first one, so Alembic applies them in order. downgrade() drops the tables again.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("role", sa.String(200), nullable=False),
        sa.Column("interview_date", sa.Date()),
        sa.Column("interviewers", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("mode", sa.String(40), nullable=False, server_default="behavioral"),
        sa.Column("brief", postgresql.JSONB()),
        sa.Column("brief_status", sa.String(20), nullable=False, server_default="none"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "interview_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interviews.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("doc_type", sa.String(40), nullable=False),
        sa.Column("source", sa.String(300), nullable=False, server_default="pasted"),
        sa.Column("raw_text", sa.Text()),
        sa.Column("s3_key", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "interview_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("interviews.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("thread_id", sa.String(100), nullable=False, unique=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("sessions")
    op.drop_table("documents")
    op.drop_table("interviews")
