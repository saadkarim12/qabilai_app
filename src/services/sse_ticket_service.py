"""Short-lived, single-use tickets for authenticating an SSE connection.

The browser ``EventSource`` API cannot send an ``Authorization`` header, so a
Bearer token can't be used to open the realtime stream directly — and putting
the token in the query string would leak it into access/proxy logs. Instead an
authenticated HR caller mints a ticket here (``POST /events/ticket``), then
opens ``GET /events/stream?ticket=…`` with it.

A ticket is an opaque random string stored in Redis as
``sse:ticket:{token} -> user_id`` with a short TTL
(``settings.sse_ticket_ttl_seconds``). Redemption is an atomic ``GETDEL`` so a
ticket works exactly once and can't be replayed; an unknown or expired ticket
simply resolves to ``None`` and the stream is refused.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Final, cast

import redis.asyncio as aioredis

from src.config import settings

# Redis key namespace. Bump the ``v1`` segment if the value encoding changes.
TICKET_KEY_PREFIX: Final[str] = "sse:ticket:v1:"

# Bytes of entropy in the opaque token. 32 bytes -> ~43 url-safe chars; far
# beyond guessing range for a credential that also lives at most ~60s.
_TOKEN_NBYTES: Final[int] = 32


def _redis_client() -> aioredis.Redis:
    # redis.asyncio's stubs leave ``from_url`` untyped; cast + ignore so this
    # module stays under ``mypy --strict``. Mirrors src.ai.claude._redis_client.
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, decode_responses=False
    )
    return cast(aioredis.Redis, client)


def _key(token: str) -> str:
    return f"{TICKET_KEY_PREFIX}{token}"


async def issue_ticket(user_id: uuid.UUID) -> str:
    """Mint a single-use ticket for ``user_id`` and return the opaque token."""
    token = secrets.token_urlsafe(_TOKEN_NBYTES)
    client = _redis_client()
    try:
        await client.set(_key(token), str(user_id), ex=settings.sse_ticket_ttl_seconds)
    finally:
        await client.aclose()
    return token


async def redeem_ticket(token: str) -> uuid.UUID | None:
    """Atomically consume ``token`` and return the user id it was minted for.

    Returns ``None`` for an unknown, already-used, or expired ticket, or one
    whose stored value isn't a valid uuid. The ``GETDEL`` makes redemption
    single-use even under concurrent attempts.
    """
    if not token:
        return None
    client = _redis_client()
    try:
        raw = cast("bytes | None", await client.getdel(_key(token)))
    finally:
        await client.aclose()
    if raw is None:
        return None
    try:
        return uuid.UUID(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
