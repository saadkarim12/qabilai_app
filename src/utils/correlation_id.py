"""Correlation-ID propagation.

A correlation ID identifies a single request end-to-end across logs, jobs,
and external calls. The middleware here either honours an incoming
``X-Correlation-ID`` header or mints a UUID4, stores it in a ContextVar so
the rest of the request task can read it, and echoes it on the response.

Pure ASGI is used (rather than ``BaseHTTPMiddleware``) so the ContextVar
reliably propagates into the route handler's task on every Starlette
version.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_ID_HEADER = "X-Correlation-ID"
_HEADER_BYTES = CORRELATION_ID_HEADER.lower().encode("latin-1")

_correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Return the correlation ID for the current task, if any."""
    return _correlation_id_var.get()


def set_correlation_id(value: str) -> None:
    """Set the correlation ID for the current task (workers / tests)."""
    _correlation_id_var.set(value)


class CorrelationIdMiddleware:
    """Pure-ASGI middleware that ensures every request has a correlation ID."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cid = _extract_correlation_id(scope) or str(uuid.uuid4())
        # No try/finally reset: the catch-all `Exception` handler is dispatched
        # from Starlette's outer ServerErrorMiddleware *after* this middleware's
        # frame has unwound, so resetting here would clear the ContextVar before
        # the handler reads it. The request task ends after the response, which
        # naturally bounds the var's lifetime.
        _correlation_id_var.set(cid)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Idempotent: exception handlers also set this header so that
                # responses dispatched by Starlette's outer ServerErrorMiddleware
                # (which bypasses this wrapper) still carry it.
                if not any(name == _HEADER_BYTES for name, _ in headers):
                    headers.append((_HEADER_BYTES, cid.encode("latin-1")))
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _extract_correlation_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == _HEADER_BYTES:
            try:
                decoded = value.decode("latin-1").strip()
            except UnicodeDecodeError:
                return None
            return decoded or None
    return None


class CorrelationIdLogFilter(logging.Filter):
    """Inject the current correlation ID onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id_var.get() or "-"
        return True
