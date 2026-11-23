"""Budgets and limits: adds up the usage table and decides whether a new interview may start.

Used by app/api/sessions.py (before a session starts, before the questions are planned, before a
brief revision) and by GET /usage (app/api/usage.py, the spend shown on the web page).

The rules, all set in .env (app/config.py):
- Budgets, for all users together: the month (BUDGET_MONTHLY_USD, $10), the day
  (BUDGET_DAILY_USD, $1.50), and each provider for the month (BUDGET_CLAUDE_USD, ...). When one
  is used up, check_budget() refuses new sessions with 503, and the page says when it resets.
  An interview already running is never stopped: its answers, scores and feedback still go
  through, so nobody loses the interview they are in the middle of.
- Per user: at most INTERVIEWS_PER_DAY (4) interviews per UTC day. An interview counts once
  its questions are planned (sessions.questions_started_at), so writing and revising a brief
  doesn't use one up.
- At BUDGET_WARN_RATIO (80%) of any budget, status() says so; the page shows it to the admin.

Months and days are UTC calendar months and days, like the providers' own billing.
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DB

from app.config import get_settings
from app.db.models import Interview, Session, Usage, User

PROVIDERS = ("claude", "elevenlabs", "deepgram", "voyage")


def _starts() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now.replace(hour=0, minute=0, second=0, microsecond=0)


def _spent(db: DB, since: datetime, **where) -> float:
    stmt = select(func.coalesce(func.sum(Usage.cost_usd), 0)).where(Usage.created_at >= since)
    for column, value in where.items():
        stmt = stmt.where(getattr(Usage, column) == value)
    return float(db.scalar(stmt))


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
    ]
    for r in rows:
        r["spent"] = round(r["spent"], 4)
        r["ratio"] = round(r["spent"] / r["budget"], 3) if r["budget"] > 0 else 1.0
    return {
        "budgets": rows,
        "warning": any(r["ratio"] >= s.budget_warn_ratio for r in rows),
        "exceeded": [r["name"] for r in rows if r["ratio"] >= 1],
    }


def check_budget(db: DB) -> None:
    """503 if any budget is used up: no new session, no new interview."""
    exceeded = status(db)["exceeded"]
    if not exceeded:
        return
    when = "tomorrow (UTC)" if exceeded == ["today"] else "at the start of next month"
    raise HTTPException(503, f"The practice budget is used up ({', '.join(exceeded)}), so no new interview "
                             f"can start until {when}. An interview already running can still finish.")


def interviews_today(db: DB, user: User) -> int:
    _, day = _starts()
    return db.scalar(
        select(func.count()).select_from(Session).join(Interview, Session.interview_id == Interview.id)
        .where(Interview.user_id == user.id, Session.questions_started_at >= day)
    )


def check_can_start_interview(db: DB, user: User) -> None:
    """Before the questions are planned: the budgets, then this user's interviews today (429)."""
    check_budget(db)
    limit = get_settings().interviews_per_day
    if interviews_today(db, user) >= limit:
        raise HTTPException(429, f"You have done {limit} interviews today, the daily limit. "
                                 "You can start a new one tomorrow (UTC); your briefs are kept.")


def user_status(db: DB, user: User) -> dict:
    month, day = _starts()
    return {
        "month_usd": round(_spent(db, month, user_id=user.id), 4),
        "today_usd": round(_spent(db, day, user_id=user.id), 4),
        "interviews_today": interviews_today(db, user),
        "interviews_per_day": get_settings().interviews_per_day,
    }
