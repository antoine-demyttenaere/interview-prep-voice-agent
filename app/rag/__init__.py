"""RAG layer ("retrieval-augmented generation"): find the right passages of your documents for each question.

Instead of sending every document to Claude on every turn, documents are cut into small pieces
(chunks), each turned into an embedding (1024 numbers that capture its meaning) and stored in
Postgres with pgvector. When the interviewer is about to ask a question, the question is
embedded too, and the closest chunks are fetched and added to the prompt.

- parsers.py: reads PDF, DOCX, .eml emails and plain text into text.
- chunking.py: cuts a text into overlapping chunks of a few paragraphs.
- embeddings.py: the embedding interface + Voyage AI.
- ingest.py: document -> chunks -> embeddings -> the chunks table.
- retrieve.py: question -> closest chunks (and closest question-bank questions).
"""
