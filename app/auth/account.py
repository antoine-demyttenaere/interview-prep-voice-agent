"""Deleting an account and everything it owns, used by DELETE /auth/me and scripts/manage_users.py.

What goes, in order:
1. The LangGraph checkpoints of every session of the user's interviews (questions, answers,
   scores, feedback). They live in LangGraph's own tables, linked only by thread_id (no foreign
   key), so they are deleted explicitly with checkpointer.delete_thread().
2. Saved recordings of those sessions in evals/audio/ (only exist with SAVE_AUDIO=true).
3. The user row. The database then deletes the rest by itself (ON DELETE CASCADE, migrations
   0005 and 0006): interviews -> sessions, documents -> chunks, and login_sessions. Invites
   they used keep existing but forget who used them (ON DELETE SET NULL).

What can't be deleted from here: copies already sent to outside services (LangSmith traces,
the AI and speech providers' own logs). The registration form says so.
"""

from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DB

from app.db.models import Interview, Session, User

AUDIO_DIR = Path(__file__).resolve().parents[2] / "evals" / "audio"


def delete_account(db: DB, user_id, checkpointer) -> int:
    """Delete the user and all their data. Returns how many sessions were removed. Commits."""
    sessions = db.scalars(
        select(Session).join(Interview, Session.interview_id == Interview.id).where(Interview.user_id == user_id)
    ).all()
    for session in sessions:
        checkpointer.delete_thread(session.thread_id)
        for recording in AUDIO_DIR.glob(f"{session.id}_*"):
            recording.unlink(missing_ok=True)
    db.execute(delete(User).where(User.id == user_id))  # the database cascades the rest
    db.commit()
    return len(sessions)
