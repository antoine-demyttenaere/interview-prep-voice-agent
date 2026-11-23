"""Admin tasks on accounts and invites, from the command line (there is no admin web page).

    docker compose exec api python scripts/manage_users.py list                 accounts, with their data
    docker compose exec api python scripts/manage_users.py invites              invites: pending, used, expired
    docker compose exec api python scripts/manage_users.py disable EMAIL        can't log in; logged out now
    docker compose exec api python scripts/manage_users.py enable EMAIL         can log in again
    docker compose exec api python scripts/manage_users.py reset-password EMAIL prints a temporary password
    docker compose exec api python scripts/manage_users.py delete EMAIL         the account and all its data

- disable keeps the account and its data, but logs it out everywhere at once (its logins are
  deleted) and refuses new logins. Use it to pause someone's access.
- reset-password, for someone who forgot theirs: it prints a random temporary password to send
  them, logs them out everywhere, and they change it after logging in ("Your account" box at the
  bottom of the setup page, POST /auth/password).
- delete asks you to type the email again, then removes everything (app/auth/account.py), as
  "Delete my account" does. It can't be undone.

Uses the same tables as the app (app/db/models.py); invite codes are made with create_invite.py.
"""

import argparse
import os
import secrets
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session as DB  # noqa: E402

from app.auth.account import delete_account  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.auth.routes import normalize_email  # noqa: E402
from app.db.models import Document, Interview, Invite, LoginSession, User  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.graph.checkpointer import open_checkpointer  # noqa: E402


def _user(db: DB, email: str) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if not user:
        sys.exit(f"No account for {email}.")
    return user


def _day(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "-"


def list_users(db: DB) -> None:
    interviews = dict(db.execute(select(Interview.user_id, func.count()).group_by(Interview.user_id)).all())
    documents = dict(db.execute(select(Document.user_id, func.count()).group_by(Document.user_id)).all())
    print(f"{'email':40} {'role':6} {'status':9} {'created':10} {'interviews':>10} {'documents':>9}")
    for u in db.scalars(select(User).order_by(User.created_at)):
        status = "disabled" if u.disabled_at else "active"
        print(f"{u.email:40} {'admin' if u.is_admin else 'user':6} {status:9} {_day(u.created_at):10} "
              f"{interviews.get(u.id, 0):>10} {documents.get(u.id, 0):>9}")


def list_invites(db: DB) -> None:
    now = datetime.now(timezone.utc)
    print(f"{'email':40} {'status':8} {'created':10} {'expires':10} note")
    for i in db.scalars(select(Invite).order_by(Invite.created_at)):
        status = "used" if i.used_at else "expired" if i.expires_at <= now else "pending"
        print(f"{i.email:40} {status:8} {_day(i.created_at):10} {_day(i.expires_at):10} {i.note}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage accounts and invites.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="accounts, with how many interviews and documents each has")
    sub.add_parser("invites", help="invites and whether they are pending, used or expired")
    for name, text in [("disable", "refuse logins and log out everywhere"), ("enable", "allow logins again"),
                       ("reset-password", "set and print a temporary password"),
                       ("delete", "delete the account and all its data")]:
        sub.add_parser(name, help=text).add_argument("email")
    args = parser.parse_args()

    with DB(get_engine()) as db:
        if args.cmd == "list":
            list_users(db)
        elif args.cmd == "invites":
            list_invites(db)
        elif args.cmd == "disable":
            user = _user(db, args.email)
            user.disabled_at = user.disabled_at or datetime.now(timezone.utc)
            db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            db.commit()
            print(f"{user.email} is disabled and logged out everywhere. Undo with: enable {user.email}")
        elif args.cmd == "enable":
            user = _user(db, args.email)
            user.disabled_at = None
            db.commit()
            print(f"{user.email} can log in again.")
        elif args.cmd == "reset-password":
            user = _user(db, args.email)
            temporary = secrets.token_urlsafe(12)  # 16 random characters
            user.password_hash = hash_password(temporary)
            db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            db.commit()
            print(f"Temporary password for {user.email} (shown once):\n\n    {temporary}\n")
            print("Send it to them; they can change it in 'Your account' at the bottom of the setup page.")
        elif args.cmd == "delete":
            user = _user(db, args.email)
            if input(f"This deletes {user.email} and ALL their data, for good. Type the email to confirm: ").strip().lower() != user.email:
                sys.exit("Not confirmed: nothing was deleted.")
            pool, saver = open_checkpointer()
            try:
                sessions = delete_account(db, user.id, saver)
            finally:
                pool.close()
            print(f"Deleted {user.email}, with {sessions} session(s) and all their documents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
