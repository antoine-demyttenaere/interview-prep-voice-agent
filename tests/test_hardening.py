"""Tests of the account hardening (Phase 4.6 step 4), through the real API and cookie check.

They check that:
- the 6th wrong password in 15 minutes is blocked (429), even when it is the right one, and the
  email works again once the failures are older than the window;
- a successful login resets the count, so honest typos never add up to a lockout;
- one IP address can't spray guesses across many emails, nor register over and over;
- a POST / DELETE sent from another website's page is refused (403), while the app's own page,
  the CLI (no Origin header) and plain GETs go through;
- changing the password needs the current one, ends the other logins, and the old password stops working;
- "Delete my account" needs the password, then leaves no row of that user (checkpoints and
  recordings included) and doesn't touch anyone else's data.

    docker compose exec api pytest -q tests/test_hardening.py
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session as DB

from app.auth.account import AUDIO_DIR
from app.auth.passwords import hash_password
from app.config import get_settings
from app.db.models import AuthAttempt, Chunk, Document, Interview, LoginSession, Session, User
from app.db.session import get_engine
from app.main import app
from conftest import TEST_EMAIL

PASSWORD = "the right long password"


def _new_user(password=PASSWORD) -> str:
    email = f"h-{uuid.uuid4().hex[:8]}@example.com"
    with DB(get_engine()) as db:
        db.add(User(email=email, password_hash=hash_password(password)))
        db.commit()
    return email


def _login(client, email, password=PASSWORD, **headers):
    return client.post("/auth/login", json={"email": email, "password": password}, headers=headers)


# ---------- guessing limits ----------

def test_sixth_wrong_password_is_blocked_until_the_window_passes(real_auth):
    email = _new_user()
    with TestClient(app) as client:
        for _ in range(5):
            assert _login(client, email, "wrong guess").status_code == 401
        blocked = _login(client, email)  # the right password, but too late
        assert blocked.status_code == 429 and "wait" in blocked.json()["detail"]

        with DB(get_engine()) as db:  # 16 minutes later...
            db.execute(update(AuthAttempt).where(AuthAttempt.email == email)
                       .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
            db.commit()
        assert _login(client, email).status_code == 200


def test_successful_login_resets_the_count(real_auth):
    email = _new_user()
    with TestClient(app) as client:
        for _ in range(2):
            for _ in range(4):
                assert _login(client, email, "typo").status_code == 401
            assert _login(client, email).status_code == 200


def test_one_ip_cant_spray_many_emails(real_auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_failures_per_ip", 3)
    with TestClient(app) as client:
        for _ in range(3):
            assert _login(client, f"guess-{uuid.uuid4().hex[:6]}@example.com", "x").status_code == 401
        assert _login(client, _new_user()).status_code == 429  # even a real account, right password


def test_registration_attempts_are_limited_per_ip(real_auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "register_max_per_hour", 3)
    with TestClient(app) as client:
        body = {"email": "x@example.com", "password": "long enough password", "code": "AAAA-BBBB-CCCC"}
        assert [client.post("/auth/register", json=body).status_code for _ in range(4)] == [400, 400, 400, 429]


# ---------- requests from other websites ----------

def test_requests_from_another_site_are_refused(real_auth):
    with TestClient(app) as client:  # TestClient's server is http://testserver
        evil = {"Origin": "http://localhost:3000"}
        assert _login(client, TEST_EMAIL, **evil).status_code == 403
        assert client.delete(f"/documents/{uuid.uuid4()}", headers=evil).status_code == 403
        assert client.post("/auth/logout", headers={"Origin": "null"}).status_code == 403
        assert client.get("/health", headers=evil).status_code in (200, 503)  # reading is fine

        assert _login(client, TEST_EMAIL, "wrong", Origin="http://testserver").status_code == 401  # own page
        assert _login(client, TEST_EMAIL, "wrong").status_code == 401  # no Origin: CLI, curl


# ---------- password change ----------

def test_change_password(real_auth):
    email = _new_user()
    new = "a brand new long password"
    with TestClient(app) as here, TestClient(app) as elsewhere:
        _login(here, email)
        _login(elsewhere, email)
        change = lambda cur, nxt: here.post("/auth/password", json={"current_password": cur, "new_password": nxt})
        assert change("not it", new).status_code == 403
        assert change(PASSWORD, "short").status_code == 422
        assert change(PASSWORD, new).status_code == 204

        assert here.get("/auth/me").status_code == 200  # this login stays
        assert elsewhere.get("/auth/me").status_code == 401  # the other one ended
    with TestClient(app) as client:
        assert _login(client, email).status_code == 401  # the old password no longer works
        assert _login(client, email, new).status_code == 200


# ---------- delete my account ----------

def _count(db, model, user_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))


def test_delete_account_removes_everything_and_only_that(real_auth, fake, test_user):
    email = _new_user()
    with TestClient(app) as client:
        _login(client, email)
        iv = client.post("/interviews", json={"company": "Gone Co", "role": "Eng"}).json()
        client.post("/documents", json={"interview_id": iv["id"], "doc_type": "job_post", "text": "Python role"})
        client.post("/documents", json={"doc_type": "cv", "text": "My CV, soon gone."})
        sid = client.post(f"/interviews/{iv['id']}/sessions", json={"num_questions": 2}).json()["session_id"]
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        recording = AUDIO_DIR / f"{sid}_q1_test.webm"
        recording.write_bytes(b"audio")

        with DB(get_engine()) as db:
            user_id = db.scalar(select(User.id).where(User.email == email))
            mine_before = _count(db, Document, test_user.id)
            assert db.scalar(text("SELECT count(*) FROM checkpoints WHERE thread_id = :t"), {"t": sid}) > 0
            assert _count(db, Chunk, user_id) > 0
        assert client.request("DELETE", "/auth/me", json={"password": "wrong"}).status_code == 403
        assert client.get("/auth/me").status_code == 200  # nothing happened

        assert client.request("DELETE", "/auth/me", json={"password": PASSWORD}).status_code == 204
        assert client.get("/auth/me").status_code == 401

    with DB(get_engine()) as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.id == user_id)) == 0
        for model in (Interview, Document, Chunk, LoginSession):
            assert _count(db, model, user_id) == 0, model.__name__
        assert db.scalar(select(func.count()).select_from(Session).where(Session.id == uuid.UUID(sid))) == 0
        for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
            assert db.scalar(text(f"SELECT count(*) FROM {table} WHERE thread_id = :t"), {"t": sid}) == 0, table
        assert _count(db, Document, test_user.id) == mine_before  # someone else's data is untouched
    assert not recording.exists()
