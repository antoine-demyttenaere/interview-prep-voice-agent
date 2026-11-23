"""Tests of registration with an invite code (Phase 4.6 step 3), through the real API.

Invites are made with app/auth/invites.py, like scripts/create_invite.py does. They check that:
- a valid code creates the account, logs it in (cookie), and marks the invite used;
- typing is forgiving (lowercase, spaces, no dashes; email in capitals);
- the same code can't be used twice, even by the same person;
- an unknown, expired, used, or other-email code all get the same answer, and a wrong attempt
  doesn't burn the code for the right person;
- a weak password or a bad email is refused before the code is spent;
- an email that already has an account is told to log in, without spending the code;
- only the code's hash is stored, and codes look like XXXX-XXXX-XXXX.

    docker compose exec api pytest -q tests/test_register.py
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session as DB

from app.auth.invites import ALPHABET, create_invite, hash_code
from app.db.models import Invite, User
from app.db.session import get_engine
from app.main import app
from conftest import TEST_EMAIL

PASSWORD = "a long enough password"


def _email() -> str:
    return f"new-{uuid.uuid4().hex[:8]}@example.com"


def _invite(email: str, **kw) -> str:
    with DB(get_engine()) as db:
        return create_invite(db, email, **kw)


def _register(client, email, code, password=PASSWORD):
    return client.post("/auth/register", json={"email": email, "password": password, "code": code})


def _invite_row(code: str) -> Invite:
    with DB(get_engine()) as db:
        return db.scalar(select(Invite).where(Invite.code_hash == hash_code(code)))


def test_valid_code_creates_and_logs_in_the_account(real_auth):
    email = _email()
    code = _invite(email)
    with TestClient(app) as client:
        res = _register(client, email, code)
        assert res.status_code == 201 and res.json() == {"email": email, "is_admin": False}
        assert client.get("/auth/me").json()["email"] == email  # logged in by the cookie
        assert client.get("/documents/cv").json() == []  # a new, empty account
    invite = _invite_row(code)
    assert invite.used_at is not None and invite.used_by is not None
    with TestClient(app) as client:  # and the password works for a normal login
        assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


def test_typing_is_forgiving(real_auth):
    email = _email()
    code = _invite(email)
    with TestClient(app) as client:
        sloppy = " " + code.lower().replace("-", " ") + " "
        assert _register(client, "  " + email.upper(), sloppy).status_code == 201


def test_a_code_works_once(real_auth):
    email = _email()
    code = _invite(email)
    with TestClient(app) as client:
        assert _register(client, email, code).status_code == 201
        again = _register(client, _email(), code)
        assert again.status_code == 400


def test_bad_codes_all_look_the_same_and_dont_burn_the_code(real_auth):
    right, other = _email(), _email()
    code = _invite(right)
    expired_email = _email()
    expired = _invite(expired_email)
    with DB(get_engine()) as db:
        db.execute(update(Invite).where(Invite.code_hash == hash_code(expired))
                   .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()

    with TestClient(app) as client:
        answers = [
            _register(client, other, code),  # the right code, someone else's email
            _register(client, right, "AAAA-BBBB-CCCC"),  # a made-up code
            _register(client, expired_email, expired),  # expired
        ]
        assert {r.status_code for r in answers} == {400}
        assert len({r.text for r in answers}) == 1  # same message every time
        assert _register(client, right, code).status_code == 201  # still works for the right person


def test_weak_password_or_bad_email_is_refused_before_the_code_is_spent(real_auth):
    email = _email()
    code = _invite(email)
    with TestClient(app) as client:
        weak = _register(client, email, code, password="short")
        assert weak.status_code == 422 and "10 characters" in weak.json()["detail"]
        assert _register(client, "not-an-email", code).status_code == 422
        assert _invite_row(code).used_at is None
        assert _register(client, email, code).status_code == 201


def test_existing_account_is_told_to_log_in(real_auth, test_user):
    code = _invite(TEST_EMAIL)  # an invite for an email that already has an account
    with TestClient(app) as client:
        res = _register(client, TEST_EMAIL, code, password="another long password")
        assert res.status_code == 409 and "log in" in res.json()["detail"]
    assert _invite_row(code).used_at is None
    with DB(get_engine()) as db:  # the existing account was not changed
        assert db.scalar(select(User).where(User.email == TEST_EMAIL)).password_hash == test_user.password_hash


def test_only_the_hash_is_stored_and_codes_are_readable():
    email = _email()
    code = _invite(email)
    assert re.fullmatch(rf"[{ALPHABET}]{{4}}-[{ALPHABET}]{{4}}-[{ALPHABET}]{{4}}", code)
    with DB(get_engine()) as db:
        row = db.scalar(select(Invite).where(Invite.email == email))
        assert row.code_hash == hash_code(code) and code not in row.code_hash
