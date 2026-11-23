"""Password hashing: turns a password into an Argon2 hash, and checks a password against one.

Used by app/auth/routes.py (login) and scripts/create_user.py (new accounts).

- A hash is a one-way fingerprint: the database keeps "$argon2id$v=19$m=65536...", and nobody,
  not even us, can turn it back into the password. To check a login we hash the typed password
  the same way and compare.
- Argon2 is slow on purpose (tens of milliseconds and 64 MB of memory per hash), so someone who
  steals the database can only try a few guesses per second per password. Each hash has its own
  random "salt", so two people with the same password get different hashes.
- check_password() spends the same time whether the account exists or not (it checks a dummy
  hash when there is no user), so the response time doesn't reveal which emails have an account.
"""

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 10

_hasher = PasswordHasher()  # the library's recommended settings (Argon2id)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash("not-a-real-password")


def check_password(password_hash: str | None, password: str) -> bool:
    """True if `password` matches the hash. With no hash (unknown email), still does the work."""
    try:
        return _hasher.verify(password_hash or _dummy_hash(), password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with older settings, so login can store a fresh one."""
    return _hasher.check_needs_rehash(password_hash)


def password_problem(password: str) -> str | None:
    """A message if the password is too weak to accept, else None."""
    if len(password) < MIN_LENGTH:
        return f"The password must have at least {MIN_LENGTH} characters."
    return None
