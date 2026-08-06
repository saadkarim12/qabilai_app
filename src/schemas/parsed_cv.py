"""Pydantic schemas for AI-parsed CV records.

Two shapes:

* :class:`ParsedCVDraft` is what Claude returns from the parse-CV prompt
  — *content only*. ``extra='forbid'`` so stray keys can't sneak through.
* :class:`ParsedCV` extends the draft with the service-assigned
  :class:`~src.enums.parsed_cv.ParseStatus` flag. The parser sets it to
  ``OK`` on a clean validation and ``NEEDS_REVIEW`` when it had to salvage
  fields after the strict retry also failed.

Field caps below are sanity limits (oversize bullets get truncated by
Pydantic), not UX rules. The downstream embedding + matching code reads
this model directly, so widening any cap means re-checking embedding token
budgets.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from src.enums.parsed_cv import ParseStatus

# --- Field caps -------------------------------------------------------------
# Why pull these to module-level Final constants? They appear in both
# schema definitions and the parser's system prompt (so Claude knows the
# limits). Single source of truth keeps the two in sync.

FULL_NAME_MAX_LENGTH: Final[int] = 200
# RFC 5321 caps a complete email address at 320 chars (64 local + @ + 255 domain).
EMAIL_MAX_LENGTH: Final[int] = 320
# E.164 numbers are at most 15 digits; +20 leaves room for the leading '+' and
# whatever stray formatting Claude might emit before sanitization drops it.
PHONE_MAX_LENGTH: Final[int] = 20
SKILL_MAX_LENGTH: Final[int] = 80
SKILLS_MAX_COUNT: Final[int] = 200
WORK_HISTORY_MAX_COUNT: Final[int] = 50
EDUCATION_MAX_COUNT: Final[int] = 30
LANGUAGES_MAX_COUNT: Final[int] = 20
LANGUAGE_NAME_MAX_LENGTH: Final[int] = 60
COMPANY_MAX_LENGTH: Final[int] = 200
TITLE_MAX_LENGTH: Final[int] = 200
INSTITUTION_MAX_LENGTH: Final[int] = 200
DEGREE_MAX_LENGTH: Final[int] = 200
FIELD_MAX_LENGTH: Final[int] = 200
# CV dates are kept as raw strings ("Jan 2020 - Present", "2018", "summer 2019")
# rather than being parsed to ``date`` so messy real-world entries survive.
DATE_STRING_MAX_LENGTH: Final[int] = 40
WORK_DESCRIPTION_MAX_LENGTH: Final[int] = 2000
# Plausible human-career upper bound. Used as a sanity cap on
# ``total_experience_years`` — anything outside [0, 80] is almost certainly
# Claude misparsing a year ("graduated 2015" → years_experience=2015).
TOTAL_EXPERIENCE_YEARS_MIN: Final[float] = 0.0
TOTAL_EXPERIENCE_YEARS_MAX: Final[float] = 80.0


class WorkEntry(BaseModel):
    """One row in a candidate's work history.

    ``end is None`` semantically means "current role" (i.e. "Present" on
    the CV). Both date fields are free-form strings — see
    :data:`DATE_STRING_MAX_LENGTH` for why.
    """

    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1, max_length=COMPANY_MAX_LENGTH)
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    start: str | None = Field(default=None, max_length=DATE_STRING_MAX_LENGTH)
    end: str | None = Field(default=None, max_length=DATE_STRING_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=WORK_DESCRIPTION_MAX_LENGTH)


class EducationEntry(BaseModel):
    """One row in a candidate's education history.

    Only ``institution`` is required — many CVs list a school without a
    degree (current student) or a degree without a major. Don't drop the
    row just because a non-essential field is missing.
    """

    model_config = ConfigDict(extra="forbid")

    institution: str = Field(min_length=1, max_length=INSTITUTION_MAX_LENGTH)
    degree: str | None = Field(default=None, max_length=DEGREE_MAX_LENGTH)
    field: str | None = Field(default=None, max_length=FIELD_MAX_LENGTH)
    start: str | None = Field(default=None, max_length=DATE_STRING_MAX_LENGTH)
    end: str | None = Field(default=None, max_length=DATE_STRING_MAX_LENGTH)


class ParsedCVDraft(BaseModel):
    """Content-only shape Claude returns from the parse-CV prompt.

    ``extra='forbid'`` ensures the parser never accidentally trusts a
    field the prompt doesn't request — Claude has a habit of inventing
    keys like ``confidence`` or ``summary`` that we don't want leaking
    into stored data.

    Every top-level field is optional (``None`` or empty list). The
    parser distinguishes "Claude couldn't find this" (None) from "Claude
    returned something invalid that we sanitized away" (also None, but
    logged at warning level).
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=FULL_NAME_MAX_LENGTH)
    email: str | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    phone: str | None = Field(default=None, max_length=PHONE_MAX_LENGTH)
    skills: list[str] = Field(
        default_factory=list,
        max_length=SKILLS_MAX_COUNT,
    )
    work_history: list[WorkEntry] = Field(
        default_factory=list,
        max_length=WORK_HISTORY_MAX_COUNT,
    )
    education: list[EducationEntry] = Field(
        default_factory=list,
        max_length=EDUCATION_MAX_COUNT,
    )
    total_experience_years: float | None = Field(
        default=None,
        ge=TOTAL_EXPERIENCE_YEARS_MIN,
        le=TOTAL_EXPERIENCE_YEARS_MAX,
    )
    languages: list[str] = Field(
        default_factory=list,
        max_length=LANGUAGES_MAX_COUNT,
    )


class ParsedCV(ParsedCVDraft):
    """Final, persistable parsed CV.

    Adds :attr:`parse_status` so downstream code (HR review queue,
    matching pipeline) can tell at a glance whether the parse needs a
    human to double-check it.
    """

    parse_status: ParseStatus = ParseStatus.OK
