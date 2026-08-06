"""Realtime HR event stream (Server-Sent Events).

Two endpoints, both under ``/events``:

* ``POST /events/ticket`` — authed (HR). Mints a short-lived, single-use
  ticket the browser can use to open the stream (see
  :mod:`src.services.sse_ticket_service` for why a ticket and not the Bearer
  token).
* ``GET /events/stream?ticket=…`` — *unauthenticated at the dependency layer*
  because ``EventSource`` can't send headers; it authenticates by redeeming
  the ticket, then relays every :class:`~src.schemas.events.AppEvent`
  published to the shared ``events:hr`` channel.

The stream is a thin relay: it forwards the compact pointer envelopes
(no scores, no PII) and the FE refetches affected resources through the
normal authed REST endpoints. ``sse-starlette`` owns heartbeats (``ping``)
and client-disconnect detection; on disconnect the generator is cancelled and
the ``finally`` block tears down the Redis subscription.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Annotated, cast

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from src.api.deps import get_current_hr_user
from src.config import settings
from src.db.models.user import User
from src.enums.events import HR_EVENTS_CHANNEL
from src.schemas.events import AppEvent, SseTicketResponse
from src.services.sse_ticket_service import issue_ticket, redeem_ticket

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])


def _redis_client() -> aioredis.Redis:
    # redis.asyncio's stubs leave ``from_url`` untyped; cast + ignore so this
    # module stays under ``mypy --strict``. Mirrors src.ai.claude._redis_client.
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=False
    )
    return cast(aioredis.Redis, client)


@router.post("/ticket", response_model=SseTicketResponse)
async def create_sse_ticket(
    current_user: Annotated[User, Depends(get_current_hr_user)],
) -> SseTicketResponse:
    """Mint a single-use ticket for opening the realtime stream."""
    if not settings.sse_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Realtime events are disabled")
    token = await issue_ticket(current_user.id)
    return SseTicketResponse(ticket=token, stream_url=f"/events/stream?ticket={token}")


async def _event_stream() -> AsyncGenerator[ServerSentEvent, None]:
    """Subscribe to the shared HR channel and relay each envelope as SSE.

    Runs until the client disconnects (sse-starlette cancels the generator),
    at which point the ``finally`` unsubscribes and closes the dedicated
    pub/sub connection so we don't leak Redis connections per open tab.
    """
    client = _redis_client()
    pubsub = client.pubsub()
    await pubsub.subscribe(HR_EVENTS_CHANNEL)
    try:
        async for message in pubsub.listen():
            # ``listen`` also yields subscribe/unsubscribe confirmations.
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if not isinstance(data, bytes | str):
                continue
            try:
                event = AppEvent.from_wire(data)
            except Exception:
                # A malformed publish must not kill an HR's whole stream.
                logger.warning("events.stream.bad_envelope")
                continue
            # SSE ``event:`` field = the type, so the FE can addEventListener
            # per kind; ``data`` carries the full pointer envelope.
            yield ServerSentEvent(data=event.to_wire(), event=event.event.value)
    finally:
        await pubsub.unsubscribe(HR_EVENTS_CHANNEL)
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        await client.aclose()


@router.get("/stream")
async def stream_events(ticket: str) -> EventSourceResponse:
    """Open the realtime event stream after redeeming a single-use ticket."""
    if not settings.sse_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Realtime events are disabled")
    user_id = await redeem_ticket(ticket)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired SSE ticket")
    return EventSourceResponse(_event_stream(), ping=settings.sse_heartbeat_seconds)
