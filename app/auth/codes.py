"""Login codes: makes the 6-digit code sent by email, and checks the one typed back.

Used by app/auth/routes.py: POST /auth/start and /auth/delete-code send one (issue_code),
POST /auth/verify and DELETE /auth/me check one (check_code). The codes are rows of the
email_codes table (app/db/models.py, migration 0010).

- A code is 6 random digits from the operating system's secure generator. It is valid
  CODE_MINUTES (10), for CODE_MAX_TRIES (5) tries, and only once. Asking for a new code makes
  the previous unused ones useless, so only the latest email counts.
- Only an HMAC of the code is stored: a fingerprint computed with a server secret (SECRET_KEY).
  A plain hash wouldn't be enough here: there are only 1,000,000 possible codes, so anyone
  holding the table could hash them all in a second and read every pending code. Without the
  secret, the stored values are useless.
- Each purpose (login, delete_account) has its own codes, so a login code can't delete an account.
- The comparison uses hmac.compare_digest, which takes the same time whatever the input, so
  timing reveals nothing.

If SECRET_KEY isn't set, a random one is made when the server starts (with a warning): fine
locally, but codes sent before a restart stop working. Set it in .env (see .env.example).
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DB

from app.config import get_settings
from app.db.models import EmailCode

log = logging.getLogger(__name__)

PURPOSES = ("login", "delete_account")
_fallback_key = secrets.token_bytes(32)


def _key() -> bytes:
    key = get_settings().secret_key
    if not key:
        log.warning("SECRET_KEY is not set: login codes stop working when the server restarts. Set it in .env.")
        return _fallback_key
    return key.encode()


def _digest(email: str, purpose: str, code: str) -> str:
    # The email and purpose are part of the fingerprint, so a code only works for them.
    return hmac.new(_key(), f"{purpose}|{email}|{code}".encode(), hashlib.sha256).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_code(db: DB, email: str, purpose: str) -> str:
    """Store a new code for this email and purpose (cancelling older ones) and return it. Commits."""
    now = datetime.now(timezone.utc)
    db.execute(update(EmailCode)
               .where(EmailCode.email == email, EmailCode.purpose == purpose, EmailCode.used_at.is_(None))
               .values(used_at=now))
    code = new_code()
    db.add(EmailCode(email=email, purpose=purpose, code_hash=_digest(email, purpose, code),
                     expires_at=now + timedelta(minutes=get_settings().code_minutes)))
    db.commit()
    return code


def check_code(db: DB, email: str, purpose: str, code: str) -> bool:
    """True once for the right code; uses it up. A wrong try counts; too many end the code. Commits."""
    now = datetime.now(timezone.utc)
    row = db.scalar(
        select(EmailCode)
        .where(EmailCode.email == email, EmailCode.purpose == purpose,
               EmailCode.used_at.is_(None), EmailCode.expires_at > now)
        .order_by(EmailCode.created_at.desc())
        .with_for_update()  # two tries at the same moment are counted one after the other
    )
    if row is None:
        return False
    typed = "".join(ch for ch in code if ch.isdigit())
    if hmac.compare_digest(row.code_hash, _digest(email, purpose, typed)):
        row.used_at = now
        db.commit()
        return True
    row.attempts += 1
    if row.attempts >= get_settings().code_max_tries:
        row.used_at = now  # too many wrong tries: this code is finished
    db.commit()
    return False
