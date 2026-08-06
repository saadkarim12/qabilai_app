"""Tests for Settings validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings


def test_app_secret_key_minimum_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "too-short")
    with pytest.raises(ValidationError):
        Settings()


def test_app_env_must_be_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings()


def test_similarity_threshold_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMILARITY_REJECTION_THRESHOLD", "150")
    with pytest.raises(ValidationError):
        Settings()


def test_similarity_threshold_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIMILARITY_REJECTION_THRESHOLD", "-0.1")
    with pytest.raises(ValidationError):
        Settings()


def test_smtp_port_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_PORT", "99999")
    with pytest.raises(ValidationError):
        Settings()


def test_cors_origins_parses_comma_separated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    cfg = Settings()
    assert cfg.cors_origins == ["https://a.example", "https://b.example"]


def test_optional_secrets_default_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # Disable `.env` loading and clear any matching env vars so the test
    # asserts what it actually means: "given no input, these default to None."
    # Without this, a developer's populated local `.env` would mask the test.
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AZURE_BLOB_CONNECTION_STRING",
        "META_WA_ACCESS_TOKEN",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = Settings(_env_file=None)  # type: ignore[call-arg]
    assert cfg.openai_api_key is None
    assert cfg.anthropic_api_key is None
    assert cfg.azure_blob_connection_string is None
    assert cfg.meta_wa_access_token is None
    assert cfg.google_service_account_json is None


def test_is_production_property(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert Settings().is_production is True
    monkeypatch.setenv("APP_ENV", "development")
    assert Settings().is_production is False
