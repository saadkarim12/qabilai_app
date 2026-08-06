"""One-off measurement for the deterministic + LLM-fallback contact extractor.

Runs 5 synthetic CV snippets through ``extract_contact_details`` and reports
per-CV path (deterministic vs LLM), wall-clock latency, and (for LLM-fallback
cases) the ``input_tokens`` / ``output_tokens`` reported by Anthropic.

Redis is stubbed to no-op so this script does not require the compose stack.
Anthropic API calls are real — runs of this script cost roughly one Haiku
turn per LLM-fallback CV (~$0.001 each at current Haiku pricing).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import src.ai.claude as claude_module
from src.ai.contact_extractor import ContactDetails, extract_contact_details


class _NoopRedis:
    """Stand-in for ``redis.asyncio.Redis``. Reports nothing cached, writes nothing."""

    async def get(self, key: str) -> None:
        return None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        return None


def _install_noop_redis() -> None:
    claude_module._redis_client = lambda: _NoopRedis()  # type: ignore[assignment]


@dataclass
class CallRecord:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_bypassed: bool | None = None


@dataclass
class Capture:
    calls: list[CallRecord] = field(default_factory=list)


class _UsageHandler(logging.Handler):
    """Capture token counts from ``claude.api_call`` log records."""

    def __init__(self, capture: Capture) -> None:
        super().__init__(level=logging.INFO)
        self.capture = capture

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() != "claude.api_call":
            return
        self.capture.calls.append(
            CallRecord(
                input_tokens=getattr(record, "input_tokens", None),
                output_tokens=getattr(record, "output_tokens", None),
                cache_bypassed=getattr(record, "cache_bypassed", None),
            )
        )


# Five synthetic CV snippets covering both paths.
CVS: list[tuple[str, str]] = [
    (
        "clean / international phone (deterministic path)",
        "Layla Hassan\n"
        "Senior Backend Engineer\n"
        "layla.hassan@example.com | +971 50 123 4567 | Dubai, UAE\n\n"
        "SUMMARY\n"
        "Backend engineer with 8 years building distributed services.\n",
    ),
    (
        "clean / different name (deterministic path)",
        "Mohammed Al-Hassan\n"
        "Data Engineer\n"
        "m.alhassan@datacorp.io | +44 7700 900123 | London, UK\n\n"
        "EXPERIENCE\n"
        "Designed real-time ETL pipelines.\n",
    ),
    (
        "multi-email (LLM fallback)",
        "Priya Raman\n"
        "Full-Stack Developer\n"
        "priya.raman@example.com | +1 415 555 0142\n"
        "Referee: prof.smith@stanford.edu\n\n"
        "SKILLS\n"
        "React, Node, PostgreSQL.\n",
    ),
    (
        "national-format phone, no '+' (LLM fallback)",
        "Ahmed Khan\n"
        "Mobile Developer\n"
        "ahmed.khan@mobiledev.co | 0501234567 | Riyadh\n\n"
        "EXPERIENCE\n"
        "iOS and Android applications.\n",
    ),
    (
        "name-line absent, contact only (LLM fallback)",
        "CURRICULUM VITAE\n"
        "==================\n"
        "Email: sara.lopez@example.org\n"
        "Phone: +34 612 345 678\n\n"
        "About me: senior product designer with a background in fintech.\n"
        "I am Sara Lopez and I have worked across 4 startups.\n",
    ),
]


async def run() -> None:
    _install_noop_redis()

    capture = Capture()
    handler = _UsageHandler(capture)
    logging.getLogger("src.ai.claude").addHandler(handler)
    logging.getLogger("src.ai.claude").setLevel(logging.INFO)

    print(f"{'#':<2} {'scenario':<48} {'path':<14} {'ms':>7} {'in_tok':>7} {'out_tok':>8}")
    print("-" * 92)

    totals = {"calls": 0, "in_tokens": 0, "out_tokens": 0, "ms": 0.0}
    for idx, (label, cv) in enumerate(CVS, start=1):
        calls_before = len(capture.calls)
        t0 = time.perf_counter()
        result: ContactDetails = await extract_contact_details(cv)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        new_calls = capture.calls[calls_before:]
        path = "LLM" if new_calls else "deterministic"

        if new_calls:
            in_tok = sum((c.input_tokens or 0) for c in new_calls)
            out_tok = sum((c.output_tokens or 0) for c in new_calls)
            in_s: Any = in_tok
            out_s: Any = out_tok
            totals["calls"] += len(new_calls)
            totals["in_tokens"] += in_tok
            totals["out_tokens"] += out_tok
        else:
            in_s = "-"
            out_s = "-"
        totals["ms"] += elapsed_ms

        print(
            f"{idx:<2} {label:<48} {path:<14} {elapsed_ms:>7.1f} {in_s:>7} {out_s:>8}"
            f"   name={result.full_name!r} email={result.email!r} phone={result.phone!r}"
        )

    print("-" * 92)
    print(
        f"totals  {'':48} api_calls={totals['calls']}"
        f"  in_tokens={totals['in_tokens']}  out_tokens={totals['out_tokens']}"
        f"  total_ms={totals['ms']:.1f}"
    )


if __name__ == "__main__":
    asyncio.run(run())
