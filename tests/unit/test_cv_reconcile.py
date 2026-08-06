"""Unit tests for ``_reconcile_candidate_identity`` (no DB).

The helper corrects a candidate's display name from ``parse_cv``'s
authoritative output (fixing the "section heading mistaken for a name" bug
where the synchronous contact-extractor heuristic latches onto e.g. "Core
Competencies"), while deliberately leaving the email/phone identity untouched.
"""

from __future__ import annotations

from src.db.models.candidate import Candidate
from src.schemas.parsed_cv import ParsedCV
from src.workers.tasks.cv_pipeline import _reconcile_candidate_identity


def _candidate(*, full_name: str) -> Candidate:
    return Candidate(
        email="cand@example.com",
        phone_e164="+971501234567",
        full_name=full_name,
        parsed_profile={},
    )


def _parsed(*, full_name: str | None, email: str | None, phone: str | None) -> ParsedCV:
    return ParsedCV(full_name=full_name, email=email, phone=phone)


def test_overwrites_heuristic_section_heading_name() -> None:
    candidate = _candidate(full_name="Core Competencies")
    parsed = _parsed(full_name="Real Candidate", email="cand@example.com", phone="+971501234567")

    _reconcile_candidate_identity(candidate, parsed)

    assert candidate.full_name == "Real Candidate"


def test_identity_email_phone_never_mutated() -> None:
    candidate = _candidate(full_name="Core Competencies")
    # parse_cv reports a *different* email/phone — the stub must not touch them.
    parsed = _parsed(full_name="Real Candidate", email="other@example.com", phone="+971509999999")

    _reconcile_candidate_identity(candidate, parsed)

    assert candidate.email == "cand@example.com"
    assert candidate.phone_e164 == "+971501234567"


def test_no_overwrite_when_parsed_name_empty() -> None:
    candidate = _candidate(full_name="Existing Name")
    parsed = _parsed(full_name=None, email=None, phone=None)

    _reconcile_candidate_identity(candidate, parsed)

    assert candidate.full_name == "Existing Name"


def test_no_change_when_names_equal_ignoring_whitespace() -> None:
    candidate = _candidate(full_name="Jane Doe")
    parsed = _parsed(full_name="  Jane Doe  ", email=None, phone=None)

    _reconcile_candidate_identity(candidate, parsed)

    assert candidate.full_name == "Jane Doe"
