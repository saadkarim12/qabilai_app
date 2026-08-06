"""Tests for the correlation-ID middleware and log filter."""

from __future__ import annotations

import logging
import re

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.utils.correlation_id import (
    CORRELATION_ID_HEADER,
    CorrelationIdLogFilter,
    CorrelationIdMiddleware,
    _correlation_id_var,
    get_correlation_id,
)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _make_app() -> Starlette:
    async def endpoint(_request: Request) -> PlainTextResponse:
        return PlainTextResponse(get_correlation_id() or "")

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(CorrelationIdMiddleware)
    return app


def _make_record(msg: str = "x") -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


def test_middleware_generates_uuid_when_missing() -> None:
    response = TestClient(_make_app()).get("/")
    assert response.status_code == 200
    cid = response.headers[CORRELATION_ID_HEADER]
    assert _UUID_RE.match(cid), cid
    assert response.text == cid  # endpoint saw the same value


def test_middleware_preserves_incoming_header() -> None:
    response = TestClient(_make_app()).get("/", headers={CORRELATION_ID_HEADER: "abc-123"})
    assert response.headers[CORRELATION_ID_HEADER] == "abc-123"
    assert response.text == "abc-123"


def test_correlation_id_resets_after_request() -> None:
    TestClient(_make_app()).get("/")
    # Outside any request, the ContextVar should be back to the default.
    assert get_correlation_id() is None


def test_log_filter_defaults_to_dash() -> None:
    record = _make_record()
    assert CorrelationIdLogFilter().filter(record) is True
    assert record.correlation_id == "-"


def test_log_filter_uses_current_correlation_id() -> None:
    token = _correlation_id_var.set("test-cid")
    try:
        record = _make_record()
        CorrelationIdLogFilter().filter(record)
        assert record.correlation_id == "test-cid"
    finally:
        _correlation_id_var.reset(token)
