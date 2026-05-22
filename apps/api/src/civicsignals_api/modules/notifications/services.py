"""Public service interface for the notifications module.

Other modules call notifications only through the functions defined here — never
by importing notifications's models or routes directly (doc 06 §3).

For B1 this exposes a minimal, injectable transactional-email sender. In dev the
default transport targets Mailpit via the SMTP settings already in
``config.py``. The transport is a small :class:`EmailSender` protocol so tests
(and B-series work) can substitute an in-memory recorder without touching SMTP.
Digest/notification models and the full template system are later epics; this is
the transactional-mail seam (verification, password reset, invites).
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import structlog

from civicsignals_api.config import get_settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class OutboundEmail:
    """A rendered transactional email ready to send."""

    to: str
    subject: str
    text_body: str
    html_body: str | None = None


class EmailSender(Protocol):
    """Pluggable transport for transactional email (mockable in tests)."""

    def send(self, message: OutboundEmail) -> None: ...


class SMTPEmailSender:
    """Sends mail over SMTP. Defaults target the dev Mailpit instance.

    No TLS/auth in dev (Mailpit needs none); production self-host configures the
    operator's relay via the same ``SMTP_*`` env vars. Email/webhook delivery is
    a `worker_notify` concern at scale, but transactional verification mail is
    sent inline from the request path in MVP.
    """

    def __init__(self, host: str, port: int, from_addr: str) -> None:
        self._host = host
        self._port = port
        self._from_addr = from_addr

    def send(self, message: OutboundEmail) -> None:
        msg = EmailMessage()
        msg["From"] = self._from_addr
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg.set_content(message.text_body)
        if message.html_body is not None:
            msg.add_alternative(message.html_body, subtype="html")
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            smtp.send_message(msg)


@dataclass
class RecordingEmailSender:
    """In-memory sender for tests — records outbound mail instead of sending."""

    sent: list[OutboundEmail] = field(default_factory=list)

    def send(self, message: OutboundEmail) -> None:
        self.sent.append(message)


def default_email_sender() -> EmailSender:
    """Build the SMTP sender from settings (dev = Mailpit)."""
    settings = get_settings()
    return SMTPEmailSender(settings.smtp_host, settings.smtp_port, settings.email_from)


def send_email(message: OutboundEmail, *, sender: EmailSender | None = None) -> None:
    """Send a transactional email through ``sender`` (defaults to SMTP).

    Failures are logged and swallowed so a flaky mail relay never breaks the
    surrounding request (e.g. signup). Callers that must guarantee delivery
    should enqueue via ``worker_notify`` instead (later epics).
    """
    transport = sender or default_email_sender()
    try:
        transport.send(message)
    except Exception:
        # Best-effort transactional send: never break the surrounding request
        # (e.g. signup) on a flaky mail relay.
        logger.warning("transactional_email_send_failed", to=message.to, subject=message.subject)
