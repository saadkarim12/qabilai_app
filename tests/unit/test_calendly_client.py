"""Unit tests for the Calendly API client (Step 6.4)."""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from src.integrations import calendly_client
from src.integrations.calendly_client import (
    CalendlyApiError,
    CalendlyConfigError,
    create_single_use_link,
    parse_webhook_event,
    verify_webhook_signature,
)

EVENT_TYPE_URI = "https://api.calendly.com/event_types/EVT-UUID"


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._json = json_body

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeAsyncClient:
    def __init__(self, result: _FakeResponse | Exception, capture: dict[str, Any]) -> None:
        self._result = result
        self._capture = capture

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        self._capture.update(method=method, url=url, headers=headers, json=json)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, result: _FakeResponse | Exception
) -> dict[str, Any]:
    capture: dict[str, Any] = {}

    def factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(result, capture)

    monkeypatch.setattr(calendly_client.httpx, "AsyncClient", factory)
    return capture


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendly_client.settings, "calendly_personal_access_token", "pat-secret")


# --- create_single_use_link -------------------------------------------------


async def test_create_single_use_link_returns_booking_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(201, {"resource": {"booking_url": "https://calendly.com/d/abc"}})
    capture = _patch_client(monkeypatch, response)

    url = await create_single_use_link(EVENT_TYPE_URI)

    assert url == "https://calendly.com/d/abc"
    assert capture["method"] == "POST"
    assert capture["url"].endswith("/scheduling_links")
    assert capture["json"] == {
        "max_event_count": 1,
        "owner": EVENT_TYPE_URI,
        "owner_type": "EventType",
    }
    assert capture["headers"]["Authorization"] == "Bearer pat-secret"


async def test_create_single_use_link_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendly_client.settings, "calendly_personal_access_token", None)
    with pytest.raises(CalendlyConfigError):
        await create_single_use_link(EVENT_TYPE_URI)


async def test_create_single_use_link_non_2xx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeResponse(403, {"message": "no api access"}))
    with pytest.raises(CalendlyApiError) as exc:
        await create_single_use_link(EVENT_TYPE_URI)
    assert exc.value.status_code == 403


async def test_create_single_use_link_missing_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeResponse(201, {"resource": {}}))
    with pytest.raises(CalendlyApiError):
        await create_single_use_link(EVENT_TYPE_URI)


async def test_transport_error_is_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, httpx.ConnectError("boom"))
    with pytest.raises(CalendlyApiError) as exc:
        await create_single_use_link(EVENT_TYPE_URI)
    # Transport errors carry no HTTP status, so the task treats them transient.
    assert exc.value.status_code is None


# --- verify_webhook_signature ----------------------------------------------


def _sign(body: bytes, key: str, timestamp: int) -> str:
    signed = f"{timestamp}".encode() + b"." + body
    digest = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_verify_signature_accepts_valid() -> None:
    body = b'{"event":"invitee.created"}'
    key = "whsec-123"
    header = _sign(body, key, int(time.time()))
    assert verify_webhook_signature(body, header, key) is True


def test_verify_signature_rejects_tampered_body() -> None:
    key = "whsec-123"
    header = _sign(b'{"event":"invitee.created"}', key, int(time.time()))
    assert verify_webhook_signature(b'{"event":"tampered"}', header, key) is False


def test_verify_signature_rejects_wrong_key() -> None:
    body = b"payload"
    header = _sign(body, "right-key", int(time.time()))
    assert verify_webhook_signature(body, header, "wrong-key") is False


def test_verify_signature_rejects_stale_timestamp() -> None:
    body = b"payload"
    key = "whsec-123"
    header = _sign(body, key, int(time.time()) - 10_000)
    assert verify_webhook_signature(body, header, key) is False


@pytest.mark.parametrize("header", [None, "", "garbage", "t=123", "v1=abc"])
def test_verify_signature_rejects_malformed_header(header: str | None) -> None:
    assert verify_webhook_signature(b"x", header, "k") is False


# --- parse_webhook_event ----------------------------------------------------


def test_parse_invitee_created_extracts_fields() -> None:
    body = {
        "event": "invitee.created",
        "payload": {
            "uri": "https://api.calendly.com/scheduled_events/E/invitees/I",
            "email": "ada@example.com",
            "name": "Ada Lovelace",
            "timezone": "Asia/Karachi",
            "rescheduled": False,
            "tracking": {"utm_content": "tok-123"},
            "scheduled_event": {
                "uri": "https://api.calendly.com/scheduled_events/E",
                "start_time": "2026-06-20T15:00:00.000000Z",
                "end_time": "2026-06-20T15:30:00.000000Z",
                "location": {"type": "zoom", "join_url": "https://zoom.us/j/123"},
            },
        },
    }
    event = parse_webhook_event(body)

    assert event is not None
    assert event.kind == "invitee.created"
    assert event.tracking_token == "tok-123"
    assert event.invitee_uri == "https://api.calendly.com/scheduled_events/E/invitees/I"
    assert event.event_uri == "https://api.calendly.com/scheduled_events/E"
    assert event.start_time == datetime(2026, 6, 20, 15, 0, tzinfo=UTC)
    assert event.end_time == datetime(2026, 6, 20, 15, 30, tzinfo=UTC)
    # Enriched booking detail.
    assert event.invitee_email == "ada@example.com"
    assert event.invitee_timezone == "Asia/Karachi"
    assert event.location_type == "zoom"
    assert event.join_url == "https://zoom.us/j/123"
    assert event.location_text is None
    assert event.rescheduled is False


def test_parse_invitee_created_physical_location_and_reschedule() -> None:
    body = {
        "event": "invitee.created",
        "payload": {
            "uri": "https://api.calendly.com/scheduled_events/E/invitees/I2",
            "rescheduled": True,
            "tracking": {"utm_content": "tok-7"},
            "scheduled_event": {
                "uri": "https://api.calendly.com/scheduled_events/E2",
                "location": {"type": "physical", "location": "12 Main St, Dubai"},
            },
        },
    }
    event = parse_webhook_event(body)

    assert event is not None
    assert event.rescheduled is True
    assert event.location_type == "physical"
    assert event.join_url is None
    assert event.location_text == "12 Main St, Dubai"


def test_parse_invitee_canceled() -> None:
    body = {
        "event": "invitee.canceled",
        "payload": {
            "uri": "https://api.calendly.com/scheduled_events/E/invitees/I",
            "tracking": {"utm_content": "tok-9"},
            "scheduled_event": {"uri": "https://api.calendly.com/scheduled_events/E"},
            "cancellation": {"canceler_type": "invitee", "reason": "Schedule conflict"},
        },
    }
    event = parse_webhook_event(body)

    assert event is not None
    assert event.kind == "invitee.canceled"
    assert event.tracking_token == "tok-9"
    assert event.start_time is None
    assert event.cancel_reason == "Schedule conflict"
    assert event.canceled_by == "invitee"


def test_parse_unknown_event_kind_returns_none() -> None:
    assert parse_webhook_event({"event": "routing_form_submission.created"}) is None


def test_parse_missing_tracking_token_is_none() -> None:
    body = {
        "event": "invitee.created",
        "payload": {"scheduled_event": {"uri": "u"}},
    }
    event = parse_webhook_event(body)
    assert event is not None
    assert event.tracking_token is None
    assert event.event_uri == "u"
