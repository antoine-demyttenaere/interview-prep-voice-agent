"""Tests of the practice, the quotas and "Request more" (Phase 4.7 step 2).

Each test uses fresh accounts (default quotas: 1 practice, 2 interviews), unlike the shared test
account, which has an endless interview quota (tests/conftest.py). act_as() switches which
account current_user returns. They check that:
- a new account's first session is the practice: 3 questions whatever was asked, Haiku for the
  feedback; the next session reuses its documents and approved brief, is an interview, and gets
  Sonnet feedback;
- after the practice and 2 interviews, a new session is refused (429) before any Claude call,
  and GET /usage says nothing is left;
- "Request more" works only once nothing is left, once at a time, and emails the admins; a grant
  adds interviews, closes the request and emails the person, and the next session works;
- a practice whose brief is approved after the practice was used by another session is refused;
- the practice budget stops new practices but not interviews;
- an admin has no quota and never gets a practice.

    docker compose exec api pytest -q tests/test_quotas.py
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.api.usage import grant_more
from app.auth.deps import current_user
from app.config import get_settings
from app.db.models import AccessRequest, Session, Usage, User
from app.db.session import get_engine
from app.main import app


def new_user(**extra) -> User:
    with DB(get_engine(), expire_on_commit=False) as db:
        user = User(email=f"q-{uuid.uuid4().hex[:8]}@example.com", **extra)
        db.add(user)
        db.commit()
        return user


def act_as(user: User) -> None:
    app.dependency_overrides[current_user] = lambda: user


def _interview(client) -> dict:
    iv = client.post("/interviews", json={"company": f"Co {uuid.uuid4().hex[:6]}", "role": "Eng"}).json()
    client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python role"})
    client.post("/documents", json={"doc_type": "cv", "text": "My CV"})
    return iv


def _state(sid: str) -> dict:
    return app.state.graph.get_state({"configurable": {"thread_id": sid}}).values


def _finish(client, view: dict) -> dict:
    """Approve the brief if asked, then answer every question: returns the final view."""
    sid = view["session_id"]
    if view["pending"] and view["pending"]["type"] == "review_brief":
        view = client.post(f"/sessions/{sid}/resume", json={"value": {"action": "approve"}}).json()
    while view["status"] == "waiting":
        view = client.post(f"/sessions/{sid}/resume", json={"value": "good answer"}).json()
    return view


def test_first_session_is_a_short_practice_then_interviews_reuse_its_documents(fake):
    act_as(new_user())
    s = get_settings()
    with TestClient(app) as client:
        assert client.get("/usage").json()["you"]["next_session"] == "practice"
        iv = _interview(client)
        first = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 6}).json()
        assert first["kind"] == "practice"
        done = _finish(client, first)
        assert done["status"] == "done"
        assert _state(first["session_id"])["num_questions"] == s.practice_questions  # 3, not 6
        assert fake.models["FinalFeedback"] == s.model_turn  # Haiku

        you = client.get("/usage").json()["you"]
        assert (you["practice_left"], you["interviews_left"], you["next_session"]) == (0, 2, "interview")

        briefs = fake.calls.count("InterviewBrief")
        second = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 5}).json()
        assert second["kind"] == "interview" and second["pending"]["type"] == "answer"  # brief reused
        assert fake.calls.count("InterviewBrief") == briefs  # no new brief, no new upload
        _finish(client, second)
        assert _state(second["session_id"])["num_questions"] == 5
        assert fake.models["FinalFeedback"] == s.model_reasoning  # Sonnet


def test_nothing_left_then_request_more_then_grant(fake, mailbox):
    user = new_user()
    admin = new_user(is_admin=True)
    act_as(user)
    with TestClient(app) as client:
        iv = _interview(client)
        assert client.post("/access-requests", json={}).status_code == 409  # still has sessions
        for _ in range(3):  # the practice, then 2 interviews
            _finish(client, client.post(f"/interviews/{iv['id']}/sessions", json={}).json())

        briefs = fake.calls.count("InterviewBrief")
        refused = client.post(f"/interviews/{iv['id']}/sessions", json={})
        assert refused.status_code == 429 and "Request more" in refused.json()["detail"]
        assert fake.calls.count("InterviewBrief") == briefs  # refused before any Claude call
        you = client.get("/usage").json()["you"]
        assert (you["practice_left"], you["interviews_left"]) == (0, 0)

        assert client.post("/access-requests", json={"message": "Big interview on Friday"}).status_code == 201
        to_admin = [m for m in mailbox.sent if m["to"] == admin.email]
        assert to_admin and user.email in to_admin[-1]["subject"] and "Friday" in to_admin[-1]["text"]
        assert client.post("/access-requests", json={}).status_code == 409  # one at a time
        assert client.get("/usage").json()["you"]["request_pending_since"]

        with DB(get_engine()) as db:
            fresh = db.get(User, user.id)
            assert grant_more(db, fresh, interviews=2) == 1
            request = db.scalar(select(AccessRequest).where(AccessRequest.user_id == user.id))
            assert request.handled_at and request.interviews_added == 2
            act_as(fresh)
        assert "added to your account" in [m for m in mailbox.sent if m["to"] == user.email][-1]["text"]
        you = client.get("/usage").json()["you"]
        assert you["interviews_left"] == 2 and you["request_pending_since"] is None
        assert client.post(f"/interviews/{iv['id']}/sessions", json={}).status_code == 201


def test_a_practice_used_elsewhere_cant_start_twice(fake):
    act_as(new_user())
    with TestClient(app) as client:
        a = client.post(f"/interviews/{_interview(client)['id']}/sessions", json={}).json()
        b = client.post(f"/interviews/{_interview(client)['id']}/sessions", json={}).json()
        assert a["kind"] == b["kind"] == "practice"  # neither has started its questions yet
        assert client.post(f"/sessions/{a['session_id']}/resume", json={"value": {"action": "approve"}}).status_code == 200
        late = client.post(f"/sessions/{b['session_id']}/resume", json={"value": {"action": "approve"}})
        assert late.status_code == 429 and "practice is already used" in late.json()["detail"]
        assert client.post(f"/interviews/{_interview(client)['id']}/sessions", json={}).json()["kind"] == "interview"


def test_practice_budget_stops_practices_not_interviews(fake, monkeypatch):
    s = get_settings()
    first = new_user()
    act_as(first)
    with TestClient(app) as client:
        sid = client.post(f"/interviews/{_interview(client)['id']}/sessions", json={}).json()["session_id"]
        with DB(get_engine()) as db:  # pretend that practice cost $0.50
            db.add(Usage(provider="claude", model="m", unit="tokens", quantity=1, cost_usd=0.5,
                         user_id=first.id, session_id=uuid.UUID(sid)))
            db.commit()
        monkeypatch.setattr(s, "budget_practice_monthly_usd", 0.4)

        act_as(new_user())  # a new sign-up: their practice is refused
        refused = client.post(f"/interviews/{_interview(client)['id']}/sessions", json={})
        assert refused.status_code == 503 and "practices" in refused.json()["detail"]

        act_as(new_user(practice_quota=0))  # someone whose next session is an interview: fine
        assert client.post(f"/interviews/{_interview(client)['id']}/sessions", json={}).status_code == 201


def test_an_admin_has_no_quota(fake):
    act_as(new_user(is_admin=True, practice_quota=0, interview_quota=0))
    with TestClient(app) as client:
        you = client.get("/usage").json()["you"]
        assert you["practice_left"] is None and you["interviews_left"] is None and you["next_session"] == "interview"
        iv = _interview(client)
        for _ in range(3):
            view = client.post(f"/interviews/{iv['id']}/sessions", json={}).json()
            assert view["kind"] == "interview"
            _finish(client, view)
    with DB(get_engine()) as db:
        assert db.scalar(select(Session.kind).where(Session.id == uuid.UUID(view["session_id"]))) == "interview"
