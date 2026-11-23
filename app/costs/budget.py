"""Budgets, limits and quotas: adds up the usage table and decides whether a new session may start.

Used by app/api/sessions.py (which kind a new session is, before it starts, before its questions
are planned, before a brief revision) and by GET /usage (app/api/usage.py, the spend and what is
left, shown on the web page).

The rules, all set in .env (app/config.py):
- Budgets, for all users together: the month (BUDGET_MONTHLY_USD, $10), the day
  (BUDGET_DAILY_USD, $1.50), and each provider for the month (BUDGET_CLAUDE_USD, ...). When one
  is used up, check_budget() refuses new sessions with 503, and the page says when it resets.
  An interview already running is never stopped: its answers, scores and feedback still go
  through, so nobody loses the interview they are in the middle of.
- The practice budget (Phase 4.7): practices together may spend BUDGET_PRACTICE_MONTHLY_USD ($3)
  a month and BUDGET_PRACTICE_DAILY_USD ($0.75) a day, inside the global budget, so sign-ups
  trying the app can't use up what the interviews need.
- Quotas, per account (Phase 4.7): users.practice_quota (1) and users.interview_quota (2), in
  all, never reset by themselves (manage_users.py grant adds more). A new session is a practice
  while one is left, then an interview (session_kind). Admins have no quota.
- Per user: at most INTERVIEWS_PER_DAY (4) sessions per UTC day.
  A session counts (for the quotas and the daily limit) once its questions are planned
  (sessions.questions_started_at), so writing and revising a brief doesn't use one up.
- At BUDGET_WARN_RATIO (80%) of any budget, status() says so; the page shows it to the admin.

Months and days are UTC calendar months and days, like the providers' own billing.
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from app.config import get_settings
from app.db.models import AccessRequest, Interview, Session, Usage, User

PROVIDERS = ("claude", "elevenlabs", "deepgram", "voyage")
KINDS = ("practice", "interview")


def _starts() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now.replace(hour=0, minute=0, second=0, microsecond=0)


def _spent(db: DB, since: datetime, **where) -> float:
    stmt = select(func.coalesce(func.sum(Usage.cost_usd), 0)).where(Usage.created_at >= since)
    for column, value in where.items():
        stmt = stmt.where(getattr(Usage, column) == value)
    return float(db.scalar(stmt))


def _spent_on_practices(db: DB, since: datetime) -> float:
    return float(db.scalar(
        select(func.coalesce(func.sum(Usage.cost_usd), 0))
        .join(Session, Usage.session_id == Session.id)
        .where(Usage.created_at >= since, Session.kind == "practice")
    ))


def status(db: DB) -> dict:
    """Spend against every budget, for the month and the day, all users together."""
    s = get_settings()
    month, day = _starts()
    rows = [
        {"name": "month", "spent": _spent(db, month), "budget": s.budget_monthly_usd},
        {"name": "today", "spent": _spent(db, day), "budget": s.budget_daily_usd},
    ] + [
        {"name": p, "spent": _spent(db, month, provider=p), "budget": getattr(s, f"budget_{p}_usd")}
        for p in PROVIDERS
    ] + [
        {"name": "practices this month", "spent": _spent_on_practices(db, month), "budget": s.budget_practice_monthly_usd},
        {"name": "practices today", "spent": _spent_on_practices(db, day), "budget": s.budget_practice_daily_usd},
    ]
    for r in rows:
        r["spent"] = round(r["spent"], 4)
        r["ratio"] = round(r["spent"] / r["budget"], 3) if r["budget"] > 0 else 1.0
    return {
        "budgets": rows,
        "warning": any(r["ratio"] >= s.budget_warn_ratio for r in rows),
        "exceeded": [r["name"] for r in rows if r["ratio"] >= 1],
    }


def check_budget(db: DB, kind: str = "interview") -> None:
    """503 if a budget this kind of session depends on is used up."""
    exceeded = [n for n in status(db)["exceeded"] if kind == "practice" or not n.startswith("practices")]
    if not exceeded:
        return
    daily = all(n in ("today", "practices today") for n in exceeded)
    when = "tomorrow (UTC)" if daily else "at the start of next month"
    what = "budget for practices" if all(n.startswith("practices") for n in exceeded) else "app's budget"
    raise HTTPException(503, f"The {what} is used up ({', '.join(exceeded)}), so no new "
                             f"{'practice' if kind == 'practice' else 'interview'} can start until {when}. "
                             "An interview already running can still finish.")


# ---------- quotas ----------

def used(db: DB, user: User, kind: str) -> int:
    """Sessions of this kind whose questions were planned (they count against the quota)."""
    return db.scalar(
        select(func.count()).select_from(Session).join(Interview, Session.interview_id == Interview.id)
        .where(Interview.user_id == user.id, Session.kind == kind, Session.questions_started_at.is_not(None))
    )


def remaining(db: DB, user: User) -> dict:
    """{"practice": n, "interview": n} left for this user, or None for an admin (no quota)."""
    if user.is_admin:
        return {"practice": None, "interview": None}
    return {
        "practice": max(0, user.practice_quota - used(db, user, "practice")),
        "interview": max(0, user.interview_quota - used(db, user, "interview")),
    }


def session_kind(db: DB, user: User) -> str:
    """What a new session will be: the practice while one is left, else an interview."""
    return "practice" if remaining(db, user)["practice"] else "interview"


NO_MORE = ("You have used your practice and your {n} interviews. Use \"Request more\" on the setup "
           "page to ask for more.")


def check_quota(db: DB, user: User, kind: str) -> None:
    """429 if this user has no session of this kind left."""
    left = remaining(db, user)[kind]
    if left is None or left > 0:
        return
    if kind == "practice":
        raise HTTPException(429, "Your practice is already used: start a new session from the setup page, "
                                 "it will be an interview.")
    raise HTTPException(429, NO_MORE.format(n=user.interview_quota))


def interviews_today(db: DB, user: User) -> int:
    _, day = _starts()
    return db.scalar(
        select(func.count()).select_from(Session).join(Interview, Session.interview_id == Interview.id)
        .where(Interview.user_id == user.id, Session.questions_started_at >= day)
    )


def check_can_start_interview(db: DB, user: User, kind: str = "interview") -> None:
    """Before the questions are planned: budgets (503), this user's quota, then their sessions today (429)."""
    check_budget(db, kind)
    check_quota(db, user, kind)
    limit = get_settings().interviews_per_day
    if interviews_today(db, user) >= limit:
        raise HTTPException(429, f"You have done {limit} interviews today, the daily limit. "
                                 "You can start a new one tomorrow (UTC); your briefs are kept.")


def pending_request(db: DB, user: User) -> AccessRequest | None:
    return db.scalar(select(AccessRequest).where(AccessRequest.user_id == user.id, AccessRequest.handled_at.is_(None))
                     .order_by(AccessRequest.created_at.desc()))


def user_status(db: DB, user: User) -> dict:
    month, day = _starts()
    left = remaining(db, user)
    request = pending_request(db, user)
    return {
        "month_usd": round(_spent(db, month, user_id=user.id), 4),
        "today_usd": round(_spent(db, day, user_id=user.id), 4),
        "interviews_today": interviews_today(db, user),
        "interviews_per_day": get_settings().interviews_per_day,
        "practice_left": left["practice"],
        "interviews_left": left["interview"],
        "next_session": session_kind(db, user),
        "request_pending_since": request.created_at.isoformat() if request else None,
    }
