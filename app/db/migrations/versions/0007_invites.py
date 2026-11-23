"""Migration 0007: create the invites table (Phase 4.6 step 3).

An invite is a one-use code made for one email address (scripts/create_invite.py). Registering
with it (POST /auth/register) creates the account and marks the invite used.

- code_hash: the SHA-256 of the code, never the code itself (shown once, when it is created),
  so someone who reads the database can't register with a pending code.
- email: the only address the code works for (lowercased).
- used_at / used_by: set when someone registers with it; a used code is refused. If that user
  is deleted later, used_by becomes empty but the invite stays used (ON DELETE SET NULL).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False, index=True),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )


def downgrade() -> None:
    op.drop_table("invites")
