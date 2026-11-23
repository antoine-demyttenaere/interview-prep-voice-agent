"""Migration 0006: every interview, document and chunk belongs to a user (Phase 4.6 step 2).

Adds a required user_id column (pointing at users.id) to interviews, documents and chunks.
Sessions need none: they belong to their interview. Deleting a user deletes their rows (CASCADE).

Existing rows (made before accounts existed) need an owner, so this migration gives them all
to the oldest admin account, or the oldest account when there is no admin. If there are rows but
no account at all, it stops with a message, because it can't guess who owns them. Then:
    docker compose run --rm api python scripts/create_user.py --email you@example.com --admin
    docker compose up -d        (runs this migration again)
(`run --rm` starts a one-off container with your command instead of the server, so it works
even while the server can't start.)

Steps: add the columns as optional, fill them in, then make them required. Chunks take the
user_id of their document.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = ("interviews", "documents", "chunks")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"),
        ))
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    conn = op.get_bind()
    has_rows = conn.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM interviews) OR EXISTS (SELECT 1 FROM documents)"
    )).scalar()
    if has_rows:
        owner = conn.execute(sa.text(
            "SELECT id FROM users ORDER BY is_admin DESC, created_at LIMIT 1"
        )).scalar()
        if owner is None:
            raise RuntimeError(
                "Your interviews and documents need an owner, but there is no account yet. Create yours with\n"
                "  docker compose run --rm api python scripts/create_user.py --email you@example.com --admin\n"
                "then start again with: docker compose up -d"
            )
        conn.execute(sa.text("UPDATE interviews SET user_id = :o WHERE user_id IS NULL"), {"o": owner})
        conn.execute(sa.text("UPDATE documents SET user_id = :o WHERE user_id IS NULL"), {"o": owner})
        conn.execute(sa.text(
            "UPDATE chunks SET user_id = documents.user_id FROM documents"
            " WHERE chunks.document_id = documents.id AND chunks.user_id IS NULL"
        ))

    for table in TABLES:
        op.alter_column(table, "user_id", nullable=False)


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(f"ix_{table}_user_id", table)
        op.drop_column(table, "user_id")
