"""Ablation: does dropping raw CV text from the authenticity judge save tokens,
and does it degrade the specificity / consistency signals?

For each synthetic (cv_text, ParsedCV) pair we run the judge TWICE:

* ``full``      — the current production prompt (parsed JSON + raw CV text)
* ``json_only`` — same system prompt minus the "and as the raw text" phrase,
                  user message contains only the parsed JSON

Both variants use Haiku 4.5 and ``response_format=JSON``. Redis is stubbed
so this script does not require the compose stack. The cache key changes
between variants (different prompt_version + different system prompt), so
the two calls are independent.

Synthetic inputs are designed to span the rubric:

* CV A — highly specific + internally consistent
* CV B — vague AI-style + internally consistent (specificity should drop)
* CV C — highly specific but inconsistent timeline (consistency should drop)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, cast

import src.ai.claude as claude_module
from src.ai.claude import (
    ClaudeMessage,
    ClaudeResponseFormat,
    call_claude,
)
from src.enums.claude_models import ClaudeModel
from src.schemas.parsed_cv import EducationEntry, ParsedCV, WorkEntry
from src.services.authenticity.llm_judge import (
    MAX_TOKENS,
    SYSTEM_PROMPT_BASE,
    _parsed_cv_summary_json,
)


# --- Redis stub -------------------------------------------------------------


class _NoopRedis:
    async def get(self, key: str) -> None:
        return None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        return None


def _install_noop_redis() -> None:
    claude_module._redis_client = lambda: _NoopRedis()  # type: ignore[assignment]


# --- Token capture ----------------------------------------------------------


@dataclass
class CallRecord:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class Capture:
    calls: list[CallRecord] = field(default_factory=list)


class _UsageHandler(logging.Handler):
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
            )
        )


# --- JSON-only ablation prompt ----------------------------------------------

# Replace the single sentence that promises Claude both inputs; keep the
# rest of the rubric identical so this is a clean ablation.
SYSTEM_PROMPT_JSON_ONLY: str = SYSTEM_PROMPT_BASE.replace(
    "You will be given a\ncandidate's CV — both as a parsed JSON summary and as the raw text —\nand must score two authenticity sub-signals on a 0-100 scale.",
    "You will be given a\ncandidate's CV as a parsed JSON summary and must score two\nauthenticity sub-signals on a 0-100 scale.",
)
assert SYSTEM_PROMPT_JSON_ONLY != SYSTEM_PROMPT_BASE, "system-prompt edit failed"


# --- Synthetic CVs ----------------------------------------------------------


def _cv_a_specific_and_consistent() -> tuple[str, ParsedCV]:
    text = """Layla Hassan
Senior Backend Engineer
layla.hassan@example.com | +971 50 123 4567 | Dubai, UAE

SUMMARY
Backend engineer with 8 years building distributed services at scale.

EXPERIENCE

Senior Backend Engineer, Careem (2021-04 - present, Dubai)
- Led migration of the rides-pricing service from a 12-node Cassandra cluster to DynamoDB,
  cutting P99 latency from 380ms to 92ms and reducing infra spend by $48k/month.
- Owned the payment-reconciliation pipeline (Kafka + Flink, 9M events/day), reducing
  reconciliation drift from 0.42% to 0.06% over six months.
- Mentored 4 mid-level engineers; two were promoted to senior in 2023.

Backend Engineer, Talabat (2018-08 - 2021-03, Berlin)
- Built the restaurant-availability index (Elasticsearch, 240k venues) serving 1.2k RPS at p99 38ms.
- Shipped the merchant onboarding API; cut activation time from 11 days to 36 hours.

Junior Software Engineer, Vodafone (2016-01 - 2018-07, Cairo)
- Implemented the SS7 gateway monitor (Python + Prometheus) covering 28 cell sites.

EDUCATION
BSc Computer Science, Cairo University (2012-2016)
"""
    parsed = ParsedCV(
        full_name="Layla Hassan",
        email="layla.hassan@example.com",
        phone="+971501234567",
        skills=["python", "kafka", "flink", "dynamodb", "cassandra", "elasticsearch", "prometheus"],
        work_history=[
            WorkEntry(
                company="Careem",
                title="Senior Backend Engineer",
                start="2021-04",
                end=None,
                description=(
                    "Led Cassandra-to-DynamoDB migration of rides-pricing (P99 380ms -> 92ms, "
                    "-$48k/mo). Owned Kafka+Flink reconciliation pipeline (9M events/day, drift "
                    "0.42% -> 0.06%). Mentored 4 engineers."
                ),
            ),
            WorkEntry(
                company="Talabat",
                title="Backend Engineer",
                start="2018-08",
                end="2021-03",
                description=(
                    "Built restaurant-availability Elasticsearch index (240k venues, 1.2k RPS, "
                    "p99 38ms). Shipped merchant onboarding API; activation 11d -> 36h."
                ),
            ),
            WorkEntry(
                company="Vodafone",
                title="Junior Software Engineer",
                start="2016-01",
                end="2018-07",
                description="Built SS7 gateway monitor (Python, Prometheus, 28 cell sites).",
            ),
        ],
        education=[
            EducationEntry(
                institution="Cairo University",
                degree="BSc",
                field="Computer Science",
                start="2012",
                end="2016",
            )
        ],
        total_experience_years=8.5,
        languages=["en", "ar"],
    )
    return text, parsed


def _cv_b_vague_but_consistent() -> tuple[str, ParsedCV]:
    text = """Ahmed Khan
Senior Software Engineer
ahmed.khan@example.com | +44 7700 900111 | London, UK

SUMMARY
Results-driven software engineer with a passion for innovative solutions and a track record
of driving impactful outcomes across complex organizations. Leverages cutting-edge
technologies to deliver world-class products.

EXPERIENCE

Senior Software Engineer, GlobalTech (2020 - present)
- Led cross-functional initiatives to drive product excellence and operational efficiency.
- Leveraged best-in-class engineering practices to deliver high-impact outcomes.
- Drove transformational change across multiple teams and stakeholders.
- Spearheaded modernization efforts to align with strategic business objectives.

Software Engineer, InnovateCorp (2017 - 2020)
- Contributed to mission-critical initiatives driving organizational success.
- Collaborated with cross-functional teams to deliver innovative solutions.
- Optimized processes to enhance overall efficiency and impact.

Junior Software Engineer, StartupX (2015 - 2017)
- Supported strategic initiatives and contributed to team success.
- Assisted in delivering quality solutions to drive business outcomes.

EDUCATION
BSc Computer Science, Imperial College London (2011-2015)
"""
    parsed = ParsedCV(
        full_name="Ahmed Khan",
        email="ahmed.khan@example.com",
        phone="+447700900111",
        skills=["software engineering", "leadership", "innovation"],
        work_history=[
            WorkEntry(
                company="GlobalTech",
                title="Senior Software Engineer",
                start="2020",
                end=None,
                description=(
                    "Led cross-functional initiatives. Leveraged best-in-class practices. "
                    "Drove transformational change. Spearheaded modernization."
                ),
            ),
            WorkEntry(
                company="InnovateCorp",
                title="Software Engineer",
                start="2017",
                end="2020",
                description=(
                    "Contributed to mission-critical initiatives. Collaborated cross-functionally. "
                    "Optimized processes."
                ),
            ),
            WorkEntry(
                company="StartupX",
                title="Junior Software Engineer",
                start="2015",
                end="2017",
                description="Supported strategic initiatives. Assisted in delivering quality solutions.",
            ),
        ],
        education=[
            EducationEntry(
                institution="Imperial College London",
                degree="BSc",
                field="Computer Science",
                start="2011",
                end="2015",
            )
        ],
        total_experience_years=9.0,
        languages=["en"],
    )
    return text, parsed


def _cv_c_specific_but_inconsistent() -> tuple[str, ParsedCV]:
    text = """Sara Lopez
Lead Platform Engineer
sara.lopez@example.com | +34 612 345 678 | Madrid

SUMMARY
Platform engineer (1.5 years experience) currently leading the SRE platform org at Telefonica.

EXPERIENCE

Lead Platform Engineer, Telefonica (2024-06 - present, Madrid)
- Lead a team of 9 SREs running 240+ Kubernetes clusters across 6 regions.
- Designed the multi-cluster service mesh (Istio 1.21) cutting tail-latency p99 by 28%.
- Own the platform SLO program: 99.95% control-plane availability since Q4-2024.

Junior Cloud Engineer, Acme SaaS (2023-09 - 2024-05, Madrid)
- Built Terraform modules for the staging environment; reduced bootstrap time from 4h to 22min.
- Triaged 612 PagerDuty incidents over 8 months as part of the L2 rotation.

EDUCATION
MSc Distributed Systems, UPM Madrid (2021-2023)
"""
    # NOTE the inconsistency: 1.5 yrs total experience but title is "Lead" managing 9 SREs.
    parsed = ParsedCV(
        full_name="Sara Lopez",
        email="sara.lopez@example.com",
        phone="+34612345678",
        skills=["kubernetes", "istio", "terraform", "sre", "pagerduty"],
        work_history=[
            WorkEntry(
                company="Telefonica",
                title="Lead Platform Engineer",
                start="2024-06",
                end=None,
                description=(
                    "Leads 9-person SRE team across 240+ K8s clusters in 6 regions. "
                    "Designed Istio 1.21 multi-cluster mesh; p99 -28%. SLO program "
                    "99.95% control-plane availability."
                ),
            ),
            WorkEntry(
                company="Acme SaaS",
                title="Junior Cloud Engineer",
                start="2023-09",
                end="2024-05",
                description=(
                    "Terraform modules for staging; bootstrap 4h -> 22min. Triaged 612 "
                    "PagerDuty incidents on L2 rotation."
                ),
            ),
        ],
        education=[
            EducationEntry(
                institution="UPM Madrid",
                degree="MSc",
                field="Distributed Systems",
                start="2021",
                end="2023",
            )
        ],
        total_experience_years=1.5,
        languages=["es", "en"],
    )
    return text, parsed


CVS: list[tuple[str, str, ParsedCV]] = [
    ("A — specific + consistent", *_cv_a_specific_and_consistent()),
    ("B — vague + consistent", *_cv_b_vague_but_consistent()),
    ("C — specific + INCONSISTENT", *_cv_c_specific_but_inconsistent()),
]


# --- Judge runners ----------------------------------------------------------


async def _judge_full(cv_text: str, parsed_cv: ParsedCV) -> dict[str, Any]:
    parsed_json = _parsed_cv_summary_json(parsed_cv)
    user_message = (
        "PARSED CV (structured summary)\n"
        "==============================\n"
        f"{parsed_json}\n\n"
        "CV TEXT\n"
        "=======\n"
        f"{cv_text}\n\n"
        "TASK\n"
        "====\n"
        "Score the two authenticity sub-signals on the rubric in the system\n"
        "instructions, using both the structured summary and the raw CV text\n"
        "above. Output JSON only — no prose, no markdown fences.\n"
    )
    response = await call_claude(
        model=ClaudeModel.HAIKU,
        system=SYSTEM_PROMPT_BASE,
        messages=[ClaudeMessage(role="user", content=user_message)],
        max_tokens=MAX_TOKENS,
        response_format=ClaudeResponseFormat.JSON,
        prompt_version="authenticity_judge:ablation:full:v1",
    )
    return cast(dict[str, Any], response)


async def _judge_json_only(parsed_cv: ParsedCV) -> dict[str, Any]:
    parsed_json = _parsed_cv_summary_json(parsed_cv)
    user_message = (
        "PARSED CV (structured summary)\n"
        "==============================\n"
        f"{parsed_json}\n\n"
        "TASK\n"
        "====\n"
        "Score the two authenticity sub-signals on the rubric in the system\n"
        "instructions, using only the structured summary above. Output JSON\n"
        "only — no prose, no markdown fences.\n"
    )
    response = await call_claude(
        model=ClaudeModel.HAIKU,
        system=SYSTEM_PROMPT_JSON_ONLY,
        messages=[ClaudeMessage(role="user", content=user_message)],
        max_tokens=MAX_TOKENS,
        response_format=ClaudeResponseFormat.JSON,
        prompt_version="authenticity_judge:ablation:json_only:v1",
    )
    return cast(dict[str, Any], response)


def _scores(resp: dict[str, Any]) -> tuple[int | None, int | None]:
    spec = resp.get("specificity")
    cons = resp.get("consistency")
    sscore = spec.get("score") if isinstance(spec, dict) else None
    cscore = cons.get("score") if isinstance(cons, dict) else None
    return sscore, cscore


# --- Main -------------------------------------------------------------------


async def run() -> None:
    _install_noop_redis()

    capture = Capture()
    handler = _UsageHandler(capture)
    logging.getLogger("src.ai.claude").addHandler(handler)
    logging.getLogger("src.ai.claude").setLevel(logging.INFO)

    rows: list[dict[str, Any]] = []
    for label, cv_text, parsed_cv in CVS:
        # --- full variant (current production) ---
        n0 = len(capture.calls)
        t0 = time.perf_counter()
        full = await _judge_full(cv_text, parsed_cv)
        full_ms = (time.perf_counter() - t0) * 1000
        full_calls = capture.calls[n0:]
        full_in = sum((c.input_tokens or 0) for c in full_calls)
        full_out = sum((c.output_tokens or 0) for c in full_calls)
        full_spec, full_cons = _scores(full)

        # --- json-only variant ---
        n1 = len(capture.calls)
        t1 = time.perf_counter()
        jonly = await _judge_json_only(parsed_cv)
        jonly_ms = (time.perf_counter() - t1) * 1000
        jonly_calls = capture.calls[n1:]
        jonly_in = sum((c.input_tokens or 0) for c in jonly_calls)
        jonly_out = sum((c.output_tokens or 0) for c in jonly_calls)
        jonly_spec, jonly_cons = _scores(jonly)

        rows.append(
            {
                "label": label,
                "full_in": full_in,
                "full_out": full_out,
                "full_ms": full_ms,
                "full_spec": full_spec,
                "full_cons": full_cons,
                "full_spec_reason": (full.get("specificity") or {}).get("reasoning", ""),
                "full_cons_reason": (full.get("consistency") or {}).get("reasoning", ""),
                "jonly_in": jonly_in,
                "jonly_out": jonly_out,
                "jonly_ms": jonly_ms,
                "jonly_spec": jonly_spec,
                "jonly_cons": jonly_cons,
                "jonly_spec_reason": (jonly.get("specificity") or {}).get("reasoning", ""),
                "jonly_cons_reason": (jonly.get("consistency") or {}).get("reasoning", ""),
            }
        )

    # --- per-CV table ---
    print()
    print("=" * 110)
    print("PER-CV RESULTS")
    print("=" * 110)
    header = f"{'CV':<32} {'variant':<10} {'in':>6} {'out':>5} {'ms':>7} {'spec':>5} {'cons':>5}"
    print(header)
    print("-" * 110)
    for r in rows:
        print(
            f"{r['label']:<32} {'full':<10} {r['full_in']:>6} {r['full_out']:>5} "
            f"{r['full_ms']:>7.0f} {str(r['full_spec']):>5} {str(r['full_cons']):>5}"
        )
        print(
            f"{'':<32} {'json_only':<10} {r['jonly_in']:>6} {r['jonly_out']:>5} "
            f"{r['jonly_ms']:>7.0f} {str(r['jonly_spec']):>5} {str(r['jonly_cons']):>5}"
        )
        # token + score deltas
        spec_delta = (
            (r["jonly_spec"] - r["full_spec"])
            if (r["jonly_spec"] is not None and r["full_spec"] is not None)
            else None
        )
        cons_delta = (
            (r["jonly_cons"] - r["full_cons"])
            if (r["jonly_cons"] is not None and r["full_cons"] is not None)
            else None
        )
        in_pct = (
            (1 - r["jonly_in"] / r["full_in"]) * 100 if r["full_in"] else 0.0
        )
        spec_d = f"{spec_delta:+d}" if isinstance(spec_delta, int) else "n/a"
        cons_d = f"{cons_delta:+d}" if isinstance(cons_delta, int) else "n/a"
        print(
            f"{'  delta':<32} {'':<10} {r['jonly_in'] - r['full_in']:>+6d} "
            f"{r['jonly_out'] - r['full_out']:>+5d} {r['jonly_ms'] - r['full_ms']:>+7.0f} "
            f"{spec_d:>5} {cons_d:>5}   (input -{in_pct:.0f}%)"
        )
        print()

    # --- reasoning side-by-side ---
    print("=" * 110)
    print("REASONING (truncated to 240 chars each)")
    print("=" * 110)
    for r in rows:
        print(f"\n--- {r['label']} ---")
        print(f"  full      specificity ({r['full_spec']}): {r['full_spec_reason'][:240]}")
        print(f"  json_only specificity ({r['jonly_spec']}): {r['jonly_spec_reason'][:240]}")
        print(f"  full      consistency ({r['full_cons']}): {r['full_cons_reason'][:240]}")
        print(f"  json_only consistency ({r['jonly_cons']}): {r['jonly_cons_reason'][:240]}")

    # --- aggregates + cost ---
    full_in_total = sum(r["full_in"] for r in rows)
    full_out_total = sum(r["full_out"] for r in rows)
    jonly_in_total = sum(r["jonly_in"] for r in rows)
    jonly_out_total = sum(r["jonly_out"] for r in rows)

    # Haiku 4.5 pricing: $1.00 / Mtok input, $5.00 / Mtok output
    full_cost = full_in_total * 1e-6 + full_out_total * 5e-6
    jonly_cost = jonly_in_total * 1e-6 + jonly_out_total * 5e-6

    print()
    print("=" * 110)
    print("AGGREGATES")
    print("=" * 110)
    print(
        f"full      : in={full_in_total:>6}  out={full_out_total:>5}  cost=${full_cost:.5f}"
    )
    print(
        f"json_only : in={jonly_in_total:>6}  out={jonly_out_total:>5}  cost=${jonly_cost:.5f}"
    )
    print(
        f"reduction : in={(1 - jonly_in_total / full_in_total) * 100:.1f}%  "
        f"cost={(1 - jonly_cost / full_cost) * 100:.1f}%   "
        f"saving={full_cost - jonly_cost:.5f} USD across {len(rows)} CVs "
        f"(~${(full_cost - jonly_cost) / len(rows):.5f}/CV)"
    )

    # Raw JSON dump for the record so the deltas can be re-inspected later.
    print()
    print("=" * 110)
    print("RAW (json dump)")
    print("=" * 110)
    print(json.dumps(rows, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(run())
