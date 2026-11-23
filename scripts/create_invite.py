"""Creates an invite code for one person, and prints it once (only its hash is stored).

Send the code and the email you typed here to that person. On the login screen they click
"Create an account" and enter that email, a password and the code. The code works once,
only for that email, until it expires. The logic is in app/auth/invites.py; the code is checked
by POST /auth/register (app/auth/routes.py).

    docker compose exec api python scripts/create_invite.py --email marie@example.com --note "Marie, ex-colleague"
    docker compose exec api python scripts/create_invite.py --email marie@example.com --days 3

The code can't be shown again later: if it is lost, make a new one (the old one stays unused
until it expires).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session as DB  # noqa: E402

from app.auth.invites import create_invite  # noqa: E402
from app.auth.routes import EMAIL_SHAPE, normalize_email  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import get_engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a one-use invite code for one email.")
    parser.add_argument("--email", required=True, help="the only address the code will work for")
    parser.add_argument("--days", type=int, default=14, help="days before the code expires (default 14)")
    parser.add_argument("--note", default="", help="who it is for, to remember later")
    args = parser.parse_args()

    email = normalize_email(args.email)
    if not EMAIL_SHAPE.match(email):
        sys.exit(f"'{args.email}' is not an email address.")
    if not 1 <= args.days <= 90:
        sys.exit("--days must be between 1 and 90.")

    with DB(get_engine()) as db:
        if db.scalar(select(User).where(User.email == email)):
            sys.exit(f"{email} already has an account: no invite needed.")
        code = create_invite(db, email, days=args.days, note=args.note[:200])

    print(f"Invite for {email}, valid {args.days} days, one use:\n\n    {code}\n")
    print("Send them this code and the email above. It is not stored: it can't be shown again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
