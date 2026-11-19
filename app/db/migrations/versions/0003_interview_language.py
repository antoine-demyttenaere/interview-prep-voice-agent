"""Migration 0003: add a language column to interviews (en, fr or es).

The language decides what the interviewer speaks, what Deepgram listens for, the voice's
language, and the language of the brief and the feedback. Existing interviews get "en".
server_default fills the column for rows that already exist.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("interviews", sa.Column("language", sa.String(5), nullable=False, server_default="en"))


def downgrade() -> None:
    op.drop_column("interviews", "language")
