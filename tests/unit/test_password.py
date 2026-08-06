"""Tests for password hashing utilities."""

from __future__ import annotations

from src.utils.password import hash_password, verify_password


def test_hash_returns_bcrypt_string() -> None:
    h = hash_password("hunter2")
    # bcrypt 2b variant prefix; rounds=12 → "$2b$12$..."
    assert h.startswith("$2b$12$")
    assert len(h) == 60  # bcrypt fixed length


def test_hashing_same_password_yields_different_hashes() -> None:
    # Different salts ⇒ different hashes for the same plaintext.
    assert hash_password("hunter2") != hash_password("hunter2")


def test_verify_password_round_trip() -> None:
    assert verify_password("hunter2", hash_password("hunter2")) is True


def test_verify_password_rejects_wrong_password() -> None:
    assert verify_password("wrong", hash_password("hunter2")) is False


def test_verify_password_handles_malformed_hash_without_raising() -> None:
    assert verify_password("anything", "not-a-bcrypt-hash") is False
