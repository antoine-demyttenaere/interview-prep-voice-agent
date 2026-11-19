"""Reads an uploaded file into plain text: PDF, Word (.docx), email (.eml), or text (.txt, .md).

parse_file(filename, data) picks the reader from the file extension and returns the text plus a
guessed document type (an .eml file is most likely a recruiter email). The text is what gets
stored in documents.raw_text, sent to Claude for the brief, and cut into chunks for search.

- PDF: pypdf extracts the text layer, page by page. A scanned PDF (only images) has no text
  layer, so it is rejected with a clear message (OCR is not supported).
- DOCX: python-docx reads the paragraphs and the tables.
- EML: Python's email library reads the headers (From, To, Date, Subject) and the body, plain
  text if the email has it, otherwise the HTML turned into text. Attachments are ignored.
- html_to_text(): turns HTML (an email body, or a web page from webpage.py) into readable
  text, skipping menus, scripts and footers.
"""

import email
import io
import re
from dataclasses import dataclass
from email import policy
from html.parser import HTMLParser
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
            body = html_to_text(body)[1]
    return "\n".join(headers) + "\n\n" + body


# ---------- HTML (emails and web pages) ----------

class _TextExtractor(HTMLParser):
    """Collects the readable text of an HTML page, skipping menus, scripts, styles and forms.

    Block elements (paragraphs, headings, list items...) become line breaks; list items get a
    "- " in front. Text inside <main> or <article> is also kept apart: on a web page that is
    usually the content itself, without the site's menus and footer.
    """

    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "aside", "form", "button", "iframe", "template", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "main", "header", "ul", "ol", "table",
             "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.main = 0
        self.in_title = False
        self.title = ""
        self.parts: list[str] = []
        self.main_parts: list[str] = []

    def _add(self, text: str) -> None:
        self.parts.append(text)
        if self.main:
            self.main_parts.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in self.SKIP:
            self.skip += 1
        if tag in ("main", "article"):
            self.main += 1
        if tag in self.BLOCK and not self.skip:
            self._add("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in ("main", "article") and self.main:
            self.main -= 1
        if tag in self.BLOCK and not self.skip:
            self._add("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self._add(data)


def html_to_text(page: str) -> tuple[str, str]:
    """HTML -> (title, readable text). Prefers the <main>/<article> text when there is enough of it."""
    parser = _TextExtractor()
    parser.feed(page)
    main_text = "".join(parser.main_parts)
    raw = main_text if len(main_text.strip()) >= 500 else "".join(parser.parts)
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
    text = "\n".join(line for line in lines if line and line != "-")
    return re.sub(r"\s+", " ", parser.title).strip(), text
