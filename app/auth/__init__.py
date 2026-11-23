"""Accounts: passwords, the login cookie, and who is calling the API (Phase 4.6).

- passwords.py  hashes and checks passwords (Argon2)
- tokens.py     makes the random login token kept in the cookie, and its hash for the database
- deps.py       current_user: the FastAPI dependency that every protected route runs first
- routes.py     POST /auth/login, POST /auth/logout, GET /auth/me

app/main.py mounts routes.py and puts current_user in front of the interview and session
routes, so they answer 401 to anyone who isn't logged in. scripts/create_user.py creates an
account from the command line.
"""
