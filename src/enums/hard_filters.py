"""Hard-filter scoring keys, weights, and rubrics.

The hard-filter score (the "CV score" HR sees) is a weighted sum of five
sub-scores, each produced by Claude Opus against a rubric. Weights are
pinned by the architecture doc as percentages out of 100 (35/35/10/10/10);
the aggregator divides the weighted sum by the total weight so the result
stays in the 0-100 range. The rubrics below are the production scoring
rubrics — each carries the 90-100 / 70-89 / 50-69 / 0-49 band guidance the
model applies per signal.

All consumers MUST read from ``HARD_FILTER_WEIGHTS`` / ``HARD_FILTER_RUBRICS``
rather than hard-coding the values or the keys.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final


class HardFilterKey(StrEnum):
    """The five sub-scores that make up the hard-filter total."""

    SKILLS = "skills"
    EXPERIENCE = "experience"
    INDUSTRY = "industry"
    REGIONAL = "regional"
    ROLE_SENIORITY = "role_seniority"


# Percentages out of 100 (sum to 100). The aggregator normalises by the
# total weight, so these read directly as "this signal is 35% of the score".
HARD_FILTER_WEIGHTS: Final[Mapping[HardFilterKey, float]] = {
    HardFilterKey.SKILLS: 35.0,
    HardFilterKey.EXPERIENCE: 35.0,
    HardFilterKey.INDUSTRY: 10.0,
    HardFilterKey.REGIONAL: 10.0,
    HardFilterKey.ROLE_SENIORITY: 10.0,
}

HARD_FILTER_RUBRICS: Final[Mapping[HardFilterKey, str]] = {
    HardFilterKey.SKILLS: (
        "Match between the candidate's demonstrated skills and the job's "
        "required (must-have) and preferred skills. Skills must be EVIDENCED "
        "in work descriptions, not merely listed in a skills section. "
        "Relevant certifications count as supporting evidence, not as a "
        "substitute for demonstrated use.\n"
        "  90-100: All must-haves evidenced in real roles + most preferred "
        "present; relevant certs held.\n"
        "  70-89:  Most must-haves evidenced, minor gaps.\n"
        "  50-69:  Some must-haves only listed (not evidenced) or missing.\n"
        "  0-49:   Major skill gaps; key requirements absent."
    ),
    HardFilterKey.EXPERIENCE: (
        "Total years of RELEVANT experience versus the role's requirement. "
        "Relevance outweighs raw years — 5 relevant years beat 10 unrelated "
        "ones. Weigh role progression and depth, not just a date total.\n"
        "  90-100: Meets/exceeds required years, highly relevant.\n"
        "  70-89:  Meets required years, mostly relevant.\n"
        "  50-69:  Slightly below required years OR years met but only "
        "partially relevant.\n"
        "  0-49:   Significantly under-experienced or experience is irrelevant."
    ),
    HardFilterKey.INDUSTRY: (
        "Alignment between the candidate's industry background and the hiring "
        "company's sector (banking, healthcare, construction, tech, etc.). "
        "Adjacent / transferable industries score partially.\n"
        "  90-100: Direct industry match or a highly transferable adjacent "
        "sector.\n"
        "  70-89:  Related industry with transferable context.\n"
        "  50-69:  Loosely related industry.\n"
        "  0-49:   Unrelated industry; steep ramp-up expected."
    ),
    HardFilterKey.REGIONAL: (
        "Assess ONE thing: does the candidate have WORK EXPERIENCE (recent or "
        "previous) PHYSICALLY BASED in the target region the job specifies "
        "(e.g. UAE / KSA / wider GCC)? Read the work history ONLY — the roles, "
        "employers, and project locations that sit in that region. "
        "IGNORE and NEVER mention: current country of residence, home address, "
        "phone/country code, nationality, visa status, or ethnicity. These are "
        "NOT regional experience and must never appear in the score, evidence, "
        "or gap. Evidence must cite work history (role + employer + location + "
        "dates); the gap, if any, states only that no such regional work "
        "experience was found — nothing about where the candidate currently "
        "lives or their phone number. If the job does not require regional "
        "experience, treat this signal as satisfied.\n"
        "  90-100: Strong, recent work experience in the required region.\n"
        "  70-89:  Solid regional work experience, slightly dated or in an "
        "adjacent GCC market.\n"
        "  50-69:  Limited or older regional work experience.\n"
        "  0-49:   No work experience in the required region when the job "
        "requires it."
    ),
    HardFilterKey.ROLE_SENIORITY: (
        "Whether the candidate's most recent title and seniority level align "
        "with the role applied for. Catches over-reach (junior reaching for "
        "senior) and under-reach (senior applying to a junior role).\n"
        "  90-100: Recent role at the same level as, or one level below, the "
        "target.\n"
        "  70-89:  One-level gap with reasonable progression.\n"
        "  50-69:  Two-level gap (a stretch in either direction).\n"
        "  0-49:   Major mismatch (e.g. intern → lead role)."
    ),
}
