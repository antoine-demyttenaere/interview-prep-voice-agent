"""Admin tasks on accounts, from the command line (there is no admin web page).

    docker compose exec api python scripts/manage_users.py list            accounts, with their data
    docker compose exec api python scripts/manage_users.py disable EMAIL   can't log in; logged out now
    docker compose exec api python scripts/manage_users.py enable EMAIL    can log in again
    docker compose exec api python scripts/manage_users.py delete EMAIL    the account and all its data
    docker compose exec api python scripts/manage_users.py requests        pending "Request more" requests
    docker compose exec api python scripts/manage_users.py grant EMAIL --interviews 2 [--practices 1]

- list shows each account's role, status, sign-up and first-login (email verified) dates, and
  how many interviews and documents it has.
- disable keeps the account and its data, but logs it out everywhere at once (its logins are
  deleted) and refuses new logins: no login code is even sent. Use it to pause someone's access.
- delete asks you to type the email again, then removes everything (app/auth/account.py), as
  "Delete my account" does. It can't be undone.
- requests lists who asked for more (the page's "Request more", Phase 4.7), with their message.
- grant adds practices and / or interviews to an account's quota (they never reset by
  themselves), closes its pending requests and emails the person (app/api/usage.py grant_more).

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
from app.api.usage import grant_more  # noqa: E402
from app.costs import budget  # noqa: E402
from app.db.models import AccessRequest, Document, LoginSession, User  # noqa: E402
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
    documents = dict(db.execute(select(Document.user_id, func.count()).group_by(Document.user_id)).all())
    print(f"{'email':40} {'role':6} {'status':9} {'created':10} {'verified':10} {'left (p/i)':>10} {'documents':>9}")
    for u in db.scalars(select(User).order_by(User.created_at)):
        status = "disabled" if u.disabled_at else "active"
        left = budget.remaining(db, u)
        quota = "-" if u.is_admin else f"{left['practice']}/{left['interview']}"
        print(f"{u.email:40} {'admin' if u.is_admin else 'user':6} {status:9} {_day(u.created_at):10} "
              f"{_day(u.email_verified_at):10} {quota:>10} {documents.get(u.id, 0):>9}")


def list_requests(db: DB) -> None:
    rows = db.execute(select(AccessRequest, User.email).join(User, AccessRequest.user_id == User.id)
                      .where(AccessRequest.handled_at.is_(None)).order_by(AccessRequest.created_at)).all()
    if not rows:
        print("No pending requests.")
    for r, email in rows:
        print(f"{_day(r.created_at)}  {email}: {r.message or '(no message)'}")
        print(f"            grant with: manage_users.py grant {email} --interviews 2")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage accounts and their quotas.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="accounts, with what they have left and how many documents")
    sub.add_parser("requests", help="pending requests for more interviews")
    grant = sub.add_parser("grant", help="add practices / interviews to an account and email them")
    grant.add_argument("email")
    grant.add_argument("--interviews", type=int, default=0)
    grant.add_argument("--practices", type=int, default=0)
    for name, text in [("disable", "refuse logins and log out everywhere"), ("enable", "allow logins again"),
                       ("delete", "delete the account and all its data")]:
        sub.add_parser(name, help=text).add_argument("email")
    args = parser.parse_args()

    with DB(get_engine()) as db:
        if args.cmd == "list":
            list_users(db)
        elif args.cmd == "requests":
            list_requests(db)
        elif args.cmd == "grant":
            if args.interviews < 0 or args.practices < 0 or not (args.interviews or args.practices):
                sys.exit("Give --interviews N and / or --practices N (positive numbers).")
            user = _user(db, args.email)
            closed = grant_more(db, user, practices=args.practices, interviews=args.interviews)
            left = budget.remaining(db, user)
            print(f"{user.email} now has {left['practice']} practice(s) and {left['interview']} interview(s) left"
                  f"{f'; {closed} request(s) closed' if closed else ''}. They were emailed.")
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
