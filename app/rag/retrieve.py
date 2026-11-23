"""Retrieval: finds the chunks (or question-bank questions) closest in meaning to a search text.

- search_documents(interview_id, query): this interview's documents plus its owner's CV. The
  owner is looked up from the interview row (a subquery), so a search can never reach another
  user's chunks, even though the graph only knows the interview id.
- search_question_bank(mode, query): seeded questions for the technical modes.

Both embed the query (as "query"), then ask Postgres for the rows with the smallest cosine
distance (pgvector's <=> operator, written `.cosine_distance()` in SQLAlchemy). The HNSW index
from migration 0004 makes that fast. similarity = 1 - distance: 1 means same meaning.

They are @traceable with run_type="retriever", so LangSmith shows each search as a list of
documents with their source: that is how a trace shows which document a question came from.
If embedding fails, the search returns nothing and logs a warning: the interview goes on
without the extra context instead of stopping.
"""

import logging
import uuid

from langsmith import traceable
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DB

from app.db.models import Chunk, Interview, QuestionBankItem
from app.db.session import get_engine
from app.rag import embeddings

log = logging.getLogger(__name__)


def _query_vector(query: str) -> list[float] | None:
    try:
        return embeddings.get_embedder().embed([query], "query")[0]
    except embeddings.EmbeddingError as exc:
        log.warning("Retrieval skipped: %s", exc)
        return None


@traceable(run_type="retriever", name="search_documents")
def search_documents(interview_id: str, query: str, k: int = 3) -> list[dict]:
    """The k chunks of this interview's documents (and its owner's CV) closest to the query."""
    vector = _query_vector(query)
    if vector is None:
        return []
    interview_id = uuid.UUID(str(interview_id))
    owner = select(Interview.user_id).where(Interview.id == interview_id).scalar_subquery()
    distance = Chunk.embedding.cosine_distance(vector).label("distance")
    stmt = (
        select(Chunk, distance)
        .where(Chunk.user_id == owner,
               or_(Chunk.interview_id == interview_id, Chunk.interview_id.is_(None)))
        .order_by(distance)
        .limit(k)
    )
    with DB(get_engine()) as db:
        rows = db.execute(stmt).all()
    return [
        {
            "type": "Document",  # the shape LangSmith displays nicely for retriever runs
            "page_content": chunk.text,
            "metadata": {**chunk.meta, "chunk_index": chunk.chunk_index, "similarity": round(1 - dist, 3)},
        }
        for chunk, dist in rows
    ]


@traceable(run_type="retriever", name="search_question_bank")
def search_question_bank(mode: str, query: str, k: int = 8) -> list[dict]:
    """The k seeded questions of this mode closest to the query (empty if the bank is not seeded)."""
    vector = _query_vector(query)
    if vector is None:
        return []
    distance = QuestionBankItem.embedding.cosine_distance(vector).label("distance")
    stmt = (
        select(QuestionBankItem, distance)
        .where(QuestionBankItem.mode == mode, QuestionBankItem.embedding.is_not(None))
        .order_by(distance)
        .limit(k)
    )
    with DB(get_engine()) as db:
        rows = db.execute(stmt).all()
    return [
        {
            "type": "Document",
            "page_content": q.text,
            "metadata": {
                "source": "question_bank",
                "topic": q.topic,
                "difficulty": q.difficulty,
                "similarity": round(1 - dist, 3),
            },
        }
        for q, dist in rows
    ]
