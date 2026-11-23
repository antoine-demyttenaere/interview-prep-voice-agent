"""The account routes: log in, log out, and "who am I?".

- POST /auth/login   {email, password} -> sets the "login" cookie, returns {email, is_admin}
- POST /auth/logout  deletes this login (the cookie stops working at once) and clears the cookie
- GET  /auth/me      {email, is_admin} of the logged-in user, or 401

Mounted by app/main.py. Uses passwords.py, tokens.py and deps.py next to it, and the users and
login_sessions tables (app/db/models.py). The web page (web/app.js) and the terminal client
(scripts/interview_cli.py) both call these routes. Accounts are created with
scripts/create_user.py for now; registration with an invite code comes in Phase 4.6 step 3.

The cookie, in short: the browser stores it and sends it back with every request to this
server. It is
- HttpOnly: page scripts can't read it, so injected JavaScript can't steal it;
- SameSite=Lax: other websites can't make your browser send it with their forms or scripts;
- Secure when COOKIE_SECURE=true: sent over HTTPS only (turn it on behind HTTPS, on AWS).

A wrong email and a wrong password get the same answer, so nobody can find out who has an account.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DB

from app.auth.deps import current_login, current_user
from app.auth.passwords import check_password, hash_password, needs_rehash
from app.auth.tokens import COOKIE_NAME, hash_token, new_token
from app.config import get_settings
from app.db.models import LoginSession, User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["accounts"])

WRONG_LOGIN = "Wrong email or password."


class LoginIn(BaseModel):
    email: str
    password: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_out(user: User) -> dict:
    return {"email": user.email, "is_admin": user.is_admin}


@router.post("/login")
def login(body: LoginIn, response: Response, db: DB = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == normalize_email(body.email)))
    if not check_password(user.password_hash if user else None, body.password) or user.disabled_at:
        raise HTTPException(401, WRONG_LOGIN)

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    now = datetime.now(timezone.utc)
    settings = get_settings()
    token = new_token()
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id, LoginSession.expires_at <= now))
    db.add(LoginSession(user_id=user.id, token_hash=hash_token(token),
                        expires_at=now + timedelta(days=settings.login_days)))
    db.commit()

    response.set_cookie(
        COOKIE_NAME, token,
        max_age=settings.login_days * 24 * 3600,
        httponly=True, samesite="lax", secure=settings.cookie_secure, path="/",
    )
    return user_out(user)


@router.post("/logout", status_code=204)
def logout(response: Response, login: LoginSession = Depends(current_login), db: DB = Depends(get_db)) -> None:
    db.delete(login)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax",
                           secure=get_settings().cookie_secure)


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return user_out(user)
