"""Invite codes: making one for an email address, and checking one at registration.

Used by scripts/create_invite.py (create_invite) and POST /auth/register in routes.py
(find_usable_invite). The invites table is in app/db/models.py (Invite).

- A code looks like K7QM-2XPA-9RTD: 12 characters drawn at random by the operating system's
  secure generator, from 31 letters and digits that can't be confused when read aloud or typed
  (no 0/O, 1/I/L). That is about 59 bits of randomness: far too many to guess.
- Typing is forgiving: lowercase, spaces and missing dashes are fine (normalize_code).
- Only the code's SHA-256 hash is stored, like the login tokens: it is shown once, when made.
- Each code works once, for one email, until it expires (14 days by default).
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.db.models import Invite

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0, 1, I, L, O
GROUPS, GROUP_LENGTH = 3, 4


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LENGTH))
    return "-".join(raw[i:i + GROUP_LENGTH] for i in range(0, len(raw), GROUP_LENGTH))


def normalize_code(code: str) -> str:
    """'k7qm 2xpa-9rtd' -> 'K7QM2XPA9RTD': the form that is hashed."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_code(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode()).hexdigest()


def create_invite(db: DB, email: str, days: int = 14, note: str = "") -> str:
    """Store a new invite for `email` (already lowercased) and return its code, in clear, once."""
    code = new_code()
    db.add(Invite(code_hash=hash_code(code), email=email, note=note,
                  expires_at=datetime.now(timezone.utc) + timedelta(days=days)))
    db.commit()
    return code


def find_usable_invite(db: DB, code: str, email: str) -> Invite | None:
    """The invite behind `code` if it is unused, not expired and made for `email`; else None.

    The row is locked (SELECT ... FOR UPDATE) until the caller commits, so two registrations
    sent at the same moment with the same code can't both succeed.
    """
    return db.scalar(
        select(Invite)
        .where(Invite.code_hash == hash_code(code), Invite.email == email,
               Invite.used_at.is_(None), Invite.expires_at > datetime.now(timezone.utc))
        .with_for_update()
    )
