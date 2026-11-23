"""Tests of the account hardening (Phase 4.6 step 4, adapted to code login in 4.7), through the real API.

They check that:
- after 5 wrong codes in 15 minutes, the email is blocked (429), even with the right code, and
  works again once the failures are older than the window;
- a successful login resets the count, so honest typos never add up to a lockout;
- one IP address can't try codes on many emails, and nobody can flood an inbox with codes
  (per email and per IP);
- a POST / DELETE sent from another website's page is refused (403), while the app's own page,
  the CLI (no Origin header) and plain GETs go through;
- "Delete my account" needs a fresh code sent for that purpose (a login code doesn't work), then
  leaves no row of that user (checkpoints and recordings included) and doesn't touch anyone
  else's data.

    docker compose exec api pytest -q tests/test_hardening.py
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session as DB

from app.auth.account import AUDIO_DIR
from app.config import get_settings
from app.db.models import AuthAttempt, Chunk, Document, Interview, LoginSession, Session, User
from app.db.session import get_engine
from app.main import app
from conftest import TEST_EMAIL


def _email() -> str:
    return f"h-{uuid.uuid4().hex[:8]}@example.com"


def _start(client, email, **headers):
    return client.post("/auth/start", json={"email": email}, headers=headers)


def _verify(client, email, code, **headers):
    return client.post("/auth/verify", json={"email": email, "code": code}, headers=headers)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _log_in(client, mailbox, email):
    _start(client, email)
    return _verify(client, email, mailbox.code_for(email))


# ---------- guessing limits ----------

def test_sixth_wrong_code_is_blocked_until_the_window_passes(real_auth, mailbox, monkeypatch):
    monkeypatch.setattr(get_settings(), "code_max_tries", 100)  # test the per-email limit alone
    email = _email()
    with TestClient(app) as client:
        _start(client, email)
        right = mailbox.code_for(email)
        for _ in range(5):
            assert _verify(client, email, _wrong(right)).status_code == 401
        blocked = _verify(client, email, right)  # the right code, but too late
        assert blocked.status_code == 429 and "wait" in blocked.json()["detail"]

        with DB(get_engine()) as db:  # 16 minutes later...
            db.execute(update(AuthAttempt).where(AuthAttempt.email == email)
                       .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
            db.commit()
        assert _verify(client, email, right).status_code == 200


def test_successful_login_resets_the_count(real_auth, mailbox):
    email = _email()
    with TestClient(app) as client:
        for _ in range(2):
            _start(client, email)
            right = mailbox.code_for(email)
            for _ in range(4):
                assert _verify(client, email, _wrong(right)).status_code == 401
            assert _verify(client, email, right).status_code == 200


def test_one_ip_cant_try_codes_on_many_emails(real_auth, mailbox, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_max_failures_per_ip", 3)
    with TestClient(app) as client:
        for _ in range(3):
            assert _verify(client, _email(), "000000").status_code == 401
        email = _email()
        _start(client, email)
        assert _verify(client, email, mailbox.code_for(email)).status_code == 429  # even the right code


def test_nobody_can_flood_an_inbox(real_auth, mailbox, monkeypatch):
    email = _email()
    with TestClient(app) as client:
        assert [_start(client, email).status_code for _ in range(4)] == [202, 202, 202, 429]  # 3 per email
        assert len([m for m in mailbox.sent if m["to"] == email]) == 3

        monkeypatch.setattr(get_settings(), "codes_per_ip", 2)
        with DB(get_engine()) as db:
            db.execute(update(AuthAttempt).values(created_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
            db.commit()
        assert [_start(client, _email()).status_code for _ in range(3)] == [202, 202, 429]  # per IP


# ---------- requests from other websites ----------

def test_requests_from_another_site_are_refused(real_auth):
    with TestClient(app) as client:  # TestClient's server is http://testserver
        evil = {"Origin": "http://localhost:3000"}
        assert _start(client, TEST_EMAIL, **evil).status_code == 403
        assert _verify(client, TEST_EMAIL, "123456", **evil).status_code == 403
        assert client.delete(f"/documents/{uuid.uuid4()}", headers=evil).status_code == 403
        assert client.post("/auth/logout", headers={"Origin": "null"}).status_code == 403
        assert client.get("/health", headers=evil).status_code in (200, 503)  # reading is fine

        assert _start(client, TEST_EMAIL, Origin="http://testserver").status_code == 202  # own page
        assert _start(client, _email()).status_code == 202  # no Origin: CLI, curl


# ---------- delete my account ----------

def _count(db, model, user_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id))


def test_delete_account_needs_its_own_code_and_removes_everything(real_auth, fake, test_user, mailbox):
    email = _email()
    with TestClient(app) as client:
        _log_in(client, mailbox, email)
        login_code = mailbox.code_for(email)
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

        delete = lambda code: client.request("DELETE", "/auth/me", json={"code": code})
        assert delete(login_code).status_code == 403  # a login code can't delete the account
        assert client.post("/auth/delete-code").status_code == 202
        code = mailbox.code_for(email)
        assert "deleting" in mailbox.sent[-1]["subject"].lower()
        assert delete(_wrong(code)).status_code == 403
        assert client.get("/auth/me").status_code == 200  # nothing happened

        assert delete(code).status_code == 204
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
