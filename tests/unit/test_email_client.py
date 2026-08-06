"""Unit tests for the SMTP email transport (Step 6.4)."""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any

import aiosmtplib
import httpx
import pytest

from src.integrations import email_client
from src.integrations.email_client import (
    BREVO_API_URL,
    RESEND_API_URL,
    EmailConfigError,
    EmailSendError,
)


@pytest.fixture
def smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure a plausible SMTP host/sender on the shared settings singleton."""
    monkeypatch.setattr(email_client.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(email_client.settings, "smtp_port", 587)
    monkeypatch.setattr(email_client.settings, "smtp_user", "mailer")
    monkeypatch.setattr(email_client.settings, "smtp_pass", "secret")
    monkeypatch.setattr(email_client.settings, "smtp_from", "noreply@kabil.ai")


def _capture_send(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace ``aiosmtplib.send`` with a stub that records its call."""
    captured: dict[str, Any] = {}

    async def fake_send(message: EmailMessage, **kwargs: Any) -> None:
        captured["message"] = message
        captured["kwargs"] = kwargs

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    return captured


async def test_send_email_builds_multipart_message(
    smtp_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_send(monkeypatch)

    await email_client.send_email(
        to="ada@example.com",
        subject="Book your interview",
        html_body="<p>hello</p>",
        text_body="hello",
        category="interview_invite",
    )

    message: EmailMessage = captured["message"]
    assert message["To"] == "ada@example.com"
    assert message["From"] == "noreply@kabil.ai"
    assert message["Subject"] == "Book your interview"
    assert message.is_multipart()
    payloads = {part.get_content_type() for part in message.iter_parts()}  # type: ignore[attr-defined]
    assert payloads == {"text/plain", "text/html"}


async def test_send_email_passes_connection_settings(
    smtp_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_send(monkeypatch)

    await email_client.send_email(
        to="ada@example.com",
        subject="s",
        html_body="<p>h</p>",
        text_body="h",
        category="interview_invite",
    )

    kwargs = captured["kwargs"]
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587
    assert kwargs["username"] == "mailer"
    assert kwargs["password"] == "secret"
    # Port 587 → opportunistic STARTTLS (auto), not implicit TLS.
    assert kwargs["use_tls"] is False
    assert kwargs["start_tls"] is None


async def test_implicit_tls_on_port_465(
    smtp_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(email_client.settings, "smtp_port", 465)
    captured = _capture_send(monkeypatch)

    await email_client.send_email(
        to="ada@example.com",
        subject="s",
        html_body="<p>h</p>",
        text_body="h",
        category="interview_invite",
    )

    kwargs = captured["kwargs"]
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False


async def test_missing_host_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(email_client.settings, "smtp_host", None)

    with pytest.raises(EmailConfigError):
        await email_client.send_email(
            to="ada@example.com",
            subject="s",
            html_body="<p>h</p>",
            text_body="h",
            category="interview_invite",
        )


async def test_smtp_failure_becomes_send_error(
    smtp_configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(message: EmailMessage, **kwargs: Any) -> None:
        raise aiosmtplib.SMTPException("relay refused")

    monkeypatch.setattr(aiosmtplib, "send", boom)

    with pytest.raises(EmailSendError):
        await email_client.send_email(
            to="ada@example.com",
            subject="s",
            html_body="<p>h</p>",
            text_body="h",
            category="interview_invite",
        )


# --- Resend HTTP transport --------------------------------------------------


def _fake_httpx(monkeypatch: pytest.MonkeyPatch, *, status_code: int) -> dict[str, Any]:
    """Replace ``httpx.AsyncClient`` with a stub that records the POST."""
    captured: dict[str, Any] = {}

    class _Response:
        def __init__(self, code: int) -> None:
            self.status_code = code

        @property
        def is_error(self) -> bool:
            return self.status_code >= 400

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def post(self, url: str, *, headers: Any = None, json: Any = None) -> _Response:
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Response(status_code)

    monkeypatch.setattr(email_client.httpx, "AsyncClient", _Client)
    return captured


async def test_resend_used_when_api_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    # Resend configured, SMTP intentionally absent — the dispatch must pick HTTP.
    monkeypatch.setattr(email_client.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(email_client.settings, "resend_from", "onboarding@resend.dev")
    monkeypatch.setattr(email_client.settings, "smtp_host", None)
    captured = _fake_httpx(monkeypatch, status_code=200)

    await email_client.send_email(
        to="ada@example.com",
        subject="Book your interview",
        html_body="<p>hello</p>",
        text_body="hello",
        category="interview_invite",
    )

    assert captured["url"] == RESEND_API_URL
    assert captured["headers"]["Authorization"] == "Bearer re_test_key"
    assert captured["json"] == {
        "from": "onboarding@resend.dev",
        "to": ["ada@example.com"],
        "subject": "Book your interview",
        "html": "<p>hello</p>",
        "text": "hello",
    }


async def test_resend_non_2xx_becomes_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_client.settings, "resend_api_key", "re_test_key")
    _fake_httpx(monkeypatch, status_code=422)

    with pytest.raises(EmailSendError):
        await email_client.send_email(
            to="ada@example.com",
            subject="s",
            html_body="<p>h</p>",
            text_body="h",
            category="interview_invite",
        )


async def test_resend_transport_error_becomes_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_client.settings, "resend_api_key", "re_test_key")

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(email_client.httpx, "AsyncClient", _Client)

    with pytest.raises(EmailSendError):
        await email_client.send_email(
            to="ada@example.com",
            subject="s",
            html_body="<p>h</p>",
            text_body="h",
            category="interview_invite",
        )


# --- Brevo HTTP transport ---------------------------------------------------


async def test_brevo_used_when_api_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_client.settings, "brevo_api_key", "xkeysib-test")
    monkeypatch.setattr(email_client.settings, "brevo_from", "hr@kabil.ai")
    monkeypatch.setattr(email_client.settings, "brevo_from_name", "Kabil")
    monkeypatch.setattr(email_client.settings, "smtp_host", None)
    captured = _fake_httpx(monkeypatch, status_code=201)

    await email_client.send_email(
        to="ada@example.com",
        subject="Book your interview",
        html_body="<p>hello</p>",
        text_body="hello",
        category="interview_invite",
    )

    assert captured["url"] == BREVO_API_URL
    assert captured["headers"]["api-key"] == "xkeysib-test"
    assert captured["json"] == {
        "sender": {"name": "Kabil", "email": "hr@kabil.ai"},
        "to": [{"email": "ada@example.com"}],
        "subject": "Book your interview",
        "htmlContent": "<p>hello</p>",
        "textContent": "hello",
    }


async def test_brevo_preferred_over_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both HTTP keys set — Brevo wins (it can reach any recipient).
    monkeypatch.setattr(email_client.settings, "brevo_api_key", "xkeysib-test")
    monkeypatch.setattr(email_client.settings, "brevo_from", "hr@kabil.ai")
    monkeypatch.setattr(email_client.settings, "resend_api_key", "re_test_key")
    captured = _fake_httpx(monkeypatch, status_code=201)

    await email_client.send_email(
        to="ada@example.com",
        subject="s",
        html_body="<p>h</p>",
        text_body="h",
        category="interview_invite",
    )

    assert captured["url"] == BREVO_API_URL


async def test_brevo_missing_sender_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_client.settings, "brevo_api_key", "xkeysib-test")
    monkeypatch.setattr(email_client.settings, "brevo_from", None)

    with pytest.raises(EmailConfigError):
        await email_client.send_email(
            to="ada@example.com",
            subject="s",
            html_body="<p>h</p>",
            text_body="h",
            category="interview_invite",
        )


async def test_brevo_non_2xx_becomes_send_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_client.settings, "brevo_api_key", "xkeysib-test")
    monkeypatch.setattr(email_client.settings, "brevo_from", "hr@kabil.ai")
    _fake_httpx(monkeypatch, status_code=401)

    with pytest.raises(EmailSendError):
        await email_client.send_email(
            to="ada@example.com",
            subject="s",
            html_body="<p>h</p>",
            text_body="h",
            category="interview_invite",
        )
