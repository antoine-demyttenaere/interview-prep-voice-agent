"""Guessing limits: slow down anyone trying many passwords or invite codes.

Used by POST /auth/login and POST /auth/register (app/auth/routes.py). Attempts are rows in the
auth_attempts table (migration 0008); the limits are settings (app/config.py, .env):

- Login: after LOGIN_MAX_FAILURES (5) wrong passwords for one email in LOGIN_WINDOW_MINUTES
  (15), that email is locked until the oldest failure is out of the window, even with the right
  password. A successful login clears the email's failures. Separately, one IP address gets at
  most LOGIN_MAX_FAILURES_PER_IP (20) failures in the window, so nobody can try 5 passwords on
  every email.
- Registration: at most REGISTER_MAX_PER_HOUR (10) attempts per IP address per hour.

A blocked attempt gets HTTP 429 ("Too Many Requests") with how long to wait. It is checked
before the password, so the answer doesn't depend on whether the guess was right.

The trade-off: someone who knows your email can lock it for 15 minutes by typing wrong
passwords. That is the usual price of a per-email limit, and much better than letting them guess.

The IP address is the one the server sees (request.client.host). Behind a load balancer (AWS,
Phase 5), uvicorn must be told to trust its X-Forwarded-For header, or every caller would share
the balancer's address.
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


def _check(db: DB, kind: str, column, value: str, limit: int, window: timedelta) -> None:
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


def check_login(db: DB, email: str, ip: str) -> None:
    s = get_settings()
    window = timedelta(minutes=s.login_window_minutes)
    _check(db, "login_failed", AuthAttempt.email, email, s.login_max_failures, window)
    _check(db, "login_failed", AuthAttempt.ip, ip, s.login_max_failures_per_ip, window)


def clear_login_failures(db: DB, email: str) -> None:
    """After a successful login: that email starts again from zero (the caller commits)."""
    db.execute(delete(AuthAttempt).where(AuthAttempt.kind == "login_failed", AuthAttempt.email == email))


def check_register(db: DB, ip: str) -> None:
    _check(db, "register", AuthAttempt.ip, ip, get_settings().register_max_per_hour, timedelta(hours=1))
