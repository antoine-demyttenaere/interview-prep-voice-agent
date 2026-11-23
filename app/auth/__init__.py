"""Accounts: login codes by email, the login cookie, who is calling the API, and what protects it.

- codes.py      the 6-digit codes sent by email: make one, check one (stored as an HMAC)
- tokens.py     makes the random login token kept in the cookie, and its hash for the database
- deps.py       current_user: the FastAPI dependency that every protected route runs first
- routes.py     /auth/start, /auth/verify, /auth/logout, /auth/me, /auth/delete-code
- ownership.py  owned_interview / owned_document / owned_session: a row only if it's yours, else 404
- limits.py     limits on codes sent and wrong codes (429)
- origin.py     the middleware that refuses changes sent from another website's page (403)
- account.py    deletes an account and everything it owns

There are no passwords (Phase 4.7): you log in, and sign up the first time, by typing back a
code sent to your email (app/mail sends it). app/main.py mounts routes.py, adds the origin
middleware, and puts current_user in front of the interview and session routes, so they answer
401 to anyone who isn't logged in. scripts/create_user.py and manage_users.py do the admin work.
"""
