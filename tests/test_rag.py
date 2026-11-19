"""Tests of the documents search (Phase 3): reading files, chunking, ingesting, searching, and
the graph using the results. The FakeEmbedder from conftest.py replaces Voyage, so they run
offline; the database is the real Postgres with pgvector.

- test_parsers_*: PDF, Word, .eml (plain and HTML) and text files become text.
- test_chunking_*: chunks stay small and no text is lost.
- test_upload_ingest_and_search: an uploaded file is chunked, embedded and found by meaning.
- test_ask_uses_and_records_passages: the interviewer's prompt gets the matching passages,
  and the transcript records which document the question drew on (what LangSmith also shows).
- test_question_bank_feeds_the_plan / test_system_design_allows_three_followups: the technical modes.

    docker compose exec api pytest -q tests/test_rag.py
"""

import io
import uuid

import docx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph import nodes
from app.graph.build import build_graph
from app.rag import retrieve
from app.rag.chunking import chunk_text
from app.rag.parsers import ParseError, parse_file
from conftest import BRIEF


# ---------- parsers ----------

def _tiny_pdf(text: str) -> bytes:
    """A valid one-page PDF with a text layer, built by hand (no PDF library needed)."""
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 400 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def test_parsers_pdf_docx_txt():
    assert "Kafka migration" in parse_file("cv.pdf", _tiny_pdf("Led the Kafka migration")).text

    buf = io.BytesIO()
    document = docx.Document()
    document.add_paragraph("Senior Backend Engineer")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Stack", "Python, Postgres"
    document.save(buf)
    text = parse_file("post.docx", buf.getvalue()).text
    assert "Senior Backend Engineer" in text and "Python, Postgres" in text

    assert parse_file("notes.txt", "Call with Julien\r\n\r\n\r\n\r\nOn-call".encode()).text == "Call with Julien\n\nOn-call"


def test_parsers_eml_plain_and_html():
    plain = (
        "From: Camille <camille@example.com>\r\nTo: me@example.com\r\nSubject: Next steps\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\nYour interview with Julien is on Tuesday.\r\n"
    ).encode()
    parsed = parse_file("invite.eml", plain)
    assert parsed.doc_type_guess == "recruiter_email"
    assert "Subject: Next steps" in parsed.text and "Julien is on Tuesday" in parsed.text

    html_mail = (
        "From: hr@example.com\r\nSubject: Offer\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        "<html><style>p{}</style><p>Salary &amp; benefits</p><br>See you</html>\r\n"
    ).encode()
    text = parse_file("offer.eml", html_mail).text
    assert "Salary & benefits" in text and "<p>" not in text and "p{}" not in text


def test_parsers_reject_unknown_and_empty():
    with pytest.raises(ParseError):
        parse_file("photo.png", b"\x89PNG")
    with pytest.raises(ParseError):
        parse_file("empty.txt", b"   ")


# ---------- chunking ----------

def test_chunking_keeps_chunks_small_and_loses_nothing():
    paragraphs = [f"Paragraph {i}. " + "word " * 60 for i in range(30)]
    chunks = chunk_text("\n\n".join(paragraphs), max_chars=500, overlap=80)
    assert len(chunks) > 5
    assert all(len(c) <= 500 + 80 + 2 for c in chunks)
    for i in range(30):
        assert any(f"Paragraph {i}." in c for c in chunks)


def test_chunking_splits_one_huge_paragraph():
    chunks = chunk_text("Sentence number one is here. " * 200, max_chars=300, overlap=0)
    assert len(chunks) > 10 and all(len(c) <= 300 for c in chunks)


# ---------- ingest + search through the API ----------

def _interview(client, **extra):
    return client.post("/interviews", json={"company": f"Fake {uuid.uuid4().hex[:6]}", "role": "Eng", **extra}).json()


def test_upload_ingest_and_search(fake):
    from app.main import app

    with TestClient(app) as client:
        iv = _interview(client)
        mail = b"From: hr@example.com\r\nSubject: Logistics\r\n\r\nThe team ships zebrafish telemetry pipelines."
        doc = client.post(
            "/documents/upload",
            files={"file": ("recruiter.eml", mail, "message/rfc822")},
            data={"interview_id": iv["id"]},
        ).json()
        assert doc["doc_type"] == "recruiter_email" and doc["chunks"] == 1
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post",
                                        "text": "We need strong Postgres indexing skills."})

        hits = retrieve.search_documents(iv["id"], "zebrafish telemetry", k=2)
        assert hits[0]["metadata"]["source"] == "recruiter.eml"
        assert hits[0]["metadata"]["doc_type"] == "recruiter_email"

        other = _interview(client)  # another interview never sees these documents
        assert all(h["metadata"]["source"] != "recruiter.eml" for h in retrieve.search_documents(other["id"], "zebrafish"))

        bad = client.post("/documents/upload", files={"file": ("x.png", b"\x89PNG", "image/png")},
                          data={"interview_id": iv["id"]})
        assert bad.status_code == 422

        listed = client.get(f"/interviews/{iv['id']}").json()["documents"]
        assert sorted(d["chunks"] for d in listed) == [1, 1]
        assert client.delete(f"/documents/{doc['id']}").status_code == 204


def test_ask_uses_and_records_passages(fake):
    """The check of Phase 3: each question shows which document it came from."""
    from app.main import app

    with TestClient(app) as client:
        iv = _interview(client)
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "source": "northwind_post.txt",
                                        "text": "Q1 needs someone who owned the platypus outage postmortem."})
        view = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2}).json()
        view = client.post(f"/sessions/{view['session_id']}/resume", json={"value": {"action": "approve"}}).json()

        first = view["transcript"][0]
        assert "northwind_post.txt" in first["sources"]
        assert "platypus outage postmortem" in fake.asks[-1]  # the passage reached the prompt


def test_question_bank_feeds_the_plan(fake, monkeypatch):
    seen = {}

    def bank(mode, query, k=8):
        seen.update(mode=mode, query=query)
        return [{"type": "Document", "page_content": "Design a parcel tracker.",
                 "metadata": {"source": "question_bank", "topic": "tracking", "difficulty": "hard"}}]

    monkeypatch.setattr(nodes.retrieve, "search_question_bank", bank)
    prompts_seen = []
    real = fake.structured
    monkeypatch.setattr(nodes, "structured", lambda **kw: (prompts_seen.append(kw["user"]), real(**kw))[1])

    graph = build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "bank"}}
    graph.invoke({"mode": "system_design", "company": "Fake", "role": "Eng", "documents": [],
                  "num_questions": 2, "brief": BRIEF.model_dump(), "brief_approved": True}, config)
    assert seen["mode"] == "system_design" and "ownership" in seen["query"]
    assert "[tracking, hard] Design a parcel tracker." in prompts_seen[0]


def test_system_design_allows_three_followups(fake, monkeypatch):
    monkeypatch.setattr(nodes.retrieve, "search_question_bank", lambda *a, **k: [])
    graph = build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "sd"}}
    graph.invoke({"mode": "system_design", "company": "Fake", "role": "Eng", "documents": [],
                  "num_questions": 1, "brief": BRIEF.model_dump(), "brief_approved": True}, config)
    for _ in range(4):  # Q1 + 3 follow-ups, all vague
        graph.invoke(Command(resume="vague"), config)
    values = graph.get_state(config).values
    assert [e["followup"] for e in values["evaluations"]] == [False, True, True, True]
    assert values["final_feedback"] is not None
