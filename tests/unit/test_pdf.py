"""Unit tests for ``src.utils.pdf``.

pypdf is mocked for all behavioural tests (cache, timeout, error paths).
A single end-to-end test loads ``tests/fixtures/sample_en.pdf`` to prove
the wiring against the real library.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import pytest
from pypdf.errors import PdfReadError

from src.utils import pdf

# --- Fakes -----------------------------------------------------------------


class _FakeRedis:
    """Minimal async stand-in for redis.asyncio.Redis."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ex))


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    redis = _FakeRedis()
    monkeypatch.setattr(pdf, "_redis_client", lambda: redis)
    return redis


# --- Empty / trivial -------------------------------------------------------


async def test_empty_bytes_returns_empty_without_redis(fake_redis: _FakeRedis) -> None:
    result = await pdf.extract_text(b"")
    assert result == ""
    # Empty input shouldn't even consult Redis.
    assert fake_redis.get_calls == []
    assert fake_redis.set_calls == []


# --- Caching ---------------------------------------------------------------


async def test_cache_hit_short_circuits_pypdf(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_bytes = b"%PDF-1.4 fake"
    key = pdf._cache_key(pdf_bytes)
    fake_redis.store[key] = "cached body text"

    call_count = 0

    def boom(_: bytes) -> str:
        nonlocal call_count
        call_count += 1
        return "should not run"

    monkeypatch.setattr(pdf, "_extract_sync", boom)

    result = await pdf.extract_text(pdf_bytes)

    assert result == "cached body text"
    assert call_count == 0
    # Cache hit must not write back.
    assert fake_redis.set_calls == []


async def test_cache_miss_writes_with_thirty_day_ttl(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pdf, "_extract_sync", lambda _: "fresh text")

    await pdf.extract_text(b"%PDF-1.4 fake")

    [(key, value, ex)] = fake_redis.set_calls
    assert value == "fresh text"
    assert ex == pdf.EXTRACTION_CACHE_TTL_SECONDS
    assert ex == 30 * 24 * 60 * 60
    assert key.startswith(pdf.EXTRACTION_CACHE_KEY_PREFIX)


async def test_cache_key_is_sha256_of_bytes(fake_redis: _FakeRedis) -> None:
    pdf_bytes = b"some pdf bytes here"
    expected = pdf.EXTRACTION_CACHE_KEY_PREFIX + hashlib.sha256(pdf_bytes).hexdigest()
    assert pdf._cache_key(pdf_bytes) == expected
    suffix = expected.removeprefix(pdf.EXTRACTION_CACHE_KEY_PREFIX)
    assert len(suffix) == 64
    int(suffix, 16)  # must parse as hex


async def test_same_bytes_produce_same_cache_key() -> None:
    a = pdf._cache_key(b"%PDF-1.4 hello")
    b = pdf._cache_key(b"%PDF-1.4 hello")
    assert a == b


async def test_empty_extraction_result_is_cached(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupt PDF returns '' from _extract_sync — we still cache it so we
    don't re-run pypdf on every re-upload."""
    monkeypatch.setattr(pdf, "_extract_sync", lambda _: "")

    await pdf.extract_text(b"%PDF-broken")

    assert len(fake_redis.set_calls) == 1
    _key, value, _ttl = fake_redis.set_calls[0]
    assert value == ""


# --- Truncation ------------------------------------------------------------


def test_extract_sync_truncates_at_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Output longer than MAX_EXTRACTED_TEXT_CHARS must be clipped."""
    # Build a fake reader returning oversized text.
    oversized = "a" * (pdf.MAX_EXTRACTED_TEXT_CHARS + 5_000)

    class _FakePage:
        def extract_text(self) -> str:
            return oversized

    class _FakeReader:
        def __init__(self, _stream: Any) -> None:
            self.pages = [_FakePage()]

    monkeypatch.setattr(pdf, "PdfReader", _FakeReader)
    out = pdf._extract_sync(b"%PDF-1.4 x")
    assert len(out) == pdf.MAX_EXTRACTED_TEXT_CHARS


# --- Error handling --------------------------------------------------------


def test_extract_sync_swallows_pypdf_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def raises_pdfreaderror(_: Any) -> None:
        raise PdfReadError("malformed xref")

    monkeypatch.setattr(pdf, "PdfReader", raises_pdfreaderror)
    assert pdf._extract_sync(b"%PDF-broken") == ""


def test_extract_sync_swallows_generic_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def raises_generic(_: Any) -> None:
        raise KeyError("missing object")

    monkeypatch.setattr(pdf, "PdfReader", raises_generic)
    assert pdf._extract_sync(b"%PDF-weird") == ""


# --- Timeout ---------------------------------------------------------------


async def test_timeout_raises_pdf_extraction_timeout(
    fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If pypdf hangs past PDF_EXTRACTION_TIMEOUT_SECONDS, the wrapper
    raises PdfExtractionTimeoutError rather than blocking forever.

    Drops the wall-clock cap to 50 ms and makes _extract_sync sleep 500 ms
    so real asyncio.wait_for + asyncio.to_thread fire (no internals patched).
    """
    monkeypatch.setattr(pdf, "PDF_EXTRACTION_TIMEOUT_SECONDS", 0.05)

    def slow_extract(_: bytes) -> str:
        time.sleep(0.5)
        return "should never appear"

    monkeypatch.setattr(pdf, "_extract_sync", slow_extract)

    with pytest.raises(pdf.PdfExtractionTimeoutError):
        await pdf.extract_text(b"%PDF-1.4 slow")

    # Timeout must NOT write to cache.
    assert fake_redis.set_calls == []


async def test_timeout_constant_is_ten_seconds_per_doc() -> None:
    assert pdf.PDF_EXTRACTION_TIMEOUT_SECONDS == 10


# --- Constants -------------------------------------------------------------


def test_constants_match_doc() -> None:
    assert pdf.PDF_EXTRACTION_TIMEOUT_SECONDS == 10
    assert pdf.MAX_EXTRACTED_TEXT_CHARS == 50_000
    assert pdf.EXTRACTION_CACHE_TTL_SECONDS == 30 * 24 * 60 * 60
    assert pdf.EXTRACTION_CACHE_KEY_PREFIX == "pdf:extract:v1:sha256:"


# --- End-to-end with real fixture PDF --------------------------------------


async def test_real_pdf_fixture_extracts_more_than_one_hundred_chars(
    fake_redis: _FakeRedis,
) -> None:
    """Regression: prove pypdf is actually wired in (no monkeypatching)."""
    fixture = Path(__file__).parent.parent / "fixtures" / "sample_en.pdf"
    pdf_bytes = fixture.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-")

    text = await pdf.extract_text(pdf_bytes)

    # Acceptance criterion from the doc: > 100 chars.
    assert len(text) > 100
    assert "Senior backend engineer" in text
    # And it landed in the cache for next time.
    assert len(fake_redis.set_calls) == 1


# --- Page-1 signals (font-size-ranked names + de-glued text) ----------------


def _pdf_with_sized_lines(lines: list[tuple[str, int]]) -> bytes:
    """Build a one-page PDF, each line at the given font size (points)."""
    from fpdf import FPDF

    doc = FPDF()
    doc.add_page()
    for text, size in lines:
        doc.set_font("helvetica", size=size)
        doc.cell(0, size * 0.6, text, new_x="LMARGIN", new_y="NEXT")
    return bytes(doc.output())


async def test_name_candidates_rank_largest_font_first() -> None:
    """The biggest text on page 1 leads, regardless of its position in flow.

    The title banner is emitted before the name but in a smaller font; the
    name must still rank first. This is the signal extract_text discards.
    """
    pdf_bytes = _pdf_with_sized_lines(
        [
            ("Full Stack Software Engineer", 18),
            ("Education", 16),
            ("Muhammad Awais", 36),
            ("body text here", 11),
        ]
    )
    signals = await pdf.extract_page1_signals(pdf_bytes)
    assert signals.name_candidates[0] == "Muhammad Awais"
    # Descending font order, capped at the documented limit.
    assert signals.name_candidates[:2] == ["Muhammad Awais", "Full Stack Software Engineer"]
    assert len(signals.name_candidates) <= pdf.NAME_CANDIDATE_LIMIT


async def test_deglued_text_separates_label_from_glued_email() -> None:
    """A label placed adjacent to an address is split apart in deglued_text.

    pypdf's flattened extract_text glues ``Email`` to the address; the visitor
    keeps them as separate runs, so deglued_text space-joins them and an email
    regex can find the clean address.
    """
    pdf_bytes = _pdf_with_sized_lines(
        [
            ("Muhammad Awais", 36),
            ("Email", 12),
            ("muhammadawais1000@gmail.com", 12),
        ]
    )
    signals = await pdf.extract_page1_signals(pdf_bytes)
    # The label and address are distinct, space-separated tokens (not glued).
    assert "Email muhammadawais1000@gmail.com" in signals.deglued_text
    assert "Emailmuhammadawais1000@gmail.com" not in signals.deglued_text


async def test_page1_signals_empty_bytes_returns_empty() -> None:
    signals = await pdf.extract_page1_signals(b"")
    assert signals.name_candidates == []
    assert signals.deglued_text == ""


async def test_page1_signals_corrupt_pdf_returns_empty() -> None:
    # Non-PDF bytes: pypdf raises, we degrade to empty signals rather than crash.
    signals = await pdf.extract_page1_signals(b"not a pdf at all")
    assert signals.name_candidates == []
    assert signals.deglued_text == ""
