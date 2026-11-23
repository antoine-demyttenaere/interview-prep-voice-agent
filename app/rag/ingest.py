"""Ingestion: turns stored documents into searchable chunks (document -> chunks -> embeddings -> table).

ingest_documents(db, docs) cuts each document's text into chunks (chunking.py), embeds all the
chunks of all the documents in as few Voyage requests as possible (embeddings.py, as
"document"), and writes them to the chunks table with the document's interview_id, user_id, type and
source copied in, so a search can filter on them without a join. Running it again on the same
documents replaces their chunks, so it is safe to repeat. ingest_document(db, doc) does one.

The API calls it right after saving a document. If embedding fails (e.g. no Voyage key), the
document is still saved and the error is returned; scripts/embed_documents.py fills the gap later.
"""

from sqlalchemy import delete
from sqlalchemy.orm import Session as DB

from app.db.models import Chunk, Document
from app.rag import embeddings
from app.rag.chunking import chunk_text


def ingest_documents(db: DB, docs: list[Document]) -> dict:
    """Create (or recreate) the chunks of these documents. Returns {document id: chunk count}.

    Raises EmbeddingError, before anything is written, if the embedding provider fails.
    """
    pieces = [(doc, i, text) for doc in docs for i, text in enumerate(chunk_text(doc.raw_text or ""))]
    vectors = embeddings.get_embedder().embed([text for _, _, text in pieces], "document") if pieces else []

    db.execute(delete(Chunk).where(Chunk.document_id.in_([d.id for d in docs])))
    for (doc, i, text), vector in zip(pieces, vectors):
        db.add(Chunk(
            document_id=doc.id,
            interview_id=doc.interview_id,
            user_id=doc.user_id,
            chunk_index=i,
            text=text,
            embedding=vector,
            meta={"doc_type": doc.doc_type, "source": doc.source},
        ))
    db.commit()
    counts = {d.id: 0 for d in docs}
    for doc, _, _ in pieces:
        counts[doc.id] += 1
    return counts


def ingest_document(db: DB, doc: Document) -> int:
    """Create (or recreate) the chunks of one document. Returns how many. Raises EmbeddingError."""
    return ingest_documents(db, [doc])[doc.id]
