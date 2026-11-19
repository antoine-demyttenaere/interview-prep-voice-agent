"""Reads an uploaded file into plain text: PDF, Word (.docx), email (.eml), or text (.txt, .md).

parse_file(filename, data) picks the reader from the file extension and returns the text plus a
guessed document type (an .eml file is most likely a recruiter email). The text is what gets
stored in documents.raw_text, sent to Claude for the brief, and cut into chunks for search.

- PDF: pypdf extracts the text layer, page by page. A scanned PDF (only images) has no text
  layer, so it is rejected with a clear message (OCR is not supported).
- DOCX: python-docx reads the paragraphs and the tables.
- EML: Python's email library reads the headers (From, To, Date, Subject) and the body, plain
  text if the email has it, otherwise the HTML with its tags removed. Attachments are ignored.
"""

import email
import html
import io
import re
from dataclasses import dataclass
from email import policy
from pathlib import Path

SUPPORTED = (".pdf", ".docx", ".eml", ".txt", ".md")


class ParseError(ValueError):
    """The file could not be read as text."""


@dataclass
class ParsedFile:
    text: str
    doc_type_guess: str  # recruiter_email for .eml, otherwise "other"


def parse_file(filename: str, data: bytes) -> ParsedFile:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        text = _pdf(data)
    elif ext == ".docx":
        text = _docx(data)
    elif ext == ".eml":
        return ParsedFile(text=_clean(_eml(data)), doc_type_guess="recruiter_email")
    elif ext in (".txt", ".md"):
        text = data.decode("utf-8", errors="replace")
    else:
        raise ParseError(f"Unsupported file type '{ext}'. Supported: {', '.join(SUPPORTED)}")
    text = _clean(text)
    if not text:
        raise ParseError(f"No text found in {filename}. Is it a scanned document (images only)?")
    return ParsedFile(text=text, doc_type_guess="other")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # pypdf raises many error types for broken files
        raise ParseError(f"Could not read the PDF: {exc}") from exc


def _docx(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"Could not read the Word file: {exc}") from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def _eml(data: bytes) -> str:
    msg = email.message_from_bytes(data, policy=policy.default)
    headers = [f"{name}: {msg[name]}" for name in ("From", "To", "Date", "Subject") if msg[name]]
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = re.sub(r"<(script|style).*?</\1>", "", body, flags=re.DOTALL | re.IGNORECASE)
            body = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", body, flags=re.IGNORECASE)
            body = html.unescape(re.sub(r"<[^>]+>", "", body))
    return "\n".join(headers) + "\n\n" + body
