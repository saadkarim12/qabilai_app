"""Integration tests for the realtime SSE surface (Step 4).

Covers the control plane over HTTP (ticket issuance needs auth; the stream
refuses a bad/expired/used ticket; the kill switch hides both endpoints) and
the relay itself by driving ``_event_stream`` directly against the live Redis
— more robust than streaming an endless ``text/event-stream`` body through the
ASGI test transport.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes import events as events_routes
from src.db.models.user import User
from src.enums.events import AppEventState, AppEventType
from src.enums.roles import UserRole
from src.schemas.events import AppEvent
from src.services.events_service import publish_app_event
from src.services.sse_ticket_service import redeem_ticket
from src.utils.password import hash_password

pytestmark = pytest.mark.integration


# --- Seed / auth helpers ----------------------------------------------------


async def _seed_user(db_session: AsyncSession) -> tuple[User, str]:
    password = "hunter2-correct"
    user = User(
        email=f"hr-{uuid.uuid4().hex[:8]}@kabil.dev",
        password_hash=hash_password(password),
        full_name="HR Person",
        role=UserRole.ADMIN,
    )
    db_session.add(user)
    await db_session.flush()
    return user, password


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def _issue_ticket(client: httpx.AsyncClient, db_session: AsyncSession) -> str:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    resp = await client.post("/events/ticket", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["ticket"])


# --- Ticket endpoint --------------------------------------------------------


async def test_ticket_requires_auth(migrations_applied: None, client: httpx.AsyncClient) -> None:
    resp = await client.post("/events/ticket")
    assert resp.status_code == 401


async def test_ticket_returns_token_and_stream_url(
    migrations_applied: None, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)

    resp = await client.post("/events/ticket", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ticket"]
    assert body["stream_url"] == f"/events/stream?ticket={body['ticket']}"


# --- Stream auth ------------------------------------------------------------


async def test_stream_rejects_bad_ticket(
    migrations_applied: None, client: httpx.AsyncClient
) -> None:
    resp = await client.get("/events/stream?ticket=not-a-real-ticket")
    assert resp.status_code == 401


async def test_stream_ticket_is_single_use(
    migrations_applied: None, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ticket = await _issue_ticket(client, db_session)

    # Consume the ticket the way opening the stream would, then a second
    # attempt with the same ticket must be refused.
    assert await redeem_ticket(ticket) is not None
    resp = await client.get(f"/events/stream?ticket={ticket}")
    assert resp.status_code == 401


async def test_endpoints_hidden_when_disabled(
    migrations_applied: None,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, password = await _seed_user(db_session)
    token = await _login(client, user.email, password)
    monkeypatch.setattr(events_routes.settings, "sse_enabled", False)

    ticket_resp = await client.post("/events/ticket", headers={"Authorization": f"Bearer {token}"})
    stream_resp = await client.get("/events/stream?ticket=whatever")
    assert ticket_resp.status_code == 404
    assert stream_resp.status_code == 404


# --- Relay ------------------------------------------------------------------


async def test_event_stream_relays_published_event(migrations_applied: None) -> None:
    """A published envelope is forwarded to a subscribed stream verbatim."""
    agen = events_routes._event_stream()
    # Pull the first event in a task so the generator subscribes before we
    # publish (Redis pub/sub drops messages with no live subscriber).
    first = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.25)

    event = AppEvent(
        event=AppEventType.HARD_FILTER,
        application_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        state=AppEventState.OK,
    )
    await publish_app_event(event)

    try:
        sse = await asyncio.wait_for(first, timeout=5.0)
    finally:
        await agen.aclose()

    assert sse.event == AppEventType.HARD_FILTER.value
    assert AppEvent.from_wire(sse.data) == event
