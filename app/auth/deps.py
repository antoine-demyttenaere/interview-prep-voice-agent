"""current_user: the FastAPI dependency that tells a route who is calling, or answers 401.

app/main.py adds it in front of every interview and session route
(`include_router(..., dependencies=[Depends(current_user)])`), and app/auth/routes.py uses it
for /auth/me and /auth/logout. A route that needs the user object declares
`user: User = Depends(current_user)`.

How it decides, on every request:
1. Read the "login" cookie. No cookie: 401.
2. Hash it and look for that hash in login_sessions. Not found, expired, or the account is
   disabled: 401.
3. Otherwise return the User. last_seen_at is refreshed at most every 10 minutes, so a busy
   page doesn't write to the database on every click.

401 ("Unauthorized") means "log in first"; the web page shows its login screen when it gets one.
Tests replace this dependency with a fixed test user (tests/conftest.py), except in
tests/test_auth.py, which tests the real thing.
"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.auth.tokens import COOKIE_NAME, hash_token
from app.db.models import LoginSession, User
from app.db.session import get_db

SEEN_EVERY = timedelta(minutes=10)


def current_login(request: Request, db: DB = Depends(get_db)) -> LoginSession:
    """The valid login behind this request's cookie, or a 401."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        now = datetime.now(timezone.utc)
        login = db.scalar(
            select(LoginSession)
            .join(User)
            .where(
                LoginSession.token_hash == hash_token(token),
                LoginSession.expires_at > now,
                User.disabled_at.is_(None),
            )
        )
        if login:
            if now - login.last_seen_at > SEEN_EVERY:
                login.last_seen_at = now
                db.commit()
            return login
    raise HTTPException(401, "Please log in.")


def current_user(login: LoginSession = Depends(current_login)) -> User:
    return login.user
