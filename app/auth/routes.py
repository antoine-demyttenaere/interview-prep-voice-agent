"""The account routes: register with an invite code, log in, log out, and "who am I?".

- POST /auth/register {email, password, code} -> creates the account, logs it in (cookie), 201
- POST /auth/login   {email, password} -> sets the "login" cookie, returns {email, is_admin}
- POST /auth/logout  deletes this login (the cookie stops working at once) and clears the cookie
- GET  /auth/me      {email, is_admin} of the logged-in user, or 401
- POST /auth/password {current_password, new_password} -> changes it; your other logins end
- DELETE /auth/me    {password} -> deletes your account and all your data (app/auth/account.py)

Mounted by app/main.py. Uses passwords.py, tokens.py and deps.py next to it, and the users and
login_sessions tables (app/db/models.py). The web page (web/app.js) and the terminal client
(scripts/interview_cli.py) both call these routes.

Registering needs an invite code made for that exact email (scripts/create_invite.py,
app/auth/invites.py). A code that is unknown, expired, already used or made for another email
always gets the same message, so trying codes teaches nothing. Only once the code is right for
this email does the answer say whether the email already has an account. Admin accounts are
only made with scripts/create_user.py --admin, never by registering.

The cookie, in short: the browser stores it and sends it back with every request to this
server. It is
- HttpOnly: page scripts can't read it, so injected JavaScript can't steal it;
- SameSite=Lax: other websites can't make your browser send it with their forms or scripts;
- Secure when COOKIE_SECURE=true: sent over HTTPS only (turn it on behind HTTPS, on AWS).

A wrong email and a wrong password get the same answer, so nobody can find out who has an account.
Login and registration are rate-limited (app/auth/limits.py): too many attempts answer 429.
Changing the password and deleting the account ask for the current password again, so someone
using a browser you left logged in can't do either.
"""

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DB

from app.auth import limits
from app.auth.account import delete_account
from app.auth.deps import current_login, current_user
from app.auth.invites import find_usable_invite
from app.auth.passwords import check_password, hash_password, needs_rehash, password_problem
from app.auth.tokens import COOKIE_NAME, hash_token, new_token
from app.config import get_settings
from app.db.models import LoginSession, User
from app.db.session import get_db

router = APIRouter(prefix="/auth", tags=["accounts"])

WRONG_LOGIN = "Wrong email or password."
BAD_INVITE = "This invite code isn't valid for this email address. Check both, or ask for a new code."
EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")  # something@something.something


class LoginIn(BaseModel):
    email: str
    password: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_out(user: User) -> dict:
    return {"email": user.email, "is_admin": user.is_admin}


def _start_login(db: DB, user: User, response: Response) -> None:
    """Create a login for `user` and send its token as the cookie (used by login and register)."""
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


class RegisterIn(BaseModel):
    email: str
    password: str
    code: str


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: DB = Depends(get_db)) -> dict:
    email = normalize_email(body.email)
    ip = limits.client_ip(request)
    limits.check_register(db, ip)
    limits.record(db, "register", ip, email)
    if not EMAIL_SHAPE.match(email):
        raise HTTPException(422, "Please enter a valid email address.")
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(422, problem)

    invite = find_usable_invite(db, body.code, email)
    if not invite:
        raise HTTPException(400, BAD_INVITE)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "This email already has an account: log in instead.")

    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()  # gives the user its id
    invite.used_at, invite.used_by = datetime.now(timezone.utc), user.id
    _start_login(db, user, response)  # commits the user, the used invite and the login together
    return user_out(user)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DB = Depends(get_db)) -> dict:
    email, ip = normalize_email(body.email), limits.client_ip(request)
    limits.check_login(db, email, ip)  # 429 once there were too many failures
    user = db.scalar(select(User).where(User.email == email))
    if not check_password(user.password_hash if user else None, body.password) or user.disabled_at:
        limits.record(db, "login_failed", ip, email)
        raise HTTPException(401, WRONG_LOGIN)

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    limits.clear_login_failures(db, email)
    _start_login(db, user, response)
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


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/password", status_code=204)
def change_password(body: PasswordChangeIn, login: LoginSession = Depends(current_login),
                    db: DB = Depends(get_db)) -> None:
    """Change your password. Your other logins (other browsers, the CLI) end; this one stays."""
    user = login.user
    if not check_password(user.password_hash, body.current_password):
        raise HTTPException(403, "Your current password is wrong.")
    problem = password_problem(body.new_password)
    if problem:
        raise HTTPException(422, problem)
    user.password_hash = hash_password(body.new_password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id, LoginSession.id != login.id))
    db.commit()


class DeleteAccountIn(BaseModel):
    password: str


@router.delete("/me", status_code=204)
def delete_me(body: DeleteAccountIn, request: Request, response: Response,
              user: User = Depends(current_user), db: DB = Depends(get_db)) -> None:
    """Delete your account and everything in it: interviews, documents, CV, sessions, answers."""
    if not check_password(user.password_hash, body.password):
        raise HTTPException(403, "Your password is wrong: nothing was deleted.")
    delete_account(db, user.id, request.app.state.graph.checkpointer)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax",
                           secure=get_settings().cookie_secure)
