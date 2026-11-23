"""Creates an account from the command line (your own admin account, or a test account).

It asks for the password twice without showing it, hashes it with Argon2
(app/auth/passwords.py) and adds a row to the users table (app/db/models.py). Use it to create
your first account: the web page and the CLI need a login, and registration with an invite code
only comes in Phase 4.6 step 3.

    docker compose exec api python scripts/create_user.py --email you@example.com --admin
    docker compose exec api python scripts/create_user.py --email test@example.com

For a non-interactive run (a script, CI), set NEW_USER_PASSWORD instead of typing it.
The email is stored lowercased. If the account already exists, nothing changes.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session as DB  # noqa: E402

from app.auth.passwords import hash_password, password_problem  # noqa: E402
from app.auth.routes import normalize_email  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import get_engine  # noqa: E402


def ask_password() -> str:
    password = os.environ.get("NEW_USER_PASSWORD")
    if password is None:
        password = getpass.getpass("Password: ")
        if getpass.getpass("Same password again: ") != password:
            sys.exit("The two passwords differ. Nothing was created.")
    problem = password_problem(password)
    if problem:
        sys.exit(problem)
    return password


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an account.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--admin", action="store_true", help="can manage invites and users")
    args = parser.parse_args()

    email = normalize_email(args.email)
    if "@" not in email:
        sys.exit(f"'{args.email}' is not an email address.")

    with DB(get_engine()) as db:
        if db.scalar(select(User).where(User.email == email)):
            print(f"{email} already has an account; nothing changed.")
            return 1
        db.add(User(email=email, password_hash=hash_password(ask_password()), is_admin=args.admin))
        db.commit()
    print(f"Created {'admin ' if args.admin else ''}account {email}. Log in at http://localhost:8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
