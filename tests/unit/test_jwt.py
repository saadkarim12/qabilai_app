"""Tests for JWT signing / verification and token hashing."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from jose import jwt as jose_jwt

from src.exceptions import InvalidTokenError
from src.utils.jwt import (
    create_access_token,
    decode_access_token,
    hash_token,
)


def test_round_trip_preserves_identifiers() -> None:
    user_id = uuid4()
    session_id = uuid4()
    token = create_access_token(user_id, session_id)
    payload = decode_access_token(token)

    assert payload.user_id == user_id
    assert payload.session_id == session_id


def test_expired_token_raises() -> None:
    token = create_access_token(uuid4(), uuid4(), expires_in=timedelta(seconds=-1))
    with pytest.raises(InvalidTokenError, match="expired"):
        decode_access_token(token)


def test_token_signed_with_other_secret_raises() -> None:
    token = jose_jwt.encode(
        {"sub": str(uuid4()), "sid": str(uuid4()), "exp": 9999999999},
        "different-secret-of-at-least-32-bytes-yes",
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_access_token(token)


def test_tampered_token_raises() -> None:
    token = create_access_token(uuid4(), uuid4())
    # Replace the signature segment entirely so verification can't possibly pass.
    header, payload, _signature = token.split(".")
    tampered = ".".join([header, payload, "AAAAAAAAAAAAAAAAAAAAAAAA"])
    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered)


def test_malformed_string_raises() -> None:
    with pytest.raises(InvalidTokenError):
        decode_access_token("not.a.jwt")


def test_payload_with_non_uuid_sub_raises() -> None:
    token = jose_jwt.encode(
        {"sub": "not-a-uuid", "sid": str(uuid4()), "exp": 9999999999},
        # Match the real signing secret so we get past signature check first.
        _current_secret(),
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError, match="Malformed"):
        decode_access_token(token)


def test_hash_token_is_deterministic_64_hex() -> None:
    token = "any-string"
    h1 = hash_token(token)
    h2 = hash_token(token)
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def _current_secret() -> str:
    # Import lazily so the test runs after conftest has populated os.environ.
    from src.config import settings

    return settings.app_secret_key
