"""Tests of GET /sessions, the list of your sessions in the page's account menu.

Uses fresh accounts (like tests/test_quotas.py), so the list holds only this test's sessions.
act_as() switches which account current_user returns. They check that:
- your sessions are listed newest first, with company, role, kind (practice or interview) and a
  status read from our own columns: "brief" while the brief waits for review, "interview" once
  the questions are planned, "done" once the feedback is written;
- another account's sessions never appear in your list.

    docker compose exec api pytest -q tests/test_sessions_list.py
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DB

from app.auth.deps import current_user
from app.db.models import User
from app.db.session import get_engine
from app.main import app


def new_user(**extra) -> User:
    with DB(get_engine(), expire_on_commit=False) as db:
        user = User(email=f"s-{uuid.uuid4().hex[:8]}@example.com", **extra)
        db.add(user)
        db.commit()
        return user


def act_as(user: User) -> None:
    app.dependency_overrides[current_user] = lambda: user


def _interview(client, company: str) -> dict:
    iv = client.post("/interviews", json={"company": company, "role": "Eng"}).json()
    client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python role"})
    client.post("/documents", json={"doc_type": "cv", "text": "My CV"})
    return iv


def test_lists_my_sessions_newest_first_with_their_status(fake):
    act_as(new_user())
    with TestClient(app) as client:
        assert client.get("/sessions").json() == []

        iv = _interview(client, "Northwind")
        first = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2}).json()
        assert first["pending"]["type"] == "review_brief"
        [row] = client.get("/sessions").json()
        assert (row["session_id"], row["company"], row["role"], row["kind"], row["status"]) == (
            first["session_id"], "Northwind", "Eng", "practice", "brief")

        sid = first["session_id"]
        view = client.post(f"/sessions/{sid}/resume", json={"value": {"action": "approve"}}).json()
        assert view["pending"]["type"] == "answer"
        assert client.get("/sessions").json()[0]["status"] == "interview"
        while view["status"] == "waiting":
            view = client.post(f"/sessions/{sid}/resume", json={"value": "good answer"}).json()
        assert client.get("/sessions").json()[0]["status"] == "done"

        second = client.post(f"/interviews/{_interview(client, 'Kintai')['id']}/sessions", json={}).json()
        rows = client.get("/sessions").json()
        assert [r["session_id"] for r in rows] == [second["session_id"], sid]  # newest first
        assert rows[0]["kind"] == "interview"


def test_another_accounts_sessions_are_not_listed(fake):
    act_as(new_user())
    with TestClient(app) as client:
        iv = _interview(client, "Mine")
        client.post(f"/interviews/{iv['id']}/sessions", json={})

        act_as(new_user())
        assert client.get("/sessions").json() == []
