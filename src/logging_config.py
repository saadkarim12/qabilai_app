"""Structured logging configuration.

- Pretty formatter for ``development`` / ``test`` (one line, easy to scan).
- JSON formatter for ``production`` (one JSON object per record, ready for
  Loki / Datadog / CloudWatch ingestion).
- Both formatters redact email and international-phone patterns from the
  rendered message so PII can't leak via logs.
- Every record carries the current ``correlation_id`` (or ``"-"`` if none),
  injected by ``CorrelationIdLogFilter``.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any, Final

from src.config import Settings
from src.utils.correlation_id import CorrelationIdLogFilter

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+\d[\d\s\-()]{6,}\d")

# Operational fields lifted from a record's ``extra`` into the JSON payload.
# Deliberately a closed, non-PII set: model + token counts + prompt version +
# cache flag. We never serialize arbitrary ``extra`` keys so a future log call
# can't leak PII (CV text, emails) through the structured payload.
_EXTRA_FIELDS_WHITELIST: Final[tuple[str, ...]] = (
    "model",
    "input_tokens",
    "output_tokens",
    "prompt_version",
    "cache_bypassed",
)


def _redact_pii(text: str) -> str:
    text = _EMAIL_RE.sub("<email-redacted>", text)
    text = _PHONE_RE.sub("<phone-redacted>", text)
    return text


class PrettyFormatter(logging.Formatter):
    """Human-readable formatter used in development / test."""

    def __init__(self) -> None:
        super().__init__(
            fmt=("%(asctime)s %(levelname)-8s [%(name)s] [cid=%(correlation_id)s] %(message)s"),
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        return _redact_pii(super().format(record))


class JsonFormatter(logging.Formatter):
    """One JSON object per log record, ready for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact_pii(record.getMessage()),
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        for field in _EXTRA_FIELDS_WHITELIST:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    """Install the chosen formatter on the root logger.

    Idempotent: clears existing handlers so repeated calls (e.g. multiple
    ``create_app`` invocations inside the test suite) don't stack handlers.
    """
    formatter: logging.Formatter = JsonFormatter() if settings.is_production else PrettyFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(CorrelationIdLogFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO if settings.is_production else logging.DEBUG)

    # Tame chatty third-party loggers. SQL echoing stays opt-in via the
    # SQLAlchemy ``echo=`` flag, not the root logger.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
