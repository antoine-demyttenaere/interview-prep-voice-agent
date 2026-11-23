"""Migration 0010: log in with a code sent by email, no more passwords or invites (Phase 4.7 step 1).

- email_codes: one row per 6-digit code sent by email (to log in, or to confirm deleting an
  account). Only an HMAC of the code is stored (app/auth/codes.py), with its expiry, how many
  wrong tries were made, and when it was used.
- users.email_verified_at: set the first time someone logs in with a code, which proves the
  inbox is theirs.
- users.password_hash is dropped: there are no passwords any more. Existing accounts keep their
  email and data, and log in with a code from now on.
- invites is dropped: anyone can sign up with a verified email; quotas (step 2) decide what
  they can use.

Downgrade puts back an empty invites table and a password_hash column, but the passwords
themselves are gone: accounts would need new ones.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, index=True),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.drop_column("users", "password_hash")
    op.drop_table("invites")


def downgrade() -> None:
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
    op.add_column("users", sa.Column("password_hash", sa.String(200), nullable=False, server_default="!"))
    op.drop_column("users", "email_verified_at")
    op.drop_table("email_codes")
