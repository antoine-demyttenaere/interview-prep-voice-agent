"""Migration 0001: enable the pgvector extension in Postgres.

pgvector adds a `vector` column type and similarity search. From Phase 3 it stores document
embeddings (lists of numbers that capture meaning) and finds the passages closest to a question.
A Postgres extension is switched on once per database; this migration does it.

Revision ID: 0001
Revises:
Create Date: 2026-11-19
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
