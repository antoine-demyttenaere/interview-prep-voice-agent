"""Migration 0008: create the auth_attempts table, used to slow down password guessing (Phase 4.6 step 4).

Each failed login and each registration attempt adds a row: what it was (kind), the email typed
and the caller's IP address, and when. app/auth/limits.py counts the recent rows before letting
a new attempt through. Rows older than a day are deleted as new ones arrive, so the table stays
small. The counts live in Postgres (not in the server's memory) so they still hold when several
copies of the server run side by side, as on AWS.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(20), nullable=False),  # login_failed | register
        sa.Column("email", sa.String(320)),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_auth_attempts_kind_email", "auth_attempts", ["kind", "email", "created_at"])
    op.create_index("ix_auth_attempts_kind_ip", "auth_attempts", ["kind", "ip", "created_at"])


def downgrade() -> None:
    op.drop_table("auth_attempts")
