"""GET /usage: what you have spent, your limits, and (for the admin) the budgets.

The web page (web/app.js) calls it after logging in and after each interview, to show the
month's spend in the header and to know the recording limit. The numbers come from the usage
table, through app/costs/budget.py.

- you:     your spend this month and today (US dollars), and your interviews today out of the limit
- limits:  the per-session limits the page needs (recording length, spoken question length, questions)
- budget:  only for an admin account: every budget (month, today, each provider) with what is
           spent, the ratio, "warning" at 80% and which are used up. Other users don't see the
           global numbers, only their own.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DB

from app.auth.deps import current_user
from app.config import get_settings
from app.costs import budget
from app.db.models import User
from app.db.session import get_db

router = APIRouter(tags=["usage"])


@router.get("/usage")
def get_usage(db: DB = Depends(get_db), user: User = Depends(current_user)) -> dict:
    s = get_settings()
    return {
        "you": budget.user_status(db, user),
        "limits": {
            "max_recording_seconds": s.max_recording_seconds,
            "max_spoken_chars": s.max_spoken_chars,
            "max_questions": s.max_questions,
            "brief_revisions_max": s.brief_revisions_max,
        },
        "budget": budget.status(db) if user.is_admin else None,
    }
