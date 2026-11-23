"""Tests of logging in (and signing up) with a code sent by email (Phase 4.7 step 1), through the real API.

Every test here takes the `real_auth` fixture (tests/conftest.py), which turns off the
"always logged in as the test user" shortcut the other tests use. Emails go to the fake
mailbox (conftest `mailbox`), where the tests read the codes. They check that:
- the interview and session routes answer 401 without a login, while /health and the page stay public;
- a new email gets a code, and typing it back creates the account, logs in and marks it verified;
  an existing account logs in the same way; capitals and spaces in the email don't matter;
- "send me a code" answers the same for a new email, an existing one and a disabled one (which
  gets no email), and a bad email shape is refused;
- a code works once; a new code replaces the old one; an expired code is refused; after 5 wrong
  tries a code stops working even if the right one comes next; a code for one email doesn't work
  for another;
- logging in sets an HttpOnly, SameSite=Lax cookie that opens the protected routes; logging out
  makes that same token useless, even if someone kept a copy; an expired login and a disabled
  account are refused;
- the database never holds the raw code or token, only their HMAC / hash.

    docker compose exec api pytest -q tests/test_auth.py
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DB

from app.auth.tokens import COOKIE_NAME, hash_token
from app.db.models import EmailCode, LoginSession, User
from app.db.session import get_engine
from app.main import app
from conftest import TEST_EMAIL


def _email() -> str:
    return f"new-{uuid.uuid4().hex[:8]}@example.com"


def _start(client, email):
    return client.post("/auth/start", json={"email": email})


def _verify(client, email, code):
    return client.post("/auth/verify", json={"email": email, "code": code})


def _log_in(client, mailbox, email):
    assert _start(client, email).status_code == 202
    return _verify(client, email, mailbox.code_for(email))


def _user(email: str) -> User | None:
    with DB(get_engine()) as db:
        return db.scalar(select(User).where(User.email == email))


def test_protected_routes_need_a_login(real_auth):
    with TestClient(app) as client:
        assert client.get("/documents/cv").status_code == 401
        assert client.post("/interviews", json={"company": "X", "role": "Y"}).status_code == 401
        assert client.get(f"/sessions/{uuid.uuid4()}").status_code == 401
        assert client.get("/auth/me").status_code == 401
        assert client.get("/usage").status_code == 401
        assert client.get("/health").status_code in (200, 503)  # public
        assert client.get("/").status_code == 200  # the page itself, which shows the login screen


def test_a_new_email_signs_up_with_its_code(real_auth, mailbox):
    email = _email()
    with TestClient(app) as client:
        assert _user(email) is None  # nothing is created before the code comes back
        res = _start(client, "  " + email.upper())
        assert res.status_code == 202 and _user(email) is None
        assert mailbox.sent[-1]["to"] == email and mailbox.code_for(email) in mailbox.sent[-1]["subject"]

        res = _verify(client, email, mailbox.code_for(email))
        assert res.status_code == 200 and res.json() == {"email": email, "is_admin": False, "new_account": True}
        cookie = res.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert client.get("/auth/me").json()["email"] == email
        assert client.get("/documents/cv").json() == []  # a new, empty account
    assert _user(email).email_verified_at is not None


def test_an_existing_account_logs_in_the_same_way(real_auth, mailbox, test_user):
    with TestClient(app) as client:
        res = _log_in(client, mailbox, TEST_EMAIL)
        assert res.status_code == 200 and res.json()["new_account"] is False
        assert client.get("/auth/me").json()["email"] == TEST_EMAIL


def test_send_me_a_code_reveals_nothing(real_auth, mailbox):
    disabled = _email()
    with DB(get_engine()) as db:
        db.add(User(email=disabled, disabled_at=datetime.now(timezone.utc)))
        db.commit()
    with TestClient(app) as client:
        answers = [_start(client, e) for e in (_email(), TEST_EMAIL, disabled)]
        assert {r.status_code for r in answers} == {202} and len({r.text for r in answers}) == 1
        assert disabled not in [m["to"] for m in mailbox.sent]  # a disabled account gets no email
        assert _start(client, "not-an-email").status_code == 422


def test_codes_work_once_and_only_the_latest(real_auth, mailbox):
    email = _email()
    with TestClient(app) as client:
        _start(client, email)
        old = mailbox.code_for(email)
        _start(client, email)
        new = mailbox.code_for(email)
        if old != new:  # (one chance in a million that both codes are equal)
            assert _verify(client, email, old).status_code == 401  # replaced by the new one
        assert _verify(client, email, new).status_code == 200
        assert _verify(client, email, new).status_code == 401  # already used


def test_a_code_belongs_to_one_email(real_auth, mailbox):
    mine, other = _email(), _email()
    with TestClient(app) as client:
        _start(client, mine)
        _start(client, other)
        if mailbox.code_for(mine) != mailbox.code_for(other):  # (one chance in a million they match)
            assert _verify(client, other, mailbox.code_for(mine)).status_code == 401
            assert _user(other) is None


def test_expired_and_overtried_codes_are_refused(real_auth, mailbox, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "login_max_failures", 100)  # test the per-code limit alone
    expired, tried = _email(), _email()
    with TestClient(app) as client:
        _start(client, expired)
        with DB(get_engine()) as db:
            db.execute(update(EmailCode).where(EmailCode.email == expired)
                       .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            db.commit()
        assert _verify(client, expired, mailbox.code_for(expired)).status_code == 401

        _start(client, tried)
        right = mailbox.code_for(tried)
        wrong = f"{(int(right) + 1) % 1_000_000:06d}"
        for _ in range(5):
            assert _verify(client, tried, wrong).status_code == 401
        assert _verify(client, tried, right).status_code == 401  # this code is finished
        assert _user(tried) is None


def test_logout_closes_the_cookie_even_for_a_kept_copy(real_auth, mailbox):
    with TestClient(app) as client:
        _log_in(client, mailbox, TEST_EMAIL)
        token = client.cookies[COOKIE_NAME]
        assert client.post("/auth/logout").status_code == 204
        assert client.get("/auth/me").status_code == 401
    with TestClient(app, cookies={COOKIE_NAME: token}) as thief:
        assert thief.get("/auth/me").status_code == 401


def test_expired_login_and_disabled_account_are_refused(real_auth, mailbox):
    email = _email()
    with TestClient(app) as client:
        _log_in(client, mailbox, email)
        token = client.cookies[COOKIE_NAME]
        with DB(get_engine()) as db:
            db.execute(update(LoginSession).where(LoginSession.token_hash == hash_token(token))
                       .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            db.commit()
        assert client.get("/auth/me").status_code == 401

        _log_in(client, mailbox, email)
        with DB(get_engine()) as db:
            db.execute(update(User).where(User.email == email).values(disabled_at=datetime.now(timezone.utc)))
            db.commit()
        assert client.get("/auth/me").status_code == 401  # the existing login stops working


def test_only_hashes_are_stored(real_auth, mailbox):
    email = _email()
    with TestClient(app) as client:
        _start(client, email)
        code = mailbox.code_for(email)
        _verify(client, email, code)
        token = client.cookies[COOKIE_NAME]
    with DB(get_engine()) as db:
        stored = db.scalars(select(EmailCode.code_hash).where(EmailCode.email == email)).all()
        assert stored and all(len(h) == 64 and code not in h for h in stored)
        hashes = db.scalars(select(LoginSession.token_hash).join(User).where(User.email == email)).all()
        assert hash_token(token) in hashes and token not in hashes
