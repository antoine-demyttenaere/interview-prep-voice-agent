"""Guessing limits: slow down anyone trying many login codes, or flooding inboxes with codes.

Used by POST /auth/start (send a code) and POST /auth/verify (check it) in app/auth/routes.py.
Attempts are rows in the auth_attempts table (migration 0008); the limits are settings
(app/config.py, .env), all over a window of LOGIN_WINDOW_MINUTES (15):

- Codes sent: at most CODES_PER_EMAIL (3) per email and CODES_PER_IP (10) per IP address. This
  stops someone filling another person's inbox, and caps what sign-ups can cost in email.
- Wrong codes: after LOGIN_MAX_FAILURES (5) wrong codes for one email, that email is locked
  until the oldest failure is out of the window, even with the right code. A successful login
  clears the email's failures. One IP address also gets at most LOGIN_MAX_FAILURES_PER_IP (20)
  wrong codes, so nobody can try codes on many emails at once. (Each code also stops working
  after CODE_MAX_TRIES wrong tries: app/auth/codes.py.)

A blocked attempt gets HTTP 429 ("Too Many Requests") with how long to wait. It is checked
before the code, so the answer doesn't depend on whether the guess was right. The counts live in
Postgres (not in the server's memory) so they hold with several servers, as on AWS.

The trade-off: someone who knows your email can lock it for 15 minutes by typing wrong codes.

The IP address is the one the server sees (request.client.host). In production every request
comes through Caddy (docker-compose.prod.yml), so uvicorn is told to trust the X-Forwarded-For
header (the visitor's IP, added by Caddy) from Caddy's fixed address only
(--forwarded-allow-ips): otherwise every caller would share Caddy's address, and if it trusted
anyone, a bot could fake a new IP on each request.
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DB

from app.config import get_settings
from app.db.models import AuthAttempt

KEEP = timedelta(days=1)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _window() -> timedelta:
    return timedelta(minutes=get_settings().login_window_minutes)


def _check(db: DB, kind: str, column, value: str, limit: int) -> None:
    window = _window()
    since = datetime.now(timezone.utc) - window
    count, oldest = db.execute(
        select(func.count(), func.min(AuthAttempt.created_at))
        .where(AuthAttempt.kind == kind, column == value, AuthAttempt.created_at > since)
    ).one()
    if count >= limit:
        wait = max(1, int(((oldest + window) - datetime.now(timezone.utc)).total_seconds() // 60) + 1)
        raise HTTPException(429, f"Too many attempts. Please wait {wait} minute{'s' if wait > 1 else ''} and try again.")


def record(db: DB, kind: str, ip: str, email: str | None = None) -> None:
    """Add one attempt (and drop rows older than a day). Commits."""
    db.execute(delete(AuthAttempt).where(AuthAttempt.created_at < datetime.now(timezone.utc) - KEEP))
    db.add(AuthAttempt(kind=kind, ip=ip, email=email))
    db.commit()


def check_code_request(db: DB, email: str, ip: str) -> None:
    s = get_settings()
    _check(db, "code_sent", AuthAttempt.email, email, s.codes_per_email)
    _check(db, "code_sent", AuthAttempt.ip, ip, s.codes_per_ip)


def check_login(db: DB, email: str, ip: str) -> None:
    s = get_settings()
    _check(db, "login_failed", AuthAttempt.email, email, s.login_max_failures)
    _check(db, "login_failed", AuthAttempt.ip, ip, s.login_max_failures_per_ip)


def clear_login_failures(db: DB, email: str) -> None:
    """After a successful login: that email starts again from zero (the caller commits)."""
    db.execute(delete(AuthAttempt).where(AuthAttempt.kind == "login_failed", AuthAttempt.email == email))
