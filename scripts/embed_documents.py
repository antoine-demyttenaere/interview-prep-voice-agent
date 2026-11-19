"""Chunks and embeds every document that has no chunks yet (e.g. pasted before Phase 3, or when
Voyage was unavailable at upload time). With --all, it redoes every document, which is useful
after changing the chunk size or the embedding model.

All the chunks are sent to Voyage together (128 per request), which matters on a free Voyage
account limited to 3 requests per minute.

    docker compose exec api python scripts/embed_documents.py
    docker compose exec api python scripts/embed_documents.py --all
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session as DB  # noqa: E402

from app.db.models import Chunk, Document  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.rag.embeddings import EmbeddingError  # noqa: E402
from app.rag.ingest import ingest_documents  # noqa: E402


def main() -> int:
    redo_all = "--all" in sys.argv
    with DB(get_engine()) as db:
        stmt = select(Document)
        if not redo_all:
            stmt = stmt.where(~select(Chunk.id).where(Chunk.document_id == Document.id).exists())
        docs = list(db.scalars(stmt))
        if not docs:
            print("Every document already has chunks.")
            return 0
        try:
            counts = ingest_documents(db, docs)
        except EmbeddingError as exc:
            print(f"Stopped, nothing written: {exc}")
            return 1
        for doc in docs:
            print(f"{doc.doc_type:16} {doc.source[:50]:50} {counts[doc.id]} chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
