"""Structured breakdown of a similarity-score result.

Pure functions: given the inputs (parsed CV skills, JD required /
preferred skills, similarity score) compute a JSON-serialisable
breakdown for ``ApplicationScore.breakdown``, and derive a one-line
``rejection_reason`` summary from a stored breakdown for the API
detail response.

Deterministic — no network, no LLM. Skill matching normalises by
lowercasing, stripping a trailing ``.js``, and removing whitespace,
hyphens, periods, and slashes; an exact normalised string match counts
as a hit. False positives (over-matching) damage HR trust more than
false negatives (under-matching), so the matcher is intentionally
strict — e.g. "Postgres" in a CV will NOT count as a match for a JD
that asks for "PostgreSQL".
"""

from __future__ import annotations

from typing import Any, Final

from src.enums.thresholds import SIMILARITY_REJECTION_THRESHOLD

# Similarity (not distance) at-or-above which auto_reject leaves the
# application active, on the 0-100 percentage scale. Derived once from the
# canonical distance threshold so the two stay in lockstep — auto_reject
# compares ``100 - similarity`` to ``SIMILARITY_REJECTION_THRESHOLD`` (60 by
# default), which is the same as comparing similarity to ``100 - 60 = 40``.
SIMILARITY_PASS_THRESHOLD: Final[float] = round(100.0 - SIMILARITY_REJECTION_THRESHOLD, 4)


def _normalize(skill: str) -> str:
    s = skill.lower().strip()
    if s.endswith(".js"):
        s = s[:-3]
    return s.replace(" ", "").replace("-", "").replace(".", "").replace("/", "")


def _partition(jd_skills: list[str], cv_skill_set: set[str]) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for jd_skill in jd_skills:
        if _normalize(jd_skill) in cv_skill_set:
            matched.append(jd_skill)
        else:
            missing.append(jd_skill)
    return matched, missing


def compute_similarity_breakdown(
    *,
    similarity_score: float,
    required_skills: list[str],
    preferred_skills: list[str],
    cv_skills: list[str],
) -> dict[str, Any]:
    """Return the structured breakdown dict for one similarity score."""
    cv_skill_set = {_normalize(s) for s in cv_skills if isinstance(s, str)}
    matched_req, missing_req = _partition(required_skills, cv_skill_set)
    matched_pref, missing_pref = _partition(preferred_skills, cv_skill_set)
    distance = round(100.0 - similarity_score, 4)
    margin = round(similarity_score - SIMILARITY_PASS_THRESHOLD, 4)
    return {
        "threshold_similarity": SIMILARITY_PASS_THRESHOLD,
        "distance": distance,
        "passes_threshold": similarity_score >= SIMILARITY_PASS_THRESHOLD,
        "margin": margin,
        "required_skills_total": len(required_skills),
        "required_skills_matched": len(matched_req),
        "matched_required_skills": matched_req,
        "missing_required_skills": missing_req,
        "preferred_skills_total": len(preferred_skills),
        "preferred_skills_matched": len(matched_pref),
        "matched_preferred_skills": matched_pref,
        "missing_preferred_skills": missing_pref,
    }


def derive_rejection_reason(
    *,
    similarity_breakdown: dict[str, Any] | None,
    status_is_rejected: bool,
) -> str | None:
    """Build a one-line rejection-reason string or ``None``.

    Returns ``None`` when the application is not rejected, or when the
    breakdown is empty / missing the keys we expect (legacy rows written
    before this step had ``breakdown={}``). Defensive against malformed
    breakdowns so a stale score row can't crash the detail endpoint.
    """
    if not status_is_rejected:
        return None
    if not similarity_breakdown:
        return None
    distance = similarity_breakdown.get("distance")
    threshold = similarity_breakdown.get("threshold_similarity")
    matched = similarity_breakdown.get("required_skills_matched")
    total = similarity_breakdown.get("required_skills_total")
    if distance is None or threshold is None:
        return None
    similarity = round(100.0 - float(distance), 4)
    parts = [f"similarity {similarity:.1f} below {float(threshold):.0f} threshold"]
    if isinstance(matched, int) and isinstance(total, int) and total > 0:
        parts.append(f"{matched}/{total} required skills matched")
    return "; ".join(parts)
