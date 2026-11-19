"""Reads a web page from a link (a job post, a company's "about" page) into plain text.

fetch_page(url) downloads the page with httpx and turns its HTML into readable text with
parsers.html_to_text(); a link to a PDF is read like an uploaded PDF. The API stores the result
as a document of the interview (source = the link), which is then chunked and embedded like
any other document.

Limits: text beyond 30,000 characters is cut (a job post is far shorter; a whole long page
would make the brief slow and costly). The page is read as the server receives it, without running JavaScript and without
being logged in. Sites that build the page with JavaScript or need a login (LinkedIn, for
example) return almost no text: fetch_page then raises ParseError with a message asking to
paste the text instead.
"""

import httpx

from app.rag.parsers import ParseError, html_to_text, parse_file

MAX_BYTES = 5_000_000
MIN_CHARS = 200  # less than this is a menu or a "please enable JavaScript" page, not content
MAX_CHARS = 30_000  # a job post is 3-8K characters; more is a long page that would slow the brief
HEADERS = {
    # A generic browser-like identity; many sites refuse requests without one.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "en,fr;q=0.8,es;q=0.7",
}


def fetch_page(url: str) -> tuple[str, str]:
    """Download a page and return (title, text). Raises ParseError with a readable reason."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ParseError(f"'{url}' is not a link: it must start with http:// or https://")
    try:
        resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=20.0)
    except httpx.HTTPError as exc:
        raise ParseError(f"Could not open {url}: {exc}") from exc
    if resp.status_code != 200:
        raise ParseError(f"{url} answered {resp.status_code}: the page may need a login. Paste its text instead.")
    if len(resp.content) > MAX_BYTES:
        raise ParseError(f"{url} is too large to read (over {MAX_BYTES // 1_000_000} MB).")

    content_type = resp.headers.get("content-type", "").lower()
    if "pdf" in content_type:
        return url.rsplit("/", 1)[-1], parse_file("page.pdf", resp.content).text
    title, text = html_to_text(resp.text)
    if len(text) < MIN_CHARS:
        raise ParseError(
            f"{url} has almost no readable text: the site probably needs JavaScript or a login "
            "(LinkedIn often does). Paste the job post text instead."
        )
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[... the rest of the page was cut: it was very long]"
    return title, text
