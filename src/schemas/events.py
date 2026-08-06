"""The realtime event envelope published to the HR SSE stream.

One shape, :class:`AppEvent`, carried over :data:`~src.enums.events.HR_EVENTS_CHANNEL`.
``extra='forbid'`` is the structural guarantee behind the "pointer not
payload" rule (see :mod:`src.enums.events`): there is deliberately no field
for a score value, a breakdown, or message text, and the config rejects any
attempt to smuggle one in. Producers publish :meth:`AppEvent.to_wire`; the
relay parses with :meth:`AppEvent.from_wire`. Both sides round-trip the same
compact JSON, so a malformed message fails loud rather than reaching the FE.
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, ConfigDict

from src.enums.events import AppEventState, AppEventType


class AppEvent(BaseModel):
    """A single change notification.

    ``application_id`` tells the FE *which* card to refresh; ``job_id`` lets
    a board-scoped view cheaply ignore events for jobs it isn't showing.
    ``state`` is present only for score-style events
    (:attr:`~src.enums.events.AppEventType.HARD_FILTER` etc.) and ``None``
    for presence-style ones (WhatsApp message arrived).
    """

    model_config = ConfigDict(extra="forbid")

    event: AppEventType
    application_id: uuid.UUID
    job_id: uuid.UUID | None = None
    state: AppEventState | None = None

    def to_wire(self) -> str:
        """Serialize to the compact JSON string published to Redis."""
        return self.model_dump_json()

    @classmethod
    def from_wire(cls, raw: str | bytes) -> AppEvent:
        """Parse a published message back into a validated envelope.

        Accepts ``bytes`` because the Redis client runs with
        ``decode_responses=False`` (matching the rest of the codebase).
        """
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return cls.model_validate(json.loads(raw))


class SseTicketResponse(BaseModel):
    """Response of ``POST /events/ticket``.

    The browser ``EventSource`` API can't attach an ``Authorization`` header,
    so an authenticated HR caller trades its Bearer token for a short-lived,
    single-use ``ticket`` and opens the stream at ``stream_url`` (which embeds
    the ticket as a query param). The real Bearer token therefore never lands
    in a URL or proxy log.
    """

    ticket: str
    stream_url: str
