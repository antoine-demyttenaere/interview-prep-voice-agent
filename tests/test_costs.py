"""Tests of cost control (Phase 4.5): prices, usage recording, budgets and limits.

The budgets and limits are set very high for every other test (conftest `generous_limits`);
each test here lowers the one it checks with monkeypatch. They check that:
- prices: Claude's four kinds of tokens, Haiku's long-prompt prices, an unknown model (never
  free), and Deepgram / ElevenLabs / Voyage per minute / thousand characters / million tokens;
- recording: every Claude response written by app/llm.py lands in the usage table, and a call
  made inside a graph node is recorded for the right user and session (the ContextVar reaches
  LangGraph's threads); usage rows outlive a deleted account, so the month's spend stays right;
- budgets: when the month's budget is used up, a running interview still ends with feedback,
  but a new one is refused (503) - the check from plan.md;
- per user: the interviews-per-day limit counts interviews whose questions were planned (not
  briefs), and stops the next one at approval (429);
- per session and input: brief revisions, questions per session, documents, characters, web
  pages and CV size are capped; recordings over the size cap are refused before speech-to-text;
- speech: a replayed question is not synthesized again, and a question over the spoken length
  is shown as text only;
- GET /usage: everyone sees their own spend; only an admin sees the budgets, with the 80% warning.

    docker compose exec api pytest -q tests/test_costs.py
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DB

from app import llm
from app.api import interviews as interviews_api
from app.auth.deps import current_user
from app.config import get_settings
from app.costs import pricing, usage
from app.db.models import Usage, User
from app.db.session import get_engine
from app.graph import nodes
from app.main import app
from test_voice_turn import _record, _session_waiting_for_answer, speech  # noqa: F401 (speech is a fixture)


def _user(**extra) -> User:
    with DB(get_engine(), expire_on_commit=False) as db:
        user = User(email=f"c-{uuid.uuid4().hex[:8]}@example.com", **extra)
        db.add(user)
        db.commit()
        return user


def act_as(user: User) -> None:
    app.dependency_overrides[current_user] = lambda: user


def _add_usage(cost: float, provider="claude", user_id=None) -> None:
    with DB(get_engine()) as db:
        db.add(Usage(provider=provider, model="m", unit="tokens", quantity=1, cost_usd=cost, user_id=user_id))
        db.commit()


def _rows() -> list[Usage]:
    with DB(get_engine()) as db:
        return db.scalars(select(Usage).order_by(Usage.id)).all()


def _interview_with_doc(client) -> dict:
    iv = client.post("/interviews", json={"company": f"Co {uuid.uuid4().hex[:6]}", "role": "Eng"}).json()
    client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python role"})
    return iv


# ---------- prices ----------

def test_prices():
    one_million_each = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert pricing.claude_cost("claude-sonnet-5-5", one_million_each) == pytest.approx(12.0)
    assert pricing.claude_cost("claude-haiku-5-5", {"input_tokens": 10_000, "output_tokens": 1_000,
                                                     "cache_read_input_tokens": 20_000,
                                                     "cache_creation_input_tokens": 4_000}) == pytest.approx(
        (10_000 * 0.10 + 1_000 * 0.50 + 20_000 * 0.01 + 4_000 * 0.125) / 1e6)
    long = {"input_tokens": 150_000, "output_tokens": 0}  # over 100K: Haiku's long-prompt price
    assert pricing.claude_cost("claude-haiku-5-5", long) == pytest.approx(150_000 * 0.50 / 1e6)
    assert pricing.claude_cost("claude-mystery-9", {"input_tokens": 1_000_000}) == pytest.approx(4.0)  # never free
    assert pricing.unit_cost("deepgram", "nova-3", 60) == pytest.approx(0.0043)
    assert pricing.unit_cost("elevenlabs", "eleven_flash_v2_5", 1_000) == pytest.approx(0.05)
    assert pricing.unit_cost("voyage", "voyage-4", 1_000_000) == pytest.approx(0.06)


# ---------- recording ----------

def test_every_claude_response_is_recorded():
    response = SimpleNamespace(model="claude-haiku-5-5", stop_reason="end_turn", usage=SimpleNamespace(
        input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0, cache_read_input_tokens=5000))
    with usage.attribute(None, None):
        llm._record(response, started=0.0)
    row = _rows()[-1]
    assert (row.provider, row.model, row.quantity) == ("claude", "claude-haiku-5-5", 6200)
    assert float(row.cost_usd) == pytest.approx((1000 * 0.10 + 200 * 0.50 + 5000 * 0.01) / 1e6, abs=1e-6)
    assert row.details["cache_read_input_tokens"] == 5000 and row.user_id is None


def test_calls_inside_the_graph_are_recorded_for_the_right_user_and_session(fake, monkeypatch, test_user):
    real = nodes.structured

    def structured_that_pays(**kw):  # like app/llm.py: record, then answer
        usage.record_claude("claude-sonnet-5-5", {"input_tokens": 100, "output_tokens": 10})
        return real(**kw)

    monkeypatch.setattr(nodes, "structured", structured_that_pays)
    with TestClient(app) as client:
        sid = client.post(f"/interviews/{_interview_with_doc(client)['id']}/sessions", json={}).json()["session_id"]
    rows = _rows()
    assert rows and all(r.user_id == test_user.id and str(r.session_id) == sid for r in rows)


def test_usage_outlives_a_deleted_account():
    user = _user()
    _add_usage(0.5, user_id=user.id)
    with DB(get_engine()) as db:
        db.execute(delete(User).where(User.id == user.id))
        db.commit()
    row = _rows()[-1]
    assert row.user_id is None and float(row.cost_usd) == 0.5  # still counted in the month


# ---------- budgets ----------

def test_over_budget_a_running_interview_finishes_but_a_new_one_is_refused(fake, monkeypatch):
    with TestClient(app) as client:
        iv = _interview_with_doc(client)
        sid = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 3}).json()["session_id"]
        client.post(f"/sessions/{sid}/resume", json={"value": {"action": "approve"}})

        monkeypatch.setattr(get_settings(), "budget_monthly_usd", 0.01)
        _add_usage(0.02)  # the month's budget is now used up

        assert client.post(f"/sessions/{sid}/resume", json={"value": "good"}).status_code == 200
        done = client.post(f"/sessions/{sid}/end").json()
        assert done["status"] == "done" and done["final_feedback"]

        refused = client.post(f"/interviews/{iv['id']}/sessions", json={})
        assert refused.status_code == 503 and "next month" in refused.json()["detail"]


def test_daily_budget_resets_tomorrow(fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "budget_daily_usd", 0.01)
    _add_usage(0.02)
    with TestClient(app) as client:
        refused = client.post(f"/interviews/{_interview_with_doc(client)['id']}/sessions", json={})
        assert refused.status_code == 503 and "tomorrow" in refused.json()["detail"]


def test_interviews_per_day_count_started_interviews_not_briefs(fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "interviews_per_day", 1)
    act_as(_user())
    with TestClient(app) as client:
        first = client.post(f"/interviews/{_interview_with_doc(client)['id']}/sessions", json={}).json()["session_id"]
        assert client.post(f"/sessions/{first}/resume", json={"value": {"action": "approve"}}).status_code == 200

        second = client.post(f"/interviews/{_interview_with_doc(client)['id']}/sessions", json={})
        assert second.status_code == 201  # writing a brief is still fine
        sid = second.json()["session_id"]
        blocked = client.post(f"/sessions/{sid}/resume", json={"value": {"action": "approve"}})
        assert blocked.status_code == 429 and "tomorrow" in blocked.json()["detail"]
        assert client.get(f"/sessions/{sid}").json()["pending"]["type"] == "review_brief"  # nothing lost
        assert client.get("/usage").json()["you"]["interviews_today"] == 1


# ---------- per session and per input ----------

def test_brief_revisions_and_questions_are_capped(fake, monkeypatch):
    monkeypatch.setattr(get_settings(), "brief_revisions_max", 2)
    with TestClient(app) as client:
        iv = _interview_with_doc(client)
        assert client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 9}).status_code == 422
        sid = client.post(f"/interviews/{iv['id']}/sessions", json={}).json()["session_id"]
        revise = {"value": {"action": "revise", "feedback": "more"}}
        assert [client.post(f"/sessions/{sid}/resume", json=revise).status_code for _ in range(3)] == [200, 200, 429]
        assert client.post(f"/sessions/{sid}/resume", json={"value": {"action": "approve"}}).status_code == 200


def test_document_limits(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "max_documents_per_interview", 2)
    monkeypatch.setattr(s, "max_document_chars_per_interview", 100)
    monkeypatch.setattr(s, "max_web_pages_per_interview", 1)
    monkeypatch.setattr(s, "max_cv_chars", 20)
    monkeypatch.setattr(interviews_api, "fetch_page", lambda url: ("Page", "careers text"))
    with TestClient(app) as client:
        iv = client.post("/interviews", json={"company": "Co", "role": "Eng"}).json()
        add = lambda text: client.post("/documents", json={"interview_id": iv["id"], "doc_type": "notes", "text": text})
        assert add("x" * 101).status_code == 422  # too many characters
        assert add("short notes").status_code == 201
        assert client.post("/documents/from-url", json={"interview_id": iv["id"], "url": "https://co.example/jobs"}
                           ).status_code == 201
        assert add("more").status_code == 422  # 2 documents already
        assert client.post("/documents", json={"doc_type": "cv", "text": "c" * 21}).status_code == 422

        iv2 = client.post("/interviews", json={"company": "Co2", "role": "Eng"}).json()
        url = {"interview_id": iv2["id"], "url": "https://co.example/about"}
        assert client.post("/documents/from-url", json=url).status_code == 201
        assert client.post("/documents/from-url", json=url).status_code == 422  # 1 web page at most


def test_recordings_over_the_size_cap_are_refused_before_speech_to_text(fake, speech, monkeypatch):
    stt, _ = speech
    monkeypatch.setattr(get_settings(), "max_recording_bytes", 5)
    with TestClient(app) as client:
        sid = _session_waiting_for_answer(client)
        assert _record(client, sid).status_code == 413  # "fake-audio" is 10 bytes
    assert stt.languages == []  # Deepgram was never called


# ---------- speech ----------

def test_replays_are_free_and_long_questions_are_text_only(fake, speech, monkeypatch):
    _, tts = speech
    with TestClient(app) as client:
        sid = _session_waiting_for_answer(client)
        first = client.post(f"/sessions/{sid}/speak")
        again = client.post(f"/sessions/{sid}/speak")
        assert first.status_code == again.status_code == 200 and first.content == again.content
        assert len(tts.said) == 1  # the replay came from the question_audio table

        monkeypatch.setattr(get_settings(), "max_spoken_chars", 3)
        assert client.post(f"/sessions/{sid}/speak").status_code == 422
        turn = _record(client, sid).json()  # the next question is too long to speak, too
        assert turn["audio"] is None and "text only" in turn["audio_error"]
        assert len(tts.said) == 1


# ---------- GET /usage ----------

def test_usage_endpoint(monkeypatch):
    user = _user()
    _add_usage(0.25, user_id=user.id)
    _add_usage(0.50)  # someone else's
    with TestClient(app) as client:
        act_as(user)
        mine = client.get("/usage").json()
        assert mine["you"]["month_usd"] == pytest.approx(0.25) and mine["budget"] is None
        assert mine["limits"]["max_recording_seconds"] == get_settings().max_recording_seconds

        monkeypatch.setattr(get_settings(), "budget_monthly_usd", 0.9)
        act_as(_user(is_admin=True))
        admin = client.get("/usage").json()["budget"]
        month = next(b for b in admin["budgets"] if b["name"] == "month")
        assert month["spent"] == pytest.approx(0.75) and admin["warning"] and admin["exceeded"] == []
