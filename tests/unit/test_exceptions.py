"""Tests for the domain exception base class."""

from __future__ import annotations

from src.exceptions import KabilError


def test_kabil_error_defaults() -> None:
    err = KabilError("something broke")
    assert err.message == "something broke"
    assert err.http_status == 500
    assert err.error_code == "internal_server_error"
    assert err.details is None
    assert str(err) == "something broke"


def test_kabil_error_overrides_per_instance() -> None:
    err = KabilError(
        "bad input",
        http_status=400,
        error_code="bad_input",
        details={"field": "email"},
    )
    assert err.http_status == 400
    assert err.error_code == "bad_input"
    assert err.details == {"field": "email"}


def test_subclass_class_defaults_apply() -> None:
    class NotFoundError(KabilError):
        http_status = 404
        error_code = "not_found"

    err = NotFoundError("user missing")
    assert err.http_status == 404
    assert err.error_code == "not_found"
