"""Tests for the logging formatters and PII redaction."""

from __future__ import annotations

import json
import logging

from src.logging_config import JsonFormatter, PrettyFormatter, _redact_pii


def _make_record(msg: str) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    record.correlation_id = "test-cid"
    return record


def test_pii_redaction_masks_email() -> None:
    redacted = _redact_pii("contact user@example.com today")
    assert "user@example.com" not in redacted
    assert "<email-redacted>" in redacted


def test_pii_redaction_masks_international_phone() -> None:
    redacted = _redact_pii("phone +966 50 123 4567 here")
    assert "+966" not in redacted
    assert "<phone-redacted>" in redacted


def test_pretty_formatter_includes_correlation_id_and_level() -> None:
    output = PrettyFormatter().format(_make_record("hello"))
    assert "cid=test-cid" in output
    assert "INFO" in output
    assert "hello" in output
    assert "test.logger" in output


def test_json_formatter_has_required_keys() -> None:
    output = JsonFormatter().format(_make_record("hello"))
    data = json.loads(output)
    assert data["timestamp"]
    assert data["level"] == "INFO"
    assert data["logger"] == "test.logger"
    assert data["message"] == "hello"
    assert data["correlation_id"] == "test-cid"


def test_json_formatter_redacts_pii_in_message() -> None:
    output = JsonFormatter().format(_make_record("user user@example.com signed up"))
    data = json.loads(output)
    assert "user@example.com" not in data["message"]
    assert "<email-redacted>" in data["message"]


def test_json_formatter_emits_whitelisted_extra_fields() -> None:
    record = _make_record("claude.api_call")
    record.model = "claude-sonnet-4-6"
    record.input_tokens = 1360
    record.output_tokens = 820
    record.prompt_version = "parse_cv:base:v1"
    record.cache_bypassed = False

    data = json.loads(JsonFormatter().format(record))
    assert data["model"] == "claude-sonnet-4-6"
    assert data["input_tokens"] == 1360
    assert data["output_tokens"] == 820
    assert data["prompt_version"] == "parse_cv:base:v1"
    # ``False`` is meaningful and must survive (not be dropped as falsy).
    assert data["cache_bypassed"] is False


def test_json_formatter_omits_extra_fields_when_absent() -> None:
    data = json.loads(JsonFormatter().format(_make_record("plain message")))
    # Records without the operational fields stay clean — no null padding.
    assert "model" not in data
    assert "input_tokens" not in data
    assert "prompt_version" not in data


def test_json_formatter_includes_exception_when_present() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=None,
            exc_info=sys.exc_info(),
        )
        record.correlation_id = "x"

    data = json.loads(JsonFormatter().format(record))
    assert "exception" in data
    assert "RuntimeError" in data["exception"]
