"""GET /usage: what you have spent and have left; POST /access-requests: "Request more".

The web page (web/app.js) calls GET /usage after logging in and after each session, to show in
the header what is left (an admin sees the month's budget instead), to know the recording limit, and to show the
"Request more" box once nothing is left. The numbers come from app/costs/budget.py.

GET /usage returns
- you:     your sessions today out of the daily limit, your practice and interviews left (None
           for an admin: no quota), what your next session will be (practice or interview), and
           since when a request of yours is pending. Only an admin also gets month_usd and
           today_usd (their own spend): money is the operator's business, not the users'.
- limits:  the per-session limits the page needs (recording length, spoken question length, questions)
- budget:  only for an admin account: every budget (month, today, each provider, practices) with
           what is spent, the ratio, "warning" at 80% and which are used up. Other users don't see
           the global numbers, only their own.

POST /access-requests {message} (Phase 4.7): once your practice and interviews are used, asks for
more. One request can be pending at a time. It saves an access_requests row and emails the admins
(app/mail); an admin grants more with `scripts/manage_users.py grant`, which emails you back.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.auth.deps import current_user
from app.config import get_settings
from app.costs import budget
from app.db.models import AccessRequest, User
from app.db.session import get_db
from app.mail import get_mailer

router = APIRouter(tags=["usage"])


@router.get("/usage")
def get_usage(db: DB = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = get_settings()
    you = budget.user_status(db, user)
    if not user.is_admin:
        you.pop("month_usd"), you.pop("today_usd")
    return {
        "you": you,
        "limits": {
            "max_recording_seconds": s.max_recording_seconds,
            "max_spoken_chars": s.max_spoken_chars,
            "max_questions": s.max_questions,
            "practice_questions": s.practice_questions,
            "brief_revisions_max": s.brief_revisions_max,
        },
        "budget": budget.status(db) if user.is_admin else None,
    }


class AccessRequestIn(BaseModel):
    message: str = Field("", max_length=1000)


@router.post("/access-requests", status_code=201)
def request_more(body: AccessRequestIn, db: DB = Depends(get_db), user: User = Depends(current_user)) -> dict:
    left = budget.remaining(db, user)
    if left["interview"] is None or left["practice"] or left["interview"]:
        raise HTTPException(409, "You still have sessions left: no need to ask yet.")
    if budget.pending_request(db, user):
        raise HTTPException(409, "Your request is already sent: you'll get an email when it's granted.")

    request = AccessRequest(user_id=user.id, message=body.message.strip())
    db.add(request)
    db.commit()
    admins = db.scalars(select(User.email).where(User.is_admin, User.disabled_at.is_(None))).all()
    text = (f"{user.email} asks for more interviews.\n\nTheir message: {request.message or '(none)'}\n\n"
            f"To grant two more:\n  docker compose exec api python scripts/manage_users.py grant {user.email} --interviews 2")
    for admin in admins:
        get_mailer().send(admin, f"Interview Practice: {user.email} asks for more interviews", text)
    return {"detail": "Your request is sent. You'll get an email when more interviews are added."}


def grant_more(db: DB, user: User, practices: int = 0, interviews: int = 0) -> int:
    """Add to this user's quotas, close their pending requests, and email them. Returns how many
    requests were closed. Used by scripts/manage_users.py grant."""
    user.practice_quota += practices
    user.interview_quota += interviews
    pending = db.scalars(select(AccessRequest).where(AccessRequest.user_id == user.id,
                                                      AccessRequest.handled_at.is_(None))).all()
    for r in pending:
        r.handled_at, r.practices_added, r.interviews_added = datetime.now(timezone.utc), practices, interviews
    db.commit()
    added = " and ".join(f"{n} {word}{'s' if n > 1 else ''}" for n, word in
                         ((interviews, "interview"), (practices, "practice")) if n)
    get_mailer().send(user.email, "Interview Practice: more interviews for you",
                      f"Good news: {added} {'were' if (practices + interviews) > 1 else 'was'} added to your account. "
                      "Log in to Interview Practice to use them.")
    return len(pending)
