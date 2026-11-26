"""Tests of the interview flow with Claude replaced by a fake: free, fast, no API key needed.

FakeClaude (tests/conftest.py) stands in for app.llm.structured / text_reply (monkeypatch swaps
them inside nodes.py) and returns fixed answers; it asks for a follow-up whenever an answer contains
"vague". The real prompt files are still rendered, so a broken placeholder fails here.

- test_full_session_with_revision_and_followups: revise then approve the brief, follow-ups
  capped at 2, every question asked, final feedback written.
- test_approved_brief_skips_prepare: a new session reuses an already-approved brief.
- test_api_session_survives_restart: through the HTTP API and the real Postgres checkpointer;
  a fresh app instance resumes the session where it stopped.
- test_language_reaches_the_prompts: a French interview asks Claude for French.
- test_end_early_* / test_end_endpoint: ending early gives feedback on the answers so far.
- test_back_to_a_draft_brief_is_instant: a brief not approved yet is kept as a draft, and
  going back to it (review_brief) makes no Claude call; changing the interview clears it.

    docker compose exec api pytest -q
"""

import uuid

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph.build import build_graph
from conftest import BRIEF


def _initial(**extra):
    return {
        "mode": "behavioral",
        "company": "Fake Co",
        "role": "Engineer",
        "documents": [{"doc_type": "job_post", "source": "t", "text": "Python role"}],
        "num_questions": 3,
        "brief": None,
        "brief_approved": False,
        **extra,
    }


def _pending(graph, config):
    tasks = graph.get_state(config).tasks
    return next((i.value for t in tasks for i in t.interrupts), None)


def test_full_session_with_revision_and_followups(fake):
    graph = build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    graph.invoke(_initial(), config)
    assert _pending(graph, config)["type"] == "review_brief"

    graph.invoke(Command(resume={"action": "revise", "feedback": "more detail"}), config)
    assert _pending(graph, config)["type"] == "review_brief"
    assert fake.calls.count("InterviewBrief") == 2

    graph.invoke(Command(resume={"action": "approve"}), config)
    answers = ["vague", "vague", "vague", "good", "vague", "good"]
    #          Q1       fu1      fu2      Q2      Q3       fu1
    # (after fu2 the cap of 2 follow-ups is reached, so it moves on to Q2 despite "vague")
    for a in answers:
        p = _pending(graph, config)
        assert p and p["type"] == "answer", p
        graph.invoke(Command(resume=a), config)

    values = graph.get_state(config).values
    assert values["final_feedback"]["overall_score"] == 3
    assert [e["q_index"] for e in values["evaluations"]] == [0, 0, 0, 1, 2, 2]
    assert [e["followup"] for e in values["evaluations"]] == [False, True, True, False, False, True]
    assert len(values["transcript"]) == 12  # 6 questions asked, 6 answers
    assert _pending(graph, config) is None


def test_approved_brief_skips_prepare(fake):
    graph = build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t2"}}
    graph.invoke(_initial(brief=BRIEF.model_dump(), brief_approved=True), config)
    assert "InterviewBrief" not in fake.calls
    assert _pending(graph, config)["type"] == "answer"


def test_api_session_survives_restart(fake):
    """Through the API and the Postgres checkpointer; a new app instance resumes the session."""
    from app.main import app

    with TestClient(app) as client:
        iv = client.post("/interviews", json={"company": f"Fake {uuid.uuid4().hex[:6]}", "role": "Eng"}).json()
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python"})
        view = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2}).json()
        assert view["pending"]["type"] == "review_brief"

        bad = client.post(f"/sessions/{view['session_id']}/resume", json={"value": "hello"})
        assert bad.status_code == 422
        view = client.post(f"/sessions/{view['session_id']}/resume", json={"value": {"action": "approve"}}).json()
        assert view["pending"]["type"] == "answer"

    with TestClient(app) as client:  # fresh lifespan = fresh checkpointer connection
        sid = view["session_id"]
        assert client.get(f"/sessions/{sid}").json()["pending"]["type"] == "answer"
        for _ in range(2):
            view = client.post(f"/sessions/{sid}/resume", json={"value": "good"}).json()
        assert view["status"] == "done"
        assert client.get(f"/interviews/{iv['id']}").json()["brief_status"] == "approved"


def test_language_reaches_the_prompts(fake):
    """A French interview tells Claude to write the brief and run the interview in French."""
    graph = build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t3"}}
    graph.invoke(_initial(language="fr"), config)  # prepare_brief
    graph.invoke(Command(resume={"action": "approve"}), config)  # plan_questions
    assert "write the brief in French" in fake.systems[0]
    assert "The interview is in French" in fake.systems[1]



def test_end_early_gives_feedback_on_answers_so_far(fake):
    """Ending skips the remaining questions and goes straight to the final feedback."""
    graph = build_graph().compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t4"}}
    graph.invoke(_initial(brief=BRIEF.model_dump(), brief_approved=True), config)
    graph.invoke(Command(resume="good"), config)  # Q1 answered, Q2 is asked
    graph.invoke(Command(resume={"end": True}), config)  # end instead of answering Q2

    values = graph.get_state(config).values
    assert values["ended_early"] is True
    assert values["final_feedback"] is not None
    assert len(values["evaluations"]) == 1
    assert values["transcript"][-1]["role"] == "candidate"  # the unanswered Q2 was dropped
    assert _pending(graph, config) is None


def test_end_endpoint(fake):
    """POST /end: refused before any answer, then gives the feedback."""
    from app.main import app

    with TestClient(app) as client:
        iv = client.post("/interviews", json={"company": f"Fake {uuid.uuid4().hex[:6]}", "role": "Eng"}).json()
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python"})
        sid = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 3}).json()["session_id"]
        assert client.post(f"/sessions/{sid}/end").status_code == 409  # still at the brief review
        client.post(f"/sessions/{sid}/resume", json={"value": {"action": "approve"}})
        assert client.post(f"/sessions/{sid}/end").status_code == 409  # no answer yet
        client.post(f"/sessions/{sid}/resume", json={"value": "good"})
        view = client.post(f"/sessions/{sid}/end").json()
        assert view["status"] == "done" and view["ended_early"] is True


def test_duration_and_start_time_reach_the_view(fake):
    """The countdown needs the chosen duration and the moment the questions were planned."""
    from app.main import app

    with TestClient(app) as client:
        iv = client.post("/interviews", json={"company": f"Fake {uuid.uuid4().hex[:6]}", "role": "Eng"}).json()
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python"})
        view = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2, "duration_min": 45}).json()
        assert view["duration_min"] == 45 and view["interview_started_at"] is None  # still at the brief
        view = client.post(f"/sessions/{view['session_id']}/resume", json={"value": {"action": "approve"}}).json()
        assert view["duration_min"] == 45 and view["interview_started_at"]  # countdown can start
        assert client.post(f"/interviews/{iv['id']}/sessions", json={"duration_min": 5}).status_code == 422


def test_back_to_the_brief_from_an_interview(fake):
    """A new session with review_brief starts at the review of the same brief, without rewriting it."""
    from app.main import app

    with TestClient(app) as client:
        iv = client.post("/interviews", json={"company": f"Fake {uuid.uuid4().hex[:6]}", "role": "Eng"}).json()
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python"})
        first = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2}).json()
        client.post(f"/sessions/{first['session_id']}/resume", json={"value": {"action": "approve"}})
        client.post(f"/sessions/{first['session_id']}/resume", json={"value": "good"})  # mid-interview
        briefs_written = fake.calls.count("InterviewBrief")

        again = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2, "review_brief": True}).json()
        assert again["pending"]["type"] == "review_brief"
        assert again["pending"]["brief"] == BRIEF.model_dump()
        assert fake.calls.count("InterviewBrief") == briefs_written  # not written again
        view = client.post(f"/sessions/{again['session_id']}/resume", json={"value": {"action": "approve"}}).json()
        assert view["pending"]["type"] == "answer" and view["pending"]["q_index"] == 0  # fresh questions
        # the first session is untouched and can still be continued
        assert client.get(f"/sessions/{first['session_id']}").json()["pending"]["q_index"] == 1


def test_back_to_a_draft_brief_is_instant(fake):
    """Going back to a brief that was written but not approved reuses it: no new Claude call."""
    from app.main import app

    with TestClient(app) as client:
        iv = client.post("/interviews", json={"company": f"Fake {uuid.uuid4().hex[:6]}", "role": "Eng"}).json()
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python"})
        view = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2}).json()
        assert view["pending"]["type"] == "review_brief" and fake.calls.count("InterviewBrief") == 1
        assert client.get(f"/interviews/{iv['id']}").json()["brief_status"] == "draft"

        calls = len(fake.calls)
        again = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 3, "review_brief": True}).json()
        assert again["pending"]["type"] == "review_brief" and again["session_id"] != view["session_id"]
        assert again["pending"]["brief"] == view["pending"]["brief"]
        assert len(fake.calls) == calls  # no Claude call at all

        client.patch(f"/interviews/{iv['id']}", json={"role": "Senior Eng"})  # the brief must be redone
        assert client.get(f"/interviews/{iv['id']}").json()["brief_status"] == "none"
