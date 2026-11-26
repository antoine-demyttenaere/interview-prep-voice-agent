"""Cloudflare Turnstile: checks that "Continue" on the login screen was clicked by a person, not a bot.

Used by POST /auth/start (app/auth/routes.py), the route that emails a login code. Without a
check, a bot could make the server send thousands of emails (to strangers), which costs money
and gets the sending domain marked as spam. The per-email and per-IP limits (limits.py) slow one
attacker down; Turnstile stops scripts altogether.

How it works:
- The page (web/app.js) loads Cloudflare's script and shows a small widget in the login form.
  Most people never see a puzzle: Cloudflare looks at the browser and hands the page a token.
- The page sends that token with the email. Here, the server asks Cloudflare whether the token
  is genuine ("siteverify"), with its secret key. Each token works once, for 5 minutes.
- TURNSTILE_SITE_KEY (public, given to the page by GET /auth/config) and TURNSTILE_SECRET_KEY
  (private) come from the Cloudflare dashboard (Turnstile > Add site). Both empty = off, as on
  your laptop. Cloudflare's test keys (always pass) let you try it locally: site key
  1x00000000000000000000AA, secret key 1x0000000000000000000000000000000AA.

The terminal CLI can't show the widget, so with Turnstile on it logs in with a password (admins,
POST /auth/password-login) rather than a code. Tests replace verify_token() with a fake.
"""

import logging

import httpx

from app.config import get_settings

log = logging.getLogger("app.auth.turnstile")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def enabled() -> bool:
    return bool(get_settings().turnstile_secret_key)


def verify_token(token: str, ip: str) -> bool:
    """True if Cloudflare says the token is genuine and unused. A network error counts as False
    (no code is sent rather than letting bots through when Cloudflare can't be reached)."""
    if not token:
        return False
    try:
        reply = httpx.post(VERIFY_URL, timeout=10, data={
            "secret": get_settings().turnstile_secret_key, "response": token, "remoteip": ip,
        })
        reply.raise_for_status()
        result = reply.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Turnstile check failed: %s", exc)
        return False
    if not result.get("success"):
        log.info("Turnstile refused a token: %s", result.get("error-codes"))
    return bool(result.get("success"))
