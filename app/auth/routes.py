"""The account routes: log in (or sign up) with a code sent by email, log out, and "who am I?".

- POST /auth/start        {email} -> emails a 6-digit login code; always the same answer (202)
- POST /auth/verify       {email, code} -> checks it, creates the account the first time, logs in
                          (sets the "login" cookie), returns {email, is_admin, new_account}
- POST /auth/password-login {email, password} -> admins only (Phase 4.8): same cookie as a code login
- POST /auth/logout       deletes this login (the cookie stops working at once) and clears it
- GET  /auth/me           {email, is_admin} of the logged-in user, or 401
- POST /auth/delete-code  emails a code to confirm deleting your account
- DELETE /auth/me         {code} -> deletes your account and all your data (app/auth/account.py)

Mounted by app/main.py. Uses codes.py (the codes), limits.py (too many attempts: 429), deps.py
(who is logged in) and app/mail (sending the email). The web page (web/app.js) and the terminal
client (scripts/interview_cli.py) both call these routes.

Most people have no password (Phase 4.7): typing the code back proves the inbox is yours, so the
same two steps sign up a new person and log in an existing one. An admin can also set a password
(Phase 4.8, scripts/manage_users.py set-password) and log in with it; a wrong password, a
non-admin email and an admin without a password all get the same answer, so the form doesn't
reveal who is an admin. The code login keeps working for admins too, as a fallback. Anyone with a real inbox can sign up;
quotas and budgets (app/costs/) decide what they can use.

What "send me a code" answers never depends on the email: a new address, an existing account
or a disabled one (which gets no email) all get "check your inbox". So nobody can find out who
has an account. A wrong, expired or used code always gets the same message too.

The cookie, in short: the browser stores it and sends it back with every request to this
server. It is
- HttpOnly: page scripts can't read it, so injected JavaScript can't steal it;
- SameSite=Lax: other websites can't make your browser send it with their forms or scripts;
- Secure when COOKIE_SECURE=true: sent over HTTPS only (turn it on behind HTTPS, on AWS).
"""

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DB

from app.auth import limits
from app.auth.account import delete_account
from app.auth.codes import check_code, issue_code
from app.auth.deps import current_login, current_user
from app.auth.passwords import check_password, hash_password, needs_rehash
from app.auth.tokens import COOKIE_NAME, hash_token, new_token
from app.config import get_settings
from app.db.models import LoginSession, User
from app.db.session import get_db
from app.mail import get_mailer

router = APIRouter(prefix="/auth", tags=["accounts"])

CODE_SENT = "If this email can be used, a code is on its way. Check your inbox (and spam folder)."
WRONG_CODE = "This code is wrong or has expired. Check the latest email, or ask for a new code."
WRONG_PASSWORD = "Wrong email or password. (Password login is for admins; everyone else uses a code.)"
EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")  # something@something.something


def normalize_email(email: str) -> str:
    return email.strip().lower()


def user_out(user: User) -> dict:
    return {"email": user.email, "is_admin": user.is_admin}


def _valid_email(email: str) -> str:
    email = normalize_email(email)
    if not EMAIL_SHAPE.match(email) or len(email) > 320:
        raise HTTPException(422, "Please enter a valid email address.")
    return email


def _send_code(db: DB, email: str, purpose: str) -> None:
    code = issue_code(db, email, purpose)
    minutes = get_settings().code_minutes
    if purpose == "login":
        subject = f"Your Interview Practice code: {code}"
        text = (f"Your code is {code}\n\nType it on the Interview Practice page to log in. "
                f"It works once, for {minutes} minutes.\n\nIf you didn't ask for it, you can ignore this email.")
    else:
        subject = f"Confirm deleting your Interview Practice account: {code}"
        text = (f"Your code is {code}\n\nType it on the page to delete your account and ALL its data "
                f"(interviews, documents, CV, answers, feedback). It works once, for {minutes} minutes.\n\n"
                "If you didn't ask for this, ignore this email: nothing will be deleted.")
    get_mailer().send(email, subject, text)


def _start_login(db: DB, user: User, response: Response) -> None:
    """Create a login for `user` and send its token as the cookie."""
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


class StartIn(BaseModel):
    email: str


@router.post("/start", status_code=202)
def start(body: StartIn, request: Request, db: DB = Depends(get_db)) -> dict:
    """Send a login code. The account itself is only created once the code comes back."""
    email, ip = _valid_email(body.email), limits.client_ip(request)
    limits.check_code_request(db, email, ip)
    limits.record(db, "code_sent", ip, email)
    user = db.scalar(select(User).where(User.email == email))
    if not (user and user.disabled_at):  # a disabled account gets no email, but the same answer
        _send_code(db, email, "login")
    return {"detail": CODE_SENT}


class VerifyIn(BaseModel):
    email: str
    code: str


@router.post("/verify")
def verify(body: VerifyIn, request: Request, response: Response, db: DB = Depends(get_db)) -> dict:
    email, ip = _valid_email(body.email), limits.client_ip(request)
    limits.check_login(db, email, ip)  # 429 once there were too many wrong codes
    user = db.scalar(select(User).where(User.email == email))
    if (user and user.disabled_at) or not check_code(db, email, "login", body.code):
        limits.record(db, "login_failed", ip, email)
        raise HTTPException(401, WRONG_CODE)

    new_account = user is None
    if new_account:
        user = User(email=email)
        db.add(user)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(timezone.utc)
    db.flush()
    limits.clear_login_failures(db, email)
    _start_login(db, user, response)  # commits the new account and the login together
    return {**user_out(user), "new_account": new_account}


class PasswordLoginIn(BaseModel):
    email: str
    password: str


@router.post("/password-login")
def password_login(body: PasswordLoginIn, request: Request, response: Response, db: DB = Depends(get_db)) -> dict:
    """Admins only: log in with the password set by manage_users.py set-password."""
    email, ip = _valid_email(body.email), limits.client_ip(request)
    limits.check_login(db, email, ip)  # 429 once there were too many wrong attempts
    user = db.scalar(select(User).where(User.email == email))
    stored = user.password_hash if (user and user.is_admin and not user.disabled_at) else None
    if not check_password(stored, body.password):  # same work and answer whatever the reason
        limits.record(db, "login_failed", ip, email)
        raise HTTPException(401, WRONG_PASSWORD)
    if needs_rehash(stored):
        user.password_hash = hash_password(body.password)
    limits.clear_login_failures(db, email)
    _start_login(db, user, response)
    return {**user_out(user), "new_account": False}


@router.post("/logout", status_code=204)
def logout(response: Response, login: LoginSession = Depends(current_login), db: DB = Depends(get_db)) -> None:
    db.delete(login)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax",
                           secure=get_settings().cookie_secure)


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict:
    return user_out(user)


@router.post("/delete-code", status_code=202)
def delete_code(request: Request, user: User = Depends(current_user), db: DB = Depends(get_db)) -> dict:
    """Email a code that confirms deleting the account (it proves the inbox is still yours)."""
    ip = limits.client_ip(request)
    limits.check_code_request(db, user.email, ip)
    limits.record(db, "code_sent", ip, user.email)
    _send_code(db, user.email, "delete_account")
    return {"detail": f"A code is on its way to {user.email}."}


class DeleteAccountIn(BaseModel):
    code: str


@router.delete("/me", status_code=204)
def delete_me(body: DeleteAccountIn, request: Request, response: Response,
              user: User = Depends(current_user), db: DB = Depends(get_db)) -> None:
    """Delete your account and everything in it: interviews, documents, CV, sessions, answers."""
    ip = limits.client_ip(request)
    limits.check_login(db, user.email, ip)
    if not check_code(db, user.email, "delete_account", body.code):
        limits.record(db, "login_failed", ip, user.email)
        raise HTTPException(403, "This code is wrong or has expired: nothing was deleted.")
    delete_account(db, user.id, request.app.state.graph.checkpointer)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax",
                           secure=get_settings().cookie_secure)
