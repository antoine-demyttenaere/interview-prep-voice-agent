"""Sending email: one interface, with the provider chosen by MAIL_PROVIDER (Phase 4.7).

Used by app/auth/routes.py to send login codes (and the "delete my account" code).

- Mailer: the interface. Any provider is a class with send(to, subject, text).
- ConsoleMailer ("console", the default): doesn't send anything; it writes the email to the
  server logs, so locally you read your code with `docker compose logs api`. Free, nothing to
  set up, and nobody else can receive it.
- get_mailer(): returns the provider chosen by MAIL_PROVIDER (created once, then reused).

Amazon SES, to really send emails once the app is on AWS, is added in Phase 5 (it needs a
verified domain). Like the speech providers, adding it means one more class here and one more
name in get_mailer(); nothing else changes. Tests replace get_mailer() with a fake that keeps
the emails in a list (tests/conftest.py).
"""

import logging
from functools import lru_cache
from typing import Protocol

from app.config import get_settings

log = logging.getLogger("app.mail")


class MailError(RuntimeError):
    """The email could not be sent."""


class Mailer(Protocol):
    def send(self, to: str, subject: str, text: str) -> None: ...


class ConsoleMailer:
    def send(self, to: str, subject: str, text: str) -> None:
        # print() as well as log, so it shows in `docker compose logs api` whatever the log level
        message = f"\n----- EMAIL (MAIL_PROVIDER=console) -----\nTo: {to}\nSubject: {subject}\n\n{text}\n-----"
        print(message, flush=True)
        log.info("Email to %s: %s", to, subject)


@lru_cache
def get_mailer() -> Mailer:
    provider = get_settings().mail_provider
    if provider == "console":
        return ConsoleMailer()
    raise MailError(f"Unknown MAIL_PROVIDER '{provider}'")
