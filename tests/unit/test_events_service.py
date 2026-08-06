"""Unit tests for ``src.services.events_service``.

The publisher is best-effort and PII-safe by contract. These tests pin both:
it publishes the validated envelope to the shared channel on the happy path,
it is a no-op when the feature is disabled, and — crucially — it never lets a
Redis failure propagate into the caller (a scoring task must not fail because
the realtime nudge couldn't be sent), while still closing the client.
"""

from __future__ import annotations

import uuid

import pytest

from src.enums.events import HR_EVENTS_CHANNEL, AppEventState, AppEventType
from src.schemas.events import AppEvent
from src.services import events_service


class _FakeRedis:
    """In-memory async stand-in for the subset of redis.asyncio we use."""

    def __init__(self, *, raise_on_publish: bool = False) -> None:
        self.published: list[tuple[str, bytes | str]] = []
        self.closed = False
        self._raise_on_publish = raise_on_publish

    async def publish(self, channel: str, message: bytes | str) -> int:
        if self._raise_on_publish:
            raise ConnectionError("redis down")
        self.published.append((channel, message))
        return 1

    async def aclose(self) -> None:
        self.closed = True


def _patch_redis(monkeypatch: pytest.MonkeyPatch, client: _FakeRedis) -> _FakeRedis:
    monkeypatch.setattr(events_service, "_redis_client", lambda: client)
    monkeypatch.setattr(events_service.settings, "sse_enabled", True)
    return client


def _event() -> AppEvent:
    return AppEvent(
        event=AppEventType.HARD_FILTER,
        application_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        state=AppEventState.OK,
    )


async def test_publishes_envelope_to_shared_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_redis(monkeypatch, _FakeRedis())
    event = _event()

    await events_service.publish_app_event(event)

    assert len(client.published) == 1
    channel, message = client.published[0]
    assert channel == HR_EVENTS_CHANNEL
    # The wire payload round-trips back to the exact envelope (pointer-only).
    assert AppEvent.from_wire(message) == event


async def test_closes_client_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_redis(monkeypatch, _FakeRedis())
    await events_service.publish_app_event(_event())
    assert client.closed is True


async def test_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeRedis()
    monkeypatch.setattr(events_service, "_redis_client", lambda: client)
    monkeypatch.setattr(events_service.settings, "sse_enabled", False)

    await events_service.publish_app_event(_event())

    # Disabled means we never even construct/use a client.
    assert client.published == []
    assert client.closed is False


async def test_swallows_redis_error_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch_redis(monkeypatch, _FakeRedis(raise_on_publish=True))

    # Must not raise — a failed nudge cannot break the calling task.
    await events_service.publish_app_event(_event())

    assert client.published == []
    assert client.closed is True  # finally-block cleanup still runs
