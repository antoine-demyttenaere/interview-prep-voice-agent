"""Cuts a document's text into chunks: pieces of a few paragraphs that can be searched one by one.

Why chunks: an embedding captures the meaning of one piece of text. A whole CV as one embedding
is too blurry to match a precise question ("tell me about the outage"); a paragraph is not.

How: paragraphs (separated by blank lines) are added to the current chunk until it would pass
max_chars (about 200 words). A paragraph that is too long by itself is cut at sentence ends.
Each new chunk starts with the last `overlap` characters of the previous one, so a sentence
that falls on a boundary is still found in one piece.
"""

import re


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            pieces.append(para)
        else:
            pieces.extend(_split_long(para, max_chars))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > max_chars:
            chunks.append(current)
            tail = current[-overlap:]
            tail = tail[tail.find(" ") + 1:] if " " in tail else tail  # start on a word
            current = f"{tail} {piece}" if overlap else piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _split_long(para: str, max_chars: int) -> list[str]:
    """Cut one long paragraph at sentence ends (or hard, if a sentence is itself too long)."""
    out, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", para):
        while len(sentence) > max_chars:
            out.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if current and len(current) + 1 + len(sentence) > max_chars:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out
