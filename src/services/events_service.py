"""Publisher for the realtime HR event stream.

A single best-effort entry point, :func:`publish_app_event`, that fans a
small :class:`~src.schemas.events.AppEvent` envelope out to every connected
HR browser via Redis pub/sub on :data:`~src.enums.events.HR_EVENTS_CHANNEL`.
Background jobs call it at their terminal commit so the FE can refresh the
affected card without polling.

Two invariants make this safe to sprinkle through the pipeline:

* **Best-effort.** Every failure (Redis down, serialization error, feature
  disabled) is swallowed and logged at WARNING — it never raises into the
  caller. The database remains the source of truth; a missed publish only
  means the UI updates on the next fetch/reconnect, never that it's wrong.
* **PII-safe.** The envelope is a pointer, not a payload (enforced by
  ``AppEvent``'s ``extra='forbid'``), so nothing logged or published here
  carries score values, breakdowns, or candidate text.

The Redis client is created per call — matching the throwaway-client pattern
used elsewhere (``src.ai.claude._redis_client``), which coexists cleanly with
the Celery worker's ``asyncio.run``-per-task / NullPool model where a client
bound to one event loop is invalid in the next.
"""

from __future__ import annotations

import logging
from typing import cast

import redis.asyncio as aioredis

from src.config import settings
from src.enums.events import HR_EVENTS_CHANNEL
from src.schemas.events import AppEvent

logger = logging.getLogger(__name__)


def _redis_client() -> aioredis.Redis:
    # redis.asyncio's stubs leave ``from_url`` untyped; cast + ignore so this
    # module stays under ``mypy --strict``. Mirrors src.ai.claude._redis_client.
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=False
    )
    return cast(aioredis.Redis, client)


async def publish_app_event(event: AppEvent) -> None:
    """Publish ``event`` to the shared HR channel. Never raises.

    No-op when ``settings.sse_enabled`` is False. Any Redis or serialization
    error is logged (event type only — no envelope contents) and swallowed,
    because the realtime layer is an accelerator, not a correctness
    dependency.
    """
    if not settings.sse_enabled:
        return

    client = _redis_client()
    try:
        await client.publish(HR_EVENTS_CHANNEL, event.to_wire())
    except Exception:  # best-effort telemetry; callers must not fail on a missed nudge.
        # Log the type/state only — application_id is low-sensitivity but the
        # envelope is never PII either way; we keep the line minimal.
        logger.warning(
            "events.publish_failed",
            extra={"event": event.event.value},
            exc_info=True,
        )
    finally:
        await client.aclose()
