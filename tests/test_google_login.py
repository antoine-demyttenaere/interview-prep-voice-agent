"""Tests of "Continue with Google" (POST /auth/google, Phase 4.9), through the real API.

Google is never called: `google_token` replaces app.auth.google.verify_token with a fake that
reads the "token" as JSON claims, or raises ValueError (what the real check does for a forged,
expired or foreign token). They check that:
- without GOOGLE_CLIENT_ID, /auth/config offers no button and /auth/google answers 404;
- a new Google account signs up and logs in (HttpOnly cookie), and the second time logs into
  the same account;
- an existing account with the same email (made with a code) is linked, not duplicated;
- a refused token, an unverified email and a disabled account all get 401 and no login.

    docker compose exec api pytest -q tests/test_google_login.py
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from app.auth import google
from app.config import get_settings
from app.db.models import User
from app.db.session import get_engine
from app.main import app


@pytest.fixture
def google_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", "test-client.apps.googleusercontent.com")

    def fake_verify(credential, client_id):
        assert client_id == "test-client.apps.googleusercontent.com"
        if credential == "forged":
            raise ValueError("bad signature")
        return json.loads(credential)

    monkeypatch.setattr(google, "verify_token", fake_verify)


def _token(email, sub=None, verified=True) -> str:
    return json.dumps({"sub": sub or uuid.uuid4().hex, "email": email, "email_verified": verified})


def _email() -> str:
    return f"g-{uuid.uuid4().hex[:8]}@example.com"


def _users(email) -> list[User]:
    with DB(get_engine()) as db:
        return db.scalars(select(User).where(func.lower(User.email) == email.lower())).all()


def test_not_set_up(real_auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_client_id", "")
    with TestClient(app) as client:
        assert client.get("/auth/config").json()["google_client_id"] is None
        assert client.post("/auth/google", json={"credential": "x"}).status_code == 404


def test_sign_up_then_log_in_again(real_auth, google_token):
    email, sub = _email(), uuid.uuid4().hex
    with TestClient(app) as client:
        assert client.get("/auth/config").json()["google_client_id"]
        res = client.post("/auth/google", json={"credential": _token(email.upper(), sub)})
        assert res.status_code == 200 and res.json() == {"email": email, "is_admin": False, "new_account": True}
        assert "httponly" in res.headers["set-cookie"].lower()
        assert client.get("/auth/me").json()["email"] == email
        client.post("/auth/logout")

        res = client.post("/auth/google", json={"credential": _token(email, sub)})
        assert res.status_code == 200 and res.json()["new_account"] is False
    [user] = _users(email)
    assert user.google_sub == sub and user.email_verified_at is not None


def test_links_an_existing_account(real_auth, google_token):
    email = _email()
    with DB(get_engine()) as db:
        db.add(User(email=email))
        db.commit()
    with TestClient(app) as client:
        res = client.post("/auth/google", json={"credential": _token(email)})
        assert res.status_code == 200 and res.json()["new_account"] is False
    assert len(_users(email)) == 1 and _users(email)[0].google_sub


def test_refusals(real_auth, google_token):
    disabled = _email()
    with DB(get_engine()) as db:
        db.add(User(email=disabled, disabled_at=datetime.now(timezone.utc)))
        db.commit()
    with TestClient(app) as client:
        for credential in ("forged", _token(_email(), verified=False), _token(disabled)):
            res = client.post("/auth/google", json={"credential": credential})
            assert res.status_code == 401
            assert client.get("/auth/me").status_code == 401
