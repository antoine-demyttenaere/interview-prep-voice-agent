"""Login tokens: the random value kept in the browser's cookie, and the hash stored for it.

Used by app/auth/routes.py (login creates one) and app/auth/deps.py (each request checks one).

- new_token() returns 32 random bytes from the operating system's secure generator, written as
  text (about 43 characters). Guessing one is impossible in practice.
- Only hash_token(token), a SHA-256 fingerprint, goes into login_sessions.token_hash. If the
  database leaks, the hashes can't be used as cookies. (A fast hash is fine here, unlike for
  passwords: the token is random and long, so there is nothing to guess.)
"""

import hashlib
import secrets

COOKIE_NAME = "login"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
