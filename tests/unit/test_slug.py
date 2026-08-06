"""Unit tests for ``src.utils.slug``."""

from __future__ import annotations

import re
import string

from src.utils.slug import SLUG_BYTES, generate_public_slug


def test_slug_is_string() -> None:
    assert isinstance(generate_public_slug(), str)


def test_slug_length_matches_url_safe_encoding() -> None:
    slug = generate_public_slug()
    # ``secrets.token_urlsafe(n)`` returns ceil(4n/3) chars; for n=12 → 16.
    assert len(slug) == (SLUG_BYTES * 4) // 3


def test_slug_fits_public_slug_column() -> None:
    # ``Job.public_slug`` is String(64).
    assert len(generate_public_slug()) <= 64


def test_slug_uses_only_url_safe_alphabet() -> None:
    allowed = set(string.ascii_letters + string.digits + "-_")
    slug = generate_public_slug()
    assert set(slug) <= allowed


def test_slug_pattern_matches_regex() -> None:
    assert re.fullmatch(r"[A-Za-z0-9_-]{16}", generate_public_slug())


def test_slugs_are_unique_across_many_calls() -> None:
    # 5_000 draws at 96 bits of entropy → collision probability is
    # vanishingly small; if this ever flakes the RNG is broken.
    seen = {generate_public_slug() for _ in range(5_000)}
    assert len(seen) == 5_000
