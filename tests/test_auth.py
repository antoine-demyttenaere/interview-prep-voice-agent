"""Tests of accounts and login (Phase 4.6 step 1), through the real API and the real cookie check.

Every test here takes the `real_auth` fixture (tests/conftest.py), which turns off the
"always logged in as the test user" shortcut the other tests use. They check that:
- the interview and session routes answer 401 without a login, while /health and the page stay public;
- a wrong password and an unknown email get the same 401 and message;
- logging in sets an HttpOnly, SameSite=Lax cookie, the email is matched in any case, and the
  cookie then opens the protected routes;
- logging out makes that same token useless, even if someone kept a copy;
- an expired login and a disabled account are refused;
- the database never holds the password or the raw token, only their hashes.

    docker compose exec api pytest -q tests/test_auth.py
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DB

from app.auth.passwords import hash_password
from app.auth.tokens import COOKIE_NAME, hash_token
from app.db.models import LoginSession, User
from app.db.session import get_engine
from app.main import app
from conftest import TEST_EMAIL, TEST_PASSWORD


def _login(client, email=TEST_EMAIL, password=TEST_PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def _new_user(password="another long password", **extra) -> str:
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    with DB(get_engine()) as db:
        db.add(User(email=email, password_hash=hash_password(password), **extra))
        db.commit()
    return email


def test_protected_routes_need_a_login(real_auth):
    with TestClient(app) as client:
        assert client.get("/documents/cv").status_code == 401
        assert client.post("/interviews", json={"company": "X", "role": "Y"}).status_code == 401
        assert client.get(f"/sessions/{uuid.uuid4()}").status_code == 401
        assert client.get("/auth/me").status_code == 401
        assert client.get("/health").status_code in (200, 503)  # public
        assert client.get("/").status_code == 200  # the page itself, which shows the login screen


def test_wrong_password_and_unknown_email_look_the_same(real_auth):
    with TestClient(app) as client:
        wrong = _login(client, password="not the password")
        unknown = _login(client, email="nobody@example.com")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
        assert COOKIE_NAME not in client.cookies


def test_login_cookie_opens_the_api_and_logout_closes_it(real_auth):
    with TestClient(app) as client:
        res = _login(client, email="  Tests@Example.COM ")  # spaces and capitals don't matter
        assert res.status_code == 200
        assert res.json() == {"email": TEST_EMAIL, "is_admin": False}
        cookie = res.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie

        token = client.cookies[COOKIE_NAME]
        assert client.get("/auth/me").json()["email"] == TEST_EMAIL
        assert client.get("/documents/cv").status_code == 200

        assert client.post("/auth/logout").status_code == 204
        assert client.get("/auth/me").status_code == 401

    with TestClient(app, cookies={COOKIE_NAME: token}) as thief:  # a kept copy of the old cookie
        assert thief.get("/auth/me").status_code == 401


def test_expired_login_is_refused(real_auth):
    with TestClient(app) as client:
        _login(client)
        token = client.cookies[COOKIE_NAME]
        with DB(get_engine()) as db:
            db.execute(update(LoginSession).where(LoginSession.token_hash == hash_token(token))
                       .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            db.commit()
        assert client.get("/auth/me").status_code == 401


def test_disabled_account_cannot_log_in_or_stay_logged_in(real_auth):
    email = _new_user()
    with TestClient(app) as client:
        assert _login(client, email=email, password="another long password").status_code == 200
        with DB(get_engine()) as db:
            db.execute(update(User).where(User.email == email).values(disabled_at=datetime.now(timezone.utc)))
            db.commit()
        assert client.get("/auth/me").status_code == 401  # the existing login stops working
        assert _login(client, email=email, password="another long password").status_code == 401


def test_only_hashes_are_stored(real_auth):
    with TestClient(app) as client:
        _login(client)
        token = client.cookies[COOKIE_NAME]
    with DB(get_engine()) as db:
        user = db.scalar(select(User).where(User.email == TEST_EMAIL))
        assert user.password_hash.startswith("$argon2id$") and TEST_PASSWORD not in user.password_hash
        hashes = db.scalars(select(LoginSession.token_hash).where(LoginSession.user_id == user.id)).all()
        assert hash_token(token) in hashes and token not in hashes
