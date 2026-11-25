"""Tests of the production pieces (Phase 5 step 1): the settings check, Amazon SES and Turnstile.

Nothing real is called: SES is a fake client that records what it was asked to send, and
Turnstile's verify_token() is replaced by a fake that accepts the token "human". They check that:
- ENVIRONMENT=production refuses unsafe settings (no SECRET_KEY, insecure cookie, console email,
  no Turnstile keys, the local database password) and accepts safe ones; local never refuses;
- SesMailer sends a plain-text email from MAIL_FROM, and an SES failure becomes MailError,
  which the API answers with 502;
- with Turnstile on, /auth/config gives the site key and /auth/start sends a code only with a
  valid token (400 and no email otherwise); with it off, no token is needed.

    docker compose exec api pytest -q tests/test_production.py
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import turnstile
from app.auth import routes as auth_routes
from app.config import Settings, check_production, get_settings
from app.mail import MailError, SesMailer
from app.main import app

SAFE = dict(
    environment="production", secret_key="x" * 40, cookie_secure=True, mail_provider="ses",
    turnstile_site_key="site", turnstile_secret_key="secret",
    database_url="postgresql+psycopg://app:long-random-password@db:5432/interview",
)


def _email() -> str:
    return f"p-{uuid.uuid4().hex[:8]}@example.com"


# --- The production settings check ---

def test_safe_production_settings_start():
    settings = Settings(_env_file=None, **SAFE)
    assert settings.production_problems() == []
    check_production(settings)  # no error


@pytest.mark.parametrize("change, problem", [
    ({"secret_key": ""}, "SECRET_KEY"),
    ({"secret_key": "short"}, "SECRET_KEY"),
    ({"cookie_secure": False}, "COOKIE_SECURE"),
    ({"mail_provider": "console"}, "MAIL_PROVIDER"),
    ({"turnstile_secret_key": ""}, "TURNSTILE"),
    ({"database_url": "postgresql+psycopg://app:app@db:5432/interview"}, "POSTGRES_PASSWORD"),
])
def test_unsafe_production_settings_refuse_to_start(change, problem):
    settings = Settings(_env_file=None, **{**SAFE, **change})
    with pytest.raises(RuntimeError, match=problem):
        check_production(settings)


def test_local_never_refuses():
    check_production(Settings(_env_file=None, environment="local", secret_key=""))


# --- Amazon SES ---

class FakeSes:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_email(self, **request):
        if self.fail:
            raise RuntimeError("MessageRejected: Email address is not verified.")
        self.sent.append(request)


def test_ses_sends_a_plain_text_email():
    client = FakeSes()
    SesMailer("Practiss <no-reply@example.com>", "eu-west-1", client=client).send(
        "someone@example.com", "Your code: 123456", "Your code is 123456")
    [request] = client.sent
    assert request["FromEmailAddress"] == "Practiss <no-reply@example.com>"
    assert request["Destination"] == {"ToAddresses": ["someone@example.com"]}
    assert request["Content"]["Simple"]["Subject"]["Data"] == "Your code: 123456"
    assert request["Content"]["Simple"]["Body"]["Text"]["Data"] == "Your code is 123456"


def test_ses_failure_is_a_mail_error():
    with pytest.raises(MailError):
        SesMailer("a@example.com", "eu-west-1", client=FakeSes(fail=True)).send("b@example.com", "s", "t")


def test_email_failure_answers_502(monkeypatch):
    failing = SesMailer("a@example.com", "eu-west-1", client=FakeSes(fail=True))
    monkeypatch.setattr(auth_routes, "get_mailer", lambda: failing)
    reply = TestClient(app).post("/auth/start", json={"email": _email()})
    assert reply.status_code == 502
    assert "could not be sent" in reply.json()["detail"]


# --- Cloudflare Turnstile ---

@pytest.fixture
def turnstile_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "turnstile_site_key", "site-key")
    monkeypatch.setattr(get_settings(), "turnstile_secret_key", "secret-key")
    monkeypatch.setattr(turnstile, "verify_token", lambda token, ip: token == "human")


def test_config_gives_the_site_key(turnstile_on):
    assert TestClient(app).get("/auth/config").json()["turnstile_site_key"] == "site-key"


def test_config_without_turnstile():
    assert TestClient(app).get("/auth/config").json()["turnstile_site_key"] is None


@pytest.mark.parametrize("token", ["", "bot"])
def test_no_code_without_a_valid_token(turnstile_on, mailbox, token):
    reply = TestClient(app).post("/auth/start", json={"email": _email(), "turnstile_token": token})
    assert reply.status_code == 400
    assert mailbox.sent == []


def test_code_with_a_valid_token(turnstile_on, mailbox):
    email = _email()
    reply = TestClient(app).post("/auth/start", json={"email": email, "turnstile_token": "human"})
    assert reply.status_code == 202
    assert mailbox.code_for(email)


def test_no_token_needed_when_off(mailbox):
    email = _email()
    assert TestClient(app).post("/auth/start", json={"email": email}).status_code == 202
    assert mailbox.code_for(email)
