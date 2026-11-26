"""Sending email: one interface, with the provider chosen by MAIL_PROVIDER (Phase 4.7).

Used by app/auth/routes.py to send login codes (and the "delete my account" code).

- Mailer: the interface. Any provider is a class with send(to, subject, text).
- ConsoleMailer ("console", the default): doesn't send anything; it writes the email to the
  server logs, so locally you read your code with `docker compose logs api`. Free, nothing to
  set up, and nobody else can receive it.
- get_mailer(): returns the provider chosen by MAIL_PROVIDER (created once, then reused).

- SesMailer ("ses", Phase 5): really sends the email with Amazon SES (Simple Email Service), in
  AWS_REGION, from MAIL_FROM. It uses boto3 (AWS's Python library), which finds its credentials
  by itself: on the EC2 server, the server's IAM role (a set of permissions given to the machine,
  read from the instance metadata service), so no AWS keys are stored anywhere. SES only sends
  from a verified domain, and a new SES account is in a "sandbox" (it can only send to verified
  addresses) until AWS approves a request to leave it (Phase 5 step 3).
  SES costs about $0.10 per 1,000 emails. It is part of the AWS bill (watched by the AWS Budget),
  so it is not recorded in the usage table, which tracks the paid AI and speech APIs.
- get_mailer(): returns the provider chosen by MAIL_PROVIDER (created once, then reused).

A new provider is one more class with send() and one more name in get_mailer(). Tests replace
get_mailer() with a fake that keeps the emails in a list (tests/conftest.py), and test SesMailer
with a fake SES client (tests/test_production.py).
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


class SesMailer:
    def __init__(self, sender: str, region: str, client=None):
        self.sender = sender
        if client is None:  # imported here, so local use doesn't need AWS at all
            import boto3

            client = boto3.client("sesv2", region_name=region)
        self.client = client

    def send(self, to: str, subject: str, text: str) -> None:
        try:
            self.client.send_email(
                FromEmailAddress=self.sender,
                Destination={"ToAddresses": [to]},
                Content={"Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": text, "Charset": "UTF-8"}},
                }},
            )
        except Exception as exc:  # botocore's errors: no permission, sandbox, throttling...
            log.error("SES could not send an email to %s: %s", to, exc)
            raise MailError("The email could not be sent. Try again in a minute.") from exc
        log.info("Email sent to %s with SES: %s", to, subject)


@lru_cache
def get_mailer() -> Mailer:
    settings = get_settings()
    provider = settings.mail_provider
    if provider == "console":
        return ConsoleMailer()
    if provider == "ses":
        return SesMailer(settings.mail_from, settings.aws_region)
    raise MailError(f"Unknown MAIL_PROVIDER '{provider}'")
