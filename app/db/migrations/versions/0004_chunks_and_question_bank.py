"""Migration 0004: create the chunks and question_bank tables, with vector search indexes.

- chunks: searchable pieces of documents, each with an embedding vector(1024).
- question_bank: seeded questions for the technical modes, also with an embedding.

The HNSW indexes ("hierarchical navigable small world", a graph of nearby vectors) let Postgres
find the closest vectors without comparing against every row. vector_cosine_ops means
"closest" is measured with cosine distance, the `<=>` operator used in app/rag/retrieve.py.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("interview_id", postgresql.UUID(as_uuid=True), index=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.execute("CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")

    op.create_table(
        "question_bank",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mode", sa.String(40), nullable=False, index=True),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("difficulty", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024)),
        sa.UniqueConstraint("mode", "text"),
    )
    op.execute(
        "CREATE INDEX question_bank_embedding_hnsw ON question_bank USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("question_bank")
    op.drop_table("chunks")
