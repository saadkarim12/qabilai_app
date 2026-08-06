"""Email transport (Step 6.4) — Brevo / Resend HTTP API or SMTP.

A thin async wrapper that delivers one already-rendered message and surfaces
the server's errors. This module is **transport only** — it does not render
templates or decide *what* to send (that lives in
:mod:`src.services.email_service`), so the worker and service layers stay
testable against a single monkeypatchable seam.

Three transports, chosen by config in priority order:

1. ``BREVO_API_KEY`` set → POST to Brevo's HTTPS API. Preferred for production:
   single-sender verification lets it email *any* recipient (no domain/DNS),
   unlike Resend's test mode which only reaches the account's own address.
2. else ``RESEND_API_KEY`` set → POST to Resend's HTTPS API.
3. else → SMTP via :mod:`aiosmtplib` (local MailHog / a relay that allows it).

The HTTP paths exist because some hosts block outbound SMTP entirely — Railway
filters ports 25/465/587, so ``aiosmtplib`` can't connect and only an HTTPS API
gets mail out.

Configuration (all from :class:`~src.config.Settings`): ``BREVO_API_KEY`` +
``BREVO_FROM`` (+ ``BREVO_FROM_NAME``) for the Brevo path; ``RESEND_API_KEY`` +
``RESEND_FROM`` for the Resend path; ``SMTP_HOST`` (required for the SMTP path)
plus optional ``SMTP_PORT`` / ``SMTP_USER`` / ``SMTP_PASS`` and the
``SMTP_FROM`` sender. A missing transport config surfaces as
:class:`EmailConfigError` before any network round-trip, so the app and worker
boot fine without email configured — only an actual send requires it.

Message bodies are candidate-facing (they name the job and carry a personal
booking link), so they're **never logged** — only the recipient address
(already PII we hold), an opaque ``category`` tag, and the transport outcome
appear.
"""

from __future__ import annotations

import logging
from email.message import EmailMessage
from typing import Final

import aiosmtplib
import httpx

from src.config import settings

logger = logging.getLogger(__name__)


# --- Constants --------------------------------------------------------------

# Generous ceiling for a single delivery: a healthy relay answers in well under
# a second, but a slow TLS handshake on a cold connection can take longer.
# Bounds a hung Celery task without tripping on a momentarily slow hop.
SEND_TIMEOUT_SECONDS: Final[float] = 30.0

# Resend's transactional-send endpoint.
RESEND_API_URL: Final[str] = "https://api.resend.com/emails"

# Brevo's transactional-send endpoint.
BREVO_API_URL: Final[str] = "https://api.brevo.com/v3/smtp/email"

# Implicit-TLS port. 465 wraps the whole connection in TLS from the first byte
# ("SMTPS"); every other port (587 submission, 25 relay) negotiates STARTTLS
# opportunistically instead. We pick the mode from the port so a single
# SMTP_PORT knob configures both.
IMPLICIT_TLS_PORT: Final[int] = 465


# --- Exceptions -------------------------------------------------------------


class EmailConfigError(RuntimeError):
    """Raised when required SMTP settings are missing."""


class EmailSendError(RuntimeError):
    """Raised when the SMTP server rejects a message or the connection fails.

    The caller (a Celery task) decides transient-vs-permanent for retry; we do
    not classify here because aiosmtplib raises the same exception family for
    both a hard 5xx rejection and a transient connection drop.
    """


# --- Helpers ----------------------------------------------------------------


def _smtp_host() -> str:
    """Return the configured SMTP host or raise.

    Validated lazily (not at import) so unconfigured deployments still boot;
    user/password are optional because dev relays (MailHog) and some internal
    relays accept unauthenticated submission.
    """
    host = settings.smtp_host
    if not host:
        raise EmailConfigError("SMTP_HOST is not configured; cannot send email")
    return host


def _build_message(*, to: str, subject: str, html_body: str, text_body: str) -> EmailMessage:
    """Assemble a ``multipart/alternative`` message (plain text + HTML).

    The plain-text part is the default content and the HTML an alternative, so
    clients that can't render HTML fall back gracefully and spam filters see a
    text body.
    """
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


# --- Public API -------------------------------------------------------------


async def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
    category: str,
) -> None:
    """Deliver one rendered email via Resend (if configured) or SMTP.

    ``category`` is a short, PII-free tag (e.g. ``"interview_invite"``) used
    only for log context — the subject and bodies are never logged. A fresh
    connection is opened per call (no pooling): Celery bridges sync→async with
    ``asyncio.run`` (a new event loop per task), and a connection bound to a
    closed loop is unusable — the same constraint documented in the WhatsApp
    and Azure Blob clients.

    Raises :class:`EmailConfigError` if the chosen transport isn't configured
    and :class:`EmailSendError` if the provider rejects the message or the
    transport fails.
    """
    if settings.brevo_api_key:
        await _send_via_brevo(to=to, subject=subject, html_body=html_body, text_body=text_body)
        transport = "brevo"
    elif settings.resend_api_key:
        await _send_via_resend(to=to, subject=subject, html_body=html_body, text_body=text_body)
        transport = "resend"
    else:
        await _send_via_smtp(to=to, subject=subject, html_body=html_body, text_body=text_body)
        transport = "smtp"

    logger.info("email.send", extra={"to": to, "category": category, "transport": transport})


async def _send_via_brevo(*, to: str, subject: str, html_body: str, text_body: str) -> None:
    """POST one message to Brevo's HTTPS transactional API.

    Preferred on Railway (SMTP egress blocked) and over Resend because a
    verified single sender can reach any recipient. ``BREVO_FROM`` must name a
    Brevo-verified sender, else the API rejects the send. A non-2xx response or
    a transport error becomes :class:`EmailSendError`; the response body is not
    logged (it can echo the recipient or server detail).
    """
    sender = settings.brevo_from
    if not sender:
        raise EmailConfigError("BREVO_FROM is not configured; cannot send email via Brevo")
    payload = {
        "sender": {"name": settings.brevo_from_name, "email": sender},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
    }
    headers = {
        "api-key": settings.brevo_api_key or "",
        "Content-Type": "application/json",
        "accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
            response = await client.post(BREVO_API_URL, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise EmailSendError(f"Brevo send failed: {type(exc).__name__}") from exc

    if response.is_error:
        raise EmailSendError(f"Brevo send failed: HTTP {response.status_code}")


async def _send_via_resend(*, to: str, subject: str, html_body: str, text_body: str) -> None:
    """POST one message to Resend's HTTPS API.

    Used wherever SMTP egress is blocked (e.g. Railway). A non-2xx response or
    a transport error becomes :class:`EmailSendError`; the response body is not
    logged (it can echo the recipient or server detail).
    """
    payload = {
        "from": settings.resend_from,
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
            response = await client.post(RESEND_API_URL, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise EmailSendError(f"Resend send failed: {type(exc).__name__}") from exc

    if response.is_error:
        raise EmailSendError(f"Resend send failed: HTTP {response.status_code}")


async def _send_via_smtp(*, to: str, subject: str, html_body: str, text_body: str) -> None:
    """Deliver one message over SMTP via :mod:`aiosmtplib`."""
    host = _smtp_host()
    port = settings.smtp_port
    message = _build_message(to=to, subject=subject, html_body=html_body, text_body=text_body)

    use_tls = port == IMPLICIT_TLS_PORT
    # For non-465 ports let aiosmtplib auto-negotiate STARTTLS (start_tls=None):
    # it upgrades when the server advertises STARTTLS and stays plaintext for a
    # dev relay that doesn't — so one code path serves both prod and MailHog.
    start_tls = None if not use_tls else False

    try:
        await aiosmtplib.send(
            message,
            hostname=host,
            port=port,
            username=settings.smtp_user,
            password=settings.smtp_pass,
            use_tls=use_tls,
            start_tls=start_tls,
            timeout=SEND_TIMEOUT_SECONDS,
        )
    except (aiosmtplib.SMTPException, OSError) as exc:
        # Includes auth failures, hard rejections, and connection/timeout
        # errors. We surface the type but not the message, which can echo the
        # recipient or server-side detail.
        raise EmailSendError(f"SMTP send failed: {type(exc).__name__}") from exc
