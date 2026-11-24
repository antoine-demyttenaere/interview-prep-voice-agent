"""Creates an account from the command line: mainly your own admin account.

Anyone can sign up on the web page with their email (Phase 4.7), but an admin account can only
be made here. With --admin it offers to set a password (Phase 4.8; press Enter to skip), so you
can log in with "Admin? Log in with a password"; without one, or as a fallback, you log in like
everyone else with a code sent by email (locally, printed in `docker compose logs api`). It adds
a row to the users table (app/db/models.py). Set or change the password later with
`scripts/manage_users.py set-password EMAIL`.

    docker compose exec api python scripts/create_user.py --email you@example.com --admin
    docker compose run --rm api python scripts/create_user.py --email you@example.com --admin
        (the second form works even while the server can't start, e.g. before your first account)

The email is stored lowercased. If the account already exists, --admin makes it an admin.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session as DB  # noqa: E402

from app.auth.passwords import hash_password, password_problem  # noqa: E402
from app.auth.routes import EMAIL_SHAPE, normalize_email  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import get_engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an account (or make it an admin).")
    parser.add_argument("--email", required=True)
    parser.add_argument("--admin", action="store_true", help="no quotas; sees the budgets; runs the admin script")
    args = parser.parse_args()

    email = normalize_email(args.email)
    if not EMAIL_SHAPE.match(email):
        sys.exit(f"'{args.email}' is not an email address.")

    with DB(get_engine()) as db:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            if args.admin and not user.is_admin:
                user.is_admin = True
                db.commit()
                print(f"{email} is now an admin.")
            else:
                print(f"{email} already has an account; nothing changed.")
            return 0
        user = User(email=email, is_admin=args.admin)
        if args.admin:
            password = os.environ.get("NEW_PASSWORD")
            if password is None and sys.stdin.isatty():
                password = getpass.getpass("Password for this admin (at least 12 characters; Enter to skip): ")
                if password and getpass.getpass("Same password again: ") != password:
                    sys.exit("The two passwords differ. Nothing was created.")
            if password:
                problem = password_problem(password)
                if problem:
                    sys.exit(problem)
                user.password_hash = hash_password(password)
        has_password = bool(user.password_hash)  # read now: the row's values are dropped after commit
        db.add(user)
        db.commit()
    print(f"Created {'admin ' if args.admin else ''}account {email}.")
    if has_password:
        print("Log in at http://localhost:8000 with \"Admin? Log in with a password\".")
    else:
        print("Log in at http://localhost:8000 with this email: the code is printed in `docker compose logs api`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
