"""Unit tests for the Calendly webhook route (Step 6.4)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from src.api.routes import calendly_webhook
from src.main import create_app

SIGNING_KEY = "whsec-test"


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(calendly_webhook.settings, "interview_scheduling_enabled", True)
    monkeypatch.setattr(calendly_webhook.settings, "calendly_webhook_signing_key", SIGNING_KEY)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _capture_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def fake_apply_async(*, kwargs: dict[str, Any]) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(
        calendly_webhook.handle_calendly_event_task, "apply_async", fake_apply_async
    )
    return captured


def _signed_headers(body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signed = timestamp.encode() + b"." + body
    digest = hmac.new(SIGNING_KEY.encode(), signed, hashlib.sha256).hexdigest()
    return {"Calendly-Webhook-Signature": f"t={timestamp},v1={digest}"}


_CREATED_BODY = {
    "event": "invitee.created",
    "payload": {
        "uri": "https://api.calendly.com/scheduled_events/E/invitees/I",
        "tracking": {"utm_content": "tok-123"},
        "scheduled_event": {
            "uri": "https://api.calendly.com/scheduled_events/E",
            "start_time": "2026-06-20T15:00:00.000000Z",
            "end_time": "2026-06-20T15:30:00.000000Z",
        },
    },
}


async def test_valid_signature_dispatches_task(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_dispatch(monkeypatch)
    body = json.dumps(_CREATED_BODY).encode()

    resp = await client.post("/webhooks/calendly", content=body, headers=_signed_headers(body))

    assert resp.status_code == 200
    assert len(captured) == 1
    assert captured[0]["kind"] == "invitee.created"
    assert captured[0]["tracking_token"] == "tok-123"
    assert captured[0]["start_time"] == "2026-06-20T15:00:00+00:00"


async def test_bad_signature_is_403_and_no_dispatch(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_dispatch(monkeypatch)
    body = json.dumps(_CREATED_BODY).encode()

    resp = await client.post(
        "/webhooks/calendly",
        content=body,
        headers={"Calendly-Webhook-Signature": "t=123,v1=deadbeef"},
    )

    assert resp.status_code == 403
    assert captured == []


async def test_unhandled_event_kind_acks_without_dispatch(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_dispatch(monkeypatch)
    body = json.dumps({"event": "routing_form_submission.created", "payload": {}}).encode()

    resp = await client.post("/webhooks/calendly", content=body, headers=_signed_headers(body))

    # Acked so Calendly stops retrying, but nothing enqueued.
    assert resp.status_code == 200
    assert captured == []


async def test_disabled_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendly_webhook.settings, "interview_scheduling_enabled", False)
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post("/webhooks/calendly", content=b"{}")
    assert resp.status_code == 404
