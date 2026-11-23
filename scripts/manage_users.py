"""Admin tasks on accounts, from the command line (there is no admin web page).

    docker compose exec api python scripts/manage_users.py list            accounts, with their data
    docker compose exec api python scripts/manage_users.py disable EMAIL   can't log in; logged out now
    docker compose exec api python scripts/manage_users.py enable EMAIL    can log in again
    docker compose exec api python scripts/manage_users.py delete EMAIL    the account and all its data

- list shows each account's role, status, sign-up and first-login (email verified) dates, and
  how many interviews and documents it has.
- disable keeps the account and its data, but logs it out everywhere at once (its logins are
  deleted) and refuses new logins: no login code is even sent. Use it to pause someone's access.
- delete asks you to type the email again, then removes everything (app/auth/account.py), as
  "Delete my account" does. It can't be undone.

There are no passwords to reset: everyone logs in with a code sent by email (Phase 4.7). Admin
accounts are made with create_user.py --admin. Uses the same tables as the app (app/db/models.py).
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.orm import Session as DB  # noqa: E402

from app.auth.account import delete_account  # noqa: E402
from app.auth.routes import normalize_email  # noqa: E402
from app.db.models import Document, Interview, LoginSession, User  # noqa: E402
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
    print(f"{'email':40} {'role':6} {'status':9} {'created':10} {'verified':10} {'interviews':>10} {'documents':>9}")
    for u in db.scalars(select(User).order_by(User.created_at)):
        status = "disabled" if u.disabled_at else "active"
        print(f"{u.email:40} {'admin' if u.is_admin else 'user':6} {status:9} {_day(u.created_at):10} "
              f"{_day(u.email_verified_at):10} {interviews.get(u.id, 0):>10} {documents.get(u.id, 0):>9}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage accounts and invites.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="accounts, with how many interviews and documents each has")
    for name, text in [("disable", "refuse logins and log out everywhere"), ("enable", "allow logins again"),
                       ("delete", "delete the account and all its data")]:
        sub.add_parser(name, help=text).add_argument("email")
    args = parser.parse_args()

    with DB(get_engine()) as db:
        if args.cmd == "list":
            list_users(db)
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
