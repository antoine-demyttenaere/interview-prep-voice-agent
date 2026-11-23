"""Accounts: passwords, the login cookie, who is calling the API, and what protects it (Phase 4.6).

- passwords.py  hashes and checks passwords (Argon2)
- tokens.py     makes the random login token kept in the cookie, and its hash for the database
- invites.py    one-use invite codes for one email: make one, check one at registration
- deps.py       current_user: the FastAPI dependency that every protected route runs first
- routes.py     /auth/register, /auth/login, /auth/logout, /auth/me, /auth/password
- ownership.py  owned_interview / owned_document / owned_session: a row only if it's yours, else 404
- limits.py     guessing limits on login and registration (429)
- origin.py     the middleware that refuses changes sent from another website's page (403)
- account.py    deletes an account and everything it owns

app/main.py mounts routes.py, adds the origin middleware, and puts current_user in front of the
interview and session routes, so they answer 401 to anyone who isn't logged in. The scripts
create_user.py, create_invite.py and manage_users.py do the admin work from the command line.
"""
