"""Tests of the admin password login and the admin's missing per-user limits (Phase 4.8).

They check that:
- an admin with a password logs in with it (same cookie as a code login), and the code login
  still works for that admin as a fallback;
- a wrong password, a non-admin account that somehow has a password, an admin without a password,
  an unknown email and a disabled admin all get the same 401 and message;
- the guessing limit applies: the 6th wrong password in the window is blocked, even when right;
- only the Argon2 hash is stored; manage_users.py set-password refuses non-admins and short
  passwords, and ends the admin's other logins;
- an admin isn't held back by the interviews-per-day or brief-revisions limits, while the
  budgets still apply.

    docker compose exec api pytest -q tests/test_admin.py
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.auth.deps import current_user
from app.auth.passwords import hash_password
from app.config import get_settings
from app.db.models import LoginSession, Usage, User
from app.db.session import get_engine
from app.main import app
from scripts import manage_users

PASSWORD = "a long admin passphrase"


def _user(password: str | None = PASSWORD, **extra) -> User:
    with DB(get_engine(), expire_on_commit=False) as db:
        user = User(email=f"a-{uuid.uuid4().hex[:8]}@example.com",
                    password_hash=hash_password(password) if password else None, **extra)
        db.add(user)
        db.commit()
        return user


def _login(client, email, password=PASSWORD):
    return client.post("/auth/password-login", json={"email": email, "password": password})


def test_admin_logs_in_with_a_password_or_a_code(real_auth, mailbox):
    admin = _user(is_admin=True)
    with TestClient(app) as client:
        res = _login(client, admin.email.upper())
        assert res.status_code == 200 and res.json()["is_admin"] is True
        assert "httponly" in res.headers["set-cookie"].lower()
        assert client.get("/auth/me").json()["email"] == admin.email
        client.post("/auth/logout")

        client.post("/auth/start", json={"email": admin.email})  # the code login still works
        assert client.post("/auth/verify", json={"email": admin.email,
                                                 "code": mailbox.code_for(admin.email)}).status_code == 200


def test_every_refusal_looks_the_same(real_auth):
    admin = _user(is_admin=True)
    not_admin = _user()  # has a hash (e.g. left from before), but isn't an admin
    no_password = _user(password=None, is_admin=True)
    disabled = _user(is_admin=True, disabled_at=datetime.now(timezone.utc))
    with TestClient(app) as client:
        answers = [
            _login(client, admin.email, "wrong password here"),
            _login(client, not_admin.email),
            _login(client, no_password.email),
            _login(client, f"nobody-{uuid.uuid4().hex[:6]}@example.com"),
            _login(client, disabled.email),
        ]
        assert {r.status_code for r in answers} == {401}
        assert len({r.text for r in answers}) == 1
        assert client.get("/auth/me").status_code == 401


def test_sixth_wrong_password_is_blocked(real_auth):
    admin = _user(is_admin=True)
    with TestClient(app) as client:
        for _ in range(5):
            assert _login(client, admin.email, "not the password").status_code == 401
        assert _login(client, admin.email).status_code == 429  # even the right one


def test_set_password_rules(monkeypatch):
    admin = _user(password=None, is_admin=True)
    user = _user(password=None)
    with DB(get_engine()) as db:
        db.add(LoginSession(user_id=admin.id, token_hash=uuid.uuid4().hex * 2,
                            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc)))
        db.commit()

    monkeypatch.setenv("NEW_PASSWORD", "short")
    with DB(get_engine()) as db, pytest.raises(SystemExit):
        manage_users.set_password(db, db.get(User, admin.id))  # too short
    monkeypatch.setenv("NEW_PASSWORD", PASSWORD)
    with DB(get_engine()) as db, pytest.raises(SystemExit):
        manage_users.set_password(db, db.get(User, user.id))  # not an admin
    with DB(get_engine()) as db:
        manage_users.set_password(db, db.get(User, admin.id))
    with DB(get_engine()) as db:
        stored = db.get(User, admin.id).password_hash
        assert stored.startswith("$argon2id$") and PASSWORD not in stored
        assert db.scalar(select(LoginSession).where(LoginSession.user_id == admin.id)) is None  # logged out
        assert db.get(User, user.id).password_hash is None


def test_admin_has_no_per_user_limits_but_budgets_apply(fake, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "interviews_per_day", 1)
    monkeypatch.setattr(s, "brief_revisions_max", 1)
    admin = _user(is_admin=True)
    app.dependency_overrides[current_user] = lambda: admin
    with TestClient(app) as client:
        iv = client.post("/interviews", json={"company": "Co", "role": "Eng"}).json()
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python"})
        for _ in range(3):  # 3 interviews in a day, over the limit of 1
            sid = client.post(f"/interviews/{iv['id']}/sessions", json={"review_brief": True}).json()["session_id"]
            revise = {"value": {"action": "revise", "feedback": "more"}}
            assert [client.post(f"/sessions/{sid}/resume", json=revise).status_code for _ in range(3)] == [200] * 3
            assert client.post(f"/sessions/{sid}/resume", json={"value": {"action": "approve"}}).status_code == 200

        monkeypatch.setattr(s, "budget_monthly_usd", 0.01)
        with DB(get_engine()) as db:
            db.add(Usage(provider="claude", model="m", unit="tokens", quantity=1, cost_usd=0.02))
            db.commit()
        assert client.post(f"/interviews/{iv['id']}/sessions", json={}).status_code == 503  # budgets: for everyone
