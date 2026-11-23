"""Migration 0011: the practice, quotas and "Request more" (Phase 4.7 step 2).

- users.practice_quota (1) and users.interview_quota (2): how many of each a person may start in
  all. They never reset by themselves; scripts/manage_users.py grant adds more. Admins have no
  quota. Existing accounts get the defaults.
- sessions.kind: "practice" (a short session: 3 questions, feedback by Haiku) or "interview".
  Sessions made before this migration are interviews.
- access_requests: one row each time someone asks for more ("Request more" on the page), with
  their message; handled_at and what was granted are filled in by manage_users.py grant.
  Deleted with the account (CASCADE).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("practice_quota", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("users", sa.Column("interview_quota", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("sessions", sa.Column("kind", sa.String(20), nullable=False, server_default="interview"))
    op.create_table(
        "access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("handled_at", sa.DateTime(timezone=True)),
        sa.Column("practices_added", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("interviews_added", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("access_requests")
    op.drop_column("sessions", "kind")
    op.drop_column("users", "interview_quota")
    op.drop_column("users", "practice_quota")
