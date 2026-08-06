"""Pydantic request/response shapes for the Jobs API.

These are the over-the-wire contracts. The Job ORM model (``src/db/models/job.py``)
is the storage shape; these schemas are how clients see and submit jobs.

``funnel_counts`` (applied / screened / interview / hired / rejected) is
intentionally NOT on the detail response yet — the applications table
arrives in Phase 3, and a placeholder field now would lie. The detail
schema will gain it when that table exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.enums.jobs import (
    EmploymentType,
    JobStatus,
    NoticePeriod,
    VisaRequirement,
    WorkMode,
)
from src.schemas.whatsapp_question import WhatsAppQuestion
from src.utils.countries import is_valid_country

# Field-length caps mirror the DB constraints in `src/db/models/job.py`.
TITLE_MAX_LENGTH: Final[int] = 255
COMPANY_MAX_LENGTH: Final[int] = 255
CITY_MAX_LENGTH: Final[int] = 100
CURRENCY_LENGTH: Final[int] = 3
SKILL_MAX_LENGTH: Final[int] = 80
LANGUAGE_MAX_LENGTH: Final[int] = 40
NATIONALITY_MAX_LENGTH: Final[int] = 40
MAX_SKILLS: Final[int] = 50
MAX_LANGUAGES: Final[int] = 20
MAX_NATIONALITIES: Final[int] = 50
MIN_EXPERIENCE_YEARS_MAX: Final[int] = 50

# Description cap — generous but not unbounded. Embedding + WhatsApp prompt
# budgets are well within a few thousand chars.
JOB_DESCRIPTION_MAX_LENGTH: Final[int] = 20_000

# Status transitions PATCH /jobs/{id}/status is allowed to set. ``DRAFT`` is
# excluded — a job is never moved back to draft. ``CLOSED`` is retained for
# backward-compat though new flows use ``ARCHIVED``.
STATUS_PATCH_VALUES: Final[frozenset[JobStatus]] = frozenset(
    {JobStatus.OPEN, JobStatus.INACTIVE, JobStatus.ARCHIVED, JobStatus.CLOSED}
)

# Cap matches the WhatsAppQuestion `order` Field constraint (le=15). Headroom for
# up to 9 field-sourced questions + up to 3 AI background-validation questions.
MAX_WHATSAPP_QUESTIONS: Final[int] = 15


class JobSpecBase(BaseModel):
    """The Role-Basics fields shared by job creation and JD generation.

    Holds every field describing *the role itself* — title, location,
    compensation, experience, skills — plus their validators. ``JobCreateRequest``
    extends it with the screening + description fields needed to persist a job;
    ``JobDescriptionGenerateRequest`` consumes it as-is to draft a JD before any
    job row exists.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    hiring_company: str = Field(min_length=1, max_length=COMPANY_MAX_LENGTH)
    country: str = Field(min_length=2, max_length=2)
    city: str = Field(min_length=1, max_length=CITY_MAX_LENGTH)

    employment_type: EmploymentType
    work_mode: WorkMode

    currency: str = Field(min_length=CURRENCY_LENGTH, max_length=CURRENCY_LENGTH)
    min_salary: int | None = Field(default=None, ge=0)
    max_salary: int | None = Field(default=None, ge=0)

    notice_period: NoticePeriod | None = None
    min_experience_years: int = Field(ge=0, le=MIN_EXPERIENCE_YEARS_MAX)
    required_skills: list[str] = Field(default_factory=list, max_length=MAX_SKILLS)
    preferred_skills: list[str] = Field(default_factory=list, max_length=MAX_SKILLS)

    visa_requirement: VisaRequirement | None = None
    nationality_preference: list[str] = Field(default_factory=list, max_length=MAX_NATIONALITIES)
    languages_required: list[str] = Field(default_factory=list, max_length=MAX_LANGUAGES)

    @field_validator("country")
    @classmethod
    def _country_must_be_iso_alpha2(cls, v: str) -> str:
        upper = v.upper()
        if not is_valid_country(upper):
            raise ValueError(f"'{v}' is not a valid ISO-3166 alpha-2 country code")
        return upper

    @field_validator("currency")
    @classmethod
    def _currency_uppercase(cls, v: str) -> str:
        upper = v.upper()
        if not upper.isalpha():
            raise ValueError("currency must be three ISO-4217 letters")
        return upper

    @field_validator("required_skills", "preferred_skills")
    @classmethod
    def _validate_skills(cls, v: list[str]) -> list[str]:
        for skill in v:
            if not skill or len(skill) > SKILL_MAX_LENGTH:
                raise ValueError(f"each skill must be 1..{SKILL_MAX_LENGTH} chars; got {skill!r}")
        return v

    @field_validator("languages_required")
    @classmethod
    def _validate_languages(cls, v: list[str]) -> list[str]:
        for lang in v:
            if not lang or len(lang) > LANGUAGE_MAX_LENGTH:
                raise ValueError(
                    f"each language must be 1..{LANGUAGE_MAX_LENGTH} chars; got {lang!r}"
                )
        return v

    @field_validator("nationality_preference")
    @classmethod
    def _validate_nationalities(cls, v: list[str]) -> list[str]:
        for nat in v:
            if not nat or len(nat) > NATIONALITY_MAX_LENGTH:
                raise ValueError(
                    f"each nationality must be 1..{NATIONALITY_MAX_LENGTH} chars; got {nat!r}"
                )
        return v

    @model_validator(mode="after")
    def _salary_band_is_ordered(self) -> Self:
        if (
            self.min_salary is not None
            and self.max_salary is not None
            and self.min_salary > self.max_salary
        ):
            raise ValueError("min_salary must be <= max_salary")
        return self


class JobCreateRequest(JobSpecBase):
    """Body of ``POST /jobs``. Status defaults to draft on the server side.

    WhatsApp screening questions are no longer HR-selected at create time — the
    fixed set is sourced server-side on job open (see
    ``src.enums.fixed_screening_questions``), so there is no ``screening_fields``
    input. HR can add custom questions afterwards via
    ``PATCH /jobs/{id}/whatsapp-questions``.
    """

    job_description: str = Field(min_length=1, max_length=JOB_DESCRIPTION_MAX_LENGTH)
    # Optional native Gulf-Arabic rendering (from the AI JD Builder). Omitted
    # when HR wrote the description by hand.
    job_description_ar: str | None = Field(default=None, max_length=JOB_DESCRIPTION_MAX_LENGTH)


class JobDescriptionGenerateRequest(JobSpecBase):
    """Body of ``POST /jobs/generate-description``.

    The Role-Basics spec only — no ``job_description`` (that's what we're
    drafting). Reuses :class:`JobSpecBase` so the AI builder validates
    identically to job creation, and runs *before* any job row exists (the
    wizard calls it on the JD Builder step).
    """


class JobDescriptionGenerateResponse(BaseModel):
    """``POST /jobs/generate-description`` body — the drafted JD prose.

    Bilingual: ``job_description`` is the English draft, ``job_description_ar``
    the native Gulf-Arabic rendering the AI JD Builder produces alongside it.
    The wizard pre-fills both; HR can edit either before persisting on create.
    """

    job_description: str
    job_description_ar: str


class JdDraft(BaseModel):
    """Tool-forced structured output of the AI JD Builder (both languages).

    The ``description`` on each field is sent to Claude as the tool-input
    schema, so it doubles as the per-field spec — keep the content rules here
    in sync with ``JD_BUILDER_SYSTEM_PROMPT``. ``extra="forbid"`` rejects any
    stray key so a schema drift fails loud rather than silently dropping text.
    """

    model_config = ConfigDict(extra="forbid")

    job_description_en: str = Field(
        description=(
            "Full English job description, 350-500 words. Sections in order, "
            "each as a plain-text label line (no markdown '#'): Role Overview "
            "(2-3 sentences naming work mode, employment type, and city/country) "
            "-> Key Responsibilities (5-7 specific '- ' bullets with active "
            "verbs) -> What You Bring (required skills first, then preferred as "
            "'nice to have') -> What We Offer (work mode, languages, visa "
            "eligibility, and salary ONLY if provided). No generic HR filler."
        ),
    )
    job_description_ar: str = Field(
        description=(
            "Full job description written NATIVELY in formal Gulf-Arabic "
            "business register - NOT a translation of the English version. "
            "Same four sections: نظرة عامة عن الدور -> المسؤوليات الرئيسية -> "
            "المتطلبات -> ما نقدمه. Keep universally-English GCC technical terms "
            "(Cloud, KPIs, DevOps) untranslated; use Arabic-native phrasing "
            "everywhere else. Confident, professional, direct tone."
        ),
    )


class JobListItemResponse(BaseModel):
    """Lightweight row in ``GET /jobs`` list. Excludes JD text and embeddings.

    ``pipeline_status`` + ``ready_for_applications`` are included so the
    list view can show a per-row badge without a second round-trip.

    ``applications_by_stage`` is the per-job pipeline funnel: a count of this
    job's applications at each :class:`ApplicationStage` (``vector_screen`` …
    ``done``). It is always fully populated — every stage value is present,
    zero-filled — so the FE never guards a missing key (same contract as the
    dashboard's ``by_stage``). All applications are counted regardless of
    status; rejected/accepted rows preserve the stage they stopped at.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    hiring_company: str
    status: JobStatus
    country: str
    city: str
    employment_type: EmploymentType
    work_mode: WorkMode
    currency: str
    min_salary: int | None
    max_salary: int | None
    public_slug: str
    required_skills: list[str] = Field(default_factory=list, max_length=MAX_SKILLS)
    pipeline_status: dict[str, Any]
    ready_for_applications: bool
    applications_by_stage: dict[str, int] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class JobListResponse(BaseModel):
    """``GET /jobs`` envelope."""

    items: list[JobListItemResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


class JobDetailResponse(BaseModel):
    """``GET /jobs/{id}`` body. Full job minus ``jd_embedding`` (1536-d vector
    is not useful over the wire) and ``ai_verify_flags`` (reserved for later
    steps)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_by: uuid.UUID
    title: str
    hiring_company: str
    country: str
    city: str
    employment_type: EmploymentType
    work_mode: WorkMode
    currency: str
    min_salary: int | None
    max_salary: int | None
    notice_period: NoticePeriod | None
    min_experience_years: int
    required_skills: list[str]
    preferred_skills: list[str]
    visa_requirement: VisaRequirement | None
    nationality_preference: list[str]
    languages_required: list[str]
    job_description: str
    job_description_ar: str | None
    whatsapp_questions: list[WhatsAppQuestion]
    status: JobStatus
    public_slug: str
    pipeline_status: dict[str, Any]
    ready_for_applications: bool
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicJobResponse(BaseModel):
    """``GET /public/apply/{slug}`` body — the candidate-facing job view.

    A deliberately trimmed projection of :class:`JobDetailResponse`: it
    carries only what an anonymous applicant needs to decide whether to
    apply, and omits every HR/internal field (``id``, ``created_by``,
    ``whatsapp_questions``, ``pipeline_status``, ``ready_for_applications``,
    ``public_slug``, audit timestamps). ``status`` is kept so the frontend
    can render a "this role is closed" state on a stale link.
    """

    model_config = ConfigDict(from_attributes=True)

    title: str
    hiring_company: str
    country: str
    city: str
    employment_type: EmploymentType
    work_mode: WorkMode
    currency: str
    min_salary: int | None
    max_salary: int | None
    notice_period: NoticePeriod | None
    min_experience_years: int
    required_skills: list[str]
    preferred_skills: list[str]
    visa_requirement: VisaRequirement | None
    nationality_preference: list[str]
    languages_required: list[str]
    job_description: str
    status: JobStatus


class JobCreateResponse(BaseModel):
    """``POST /jobs`` 202 body."""

    id: uuid.UUID


class JobStatusUpdateRequest(BaseModel):
    """``PATCH /jobs/{id}/status`` body."""

    model_config = ConfigDict(extra="forbid")

    status: JobStatus

    @field_validator("status")
    @classmethod
    def _only_patchable_statuses(cls, v: JobStatus) -> JobStatus:
        if v not in STATUS_PATCH_VALUES:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(s.value for s in STATUS_PATCH_VALUES))}"
            )
        return v


class WhatsAppQuestionsResponse(BaseModel):
    """``GET /jobs/{id}/whatsapp-questions`` body."""

    questions: list[WhatsAppQuestion]


class WhatsAppQuestionsUpdateRequest(BaseModel):
    """``PATCH /jobs/{id}/whatsapp-questions`` body.

    HR submits the full ordered list; ``order`` values must be unique 1..N
    and ``id`` values must be unique across the list.
    """

    model_config = ConfigDict(extra="forbid")

    questions: list[WhatsAppQuestion] = Field(min_length=1, max_length=MAX_WHATSAPP_QUESTIONS)

    @model_validator(mode="after")
    def _ids_and_orders_unique(self) -> Self:
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate question ids in payload")
        orders = [q.order for q in self.questions]
        if len(set(orders)) != len(orders):
            raise ValueError("duplicate question orders in payload")
        return self
