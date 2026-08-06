"""Global FastAPI exception handlers.

Each handler logs through the application logger and returns a JSON body
that always includes the current ``correlation_id`` so support can join a
user-reported error to the corresponding server log line. Every error
response also carries the ``X-Correlation-ID`` header explicitly — the
catch-all ``Exception`` handler is dispatched by Starlette's outer
``ServerErrorMiddleware`` and bypasses ``CorrelationIdMiddleware`` on the
return path, so headers can't be relied on from middleware alone.

Stack traces never appear in client responses. In production, the
exception message is hidden too — clients see only the error code and the
correlation ID. Dev/test responses include the exception type + message to
keep local debugging painless.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import Settings
from src.exceptions import KabilError
from src.utils.correlation_id import CORRELATION_ID_HEADER, get_correlation_id

_logger = logging.getLogger(__name__)


_HTTP_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}


def _http_error_code(status: int) -> str:
    return _HTTP_STATUS_CODES.get(status, "http_error")


def _error_response(status: int, body: dict[str, Any]) -> JSONResponse:
    cid = get_correlation_id() or "-"
    body["correlation_id"] = cid
    return JSONResponse(
        status_code=status,
        content=body,
        headers={CORRELATION_ID_HEADER: cid},
    )


def install_exception_handlers(app: FastAPI, settings: Settings) -> None:
    """Register all global exception handlers against ``app``."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return _error_response(
            exc.status_code,
            {
                "error": _http_error_code(exc.status_code),
                "message": exc.detail,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's ``RequestValidationError.errors()`` can include a raw
        # ``ValueError`` instance in ``ctx.error`` when a custom validator
        # raises one — that isn't JSON-serializable. ``jsonable_encoder``
        # walks the structure and turns any unknown types into strings.
        return _error_response(
            422,
            {
                "error": "validation_error",
                "message": "Request validation failed.",
                "details": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(KabilError)
    async def kabil_exception_handler(_request: Request, exc: KabilError) -> JSONResponse:
        _logger.error("Domain error: %s [%s]", exc.message, exc.error_code, exc_info=exc)
        body: dict[str, Any] = {
            "error": exc.error_code,
            "message": exc.message,
        }
        if exc.details:
            body["details"] = exc.details
        return _error_response(exc.http_status, body)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        _logger.exception("Unhandled exception")
        body: dict[str, Any] = {"error": "internal_server_error"}
        if settings.is_production:
            body["message"] = (
                "An unexpected error occurred. Please contact support with the correlation ID."
            )
        else:
            body["message"] = str(exc)
            body["exception"] = type(exc).__name__
        return _error_response(500, body)
