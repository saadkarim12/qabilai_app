"""Integration tests for the global exception handlers."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.config import Settings
from src.exceptions import KabilError
from src.main import create_app
from src.utils.correlation_id import CORRELATION_ID_HEADER

EnvName = Literal["development", "test", "production"]

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class _EchoBody(BaseModel):
    name: str
    age: int


def _build_client(env: EnvName) -> TestClient:
    """Spin up an app under the requested APP_ENV with throwaway test routes."""
    app = create_app(Settings(app_env=env))

    @app.post("/_echo")
    async def echo(body: _EchoBody) -> dict[str, Any]:
        return {"name": body.name, "age": body.age}

    @app.get("/_boom")
    async def boom() -> None:
        raise RuntimeError("internal exception detail")

    @app.get("/_domain")
    async def domain() -> None:
        raise KabilError(
            "candidate not found",
            http_status=404,
            error_code="candidate_not_found",
            details={"id": "abc"},
        )

    return TestClient(app, raise_server_exceptions=False)


def test_validation_error_returns_422_with_field_details() -> None:
    client = _build_client("test")
    response = client.post("/_echo", json={"name": "x"})  # missing 'age'
    assert response.status_code == 422

    body = response.json()
    assert body["error"] == "validation_error"
    assert any(d["loc"] == ["body", "age"] for d in body["details"])
    assert body["correlation_id"]


def test_unhandled_exception_is_sanitized_in_production() -> None:
    client = _build_client("production")
    response = client.get("/_boom")
    assert response.status_code == 500

    body = response.json()
    assert body["error"] == "internal_server_error"
    assert "internal exception detail" not in body["message"]
    assert "exception" not in body  # type only leaks in dev/test
    assert body["correlation_id"]


def test_unhandled_exception_is_verbose_in_dev() -> None:
    client = _build_client("development")
    response = client.get("/_boom")
    assert response.status_code == 500

    body = response.json()
    assert body["error"] == "internal_server_error"
    assert body["message"] == "internal exception detail"
    assert body["exception"] == "RuntimeError"


def test_domain_error_uses_kabil_fields() -> None:
    client = _build_client("development")
    response = client.get("/_domain")
    assert response.status_code == 404

    body = response.json()
    assert body["error"] == "candidate_not_found"
    assert body["message"] == "candidate not found"
    assert body["details"] == {"id": "abc"}


def test_response_includes_correlation_id_header() -> None:
    client = _build_client("development")
    response = client.get("/_boom")
    cid = response.headers[CORRELATION_ID_HEADER]
    assert _UUID_RE.match(cid)
    assert response.json()["correlation_id"] == cid


def test_incoming_correlation_id_is_preserved() -> None:
    client = _build_client("development")
    response = client.get("/_boom", headers={CORRELATION_ID_HEADER: "my-cid"})
    assert response.headers[CORRELATION_ID_HEADER] == "my-cid"
    assert response.json()["correlation_id"] == "my-cid"
