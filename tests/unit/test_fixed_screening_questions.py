"""Unit tests for the fixed screening questions + JobCreateRequest contract."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from src.db.models.job import Job
from src.enums.fixed_screening_questions import (
    FIXED_QUESTION_KEYS,
    FIXED_QUESTIONS,
    build_fixed_questions,
)
from src.enums.jobs import EmploymentType, JobStatus, WorkMode
from src.enums.whatsapp_questions import QuestionCategory
from src.schemas.job import JobCreateRequest


def _build_job(**overrides: Any) -> Job:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "created_by": uuid.uuid4(),
        "title": "Analyst",
        "hiring_company": "Kabil",
        "country": "AE",
        "city": "Dubai",
        "employment_type": EmploymentType.PERMANENT,
        "work_mode": WorkMode.ONSITE,
        "currency": "AED",
        "min_experience_years": 3,
        "languages_required": ["English", "Arabic"],
        "job_description": "desc",
        "status": JobStatus.DRAFT,
        "public_slug": "slug000000000000",
    }
    base.update(overrides)
    return Job(**base)


def _create_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Analyst",
        "hiring_company": "Kabil",
        "country": "AE",
        "city": "Dubai",
        "employment_type": "permanent",
        "work_mode": "onsite",
        "currency": "AED",
        "min_experience_years": 3,
        "job_description": "Build things.",
    }
    base.update(overrides)
    return base


# --- fixed question set -----------------------------------------------------


def test_fixed_question_keys_in_canonical_order() -> None:
    assert FIXED_QUESTION_KEYS == (
        "commitment",
        "salary",
        "notice_period",
        "visa",
        "employment_type",
        "work_mode",
    )


def test_build_fixed_questions_returns_full_set_in_order() -> None:
    pairs = build_fixed_questions(_build_job())
    assert [key for key, _ in pairs] == list(FIXED_QUESTION_KEYS)
    assert len(pairs) == len(FIXED_QUESTIONS)


def test_fixed_question_categories() -> None:
    by_key = {key: draft.category for key, draft in build_fixed_questions(_build_job())}
    assert by_key["commitment"] is QuestionCategory.COMMITMENT
    assert by_key["salary"] is QuestionCategory.SALARY
    assert by_key["notice_period"] is QuestionCategory.COMMITMENT
    assert by_key["visa"] is QuestionCategory.COMMITMENT
    assert by_key["employment_type"] is QuestionCategory.COMMITMENT
    assert by_key["work_mode"] is QuestionCategory.COMMITMENT


def test_fixed_questions_are_bilingual() -> None:
    for _key, draft in build_fixed_questions(_build_job()):
        assert draft.question_en.strip()
        assert draft.question_ar.strip()


def test_build_fixed_questions_fills_placeholders() -> None:
    job = _build_job(city="Riyadh", currency="SAR", work_mode=WorkMode.REMOTE)
    by_key = {key: draft for key, draft in build_fixed_questions(job)}

    salary = by_key["salary"]
    assert "SAR" in salary.question_en
    assert "SAR" in salary.question_ar

    visa = by_key["visa"]
    assert "Riyadh" in visa.question_en
    assert "Riyadh" in visa.question_ar

    work_mode = by_key["work_mode"]
    assert WorkMode.REMOTE.value in work_mode.question_en
    assert "Riyadh" in work_mode.question_en

    employment = by_key["employment_type"]
    assert EmploymentType.PERMANENT.value in employment.question_en

    # No unresolved placeholder tokens leak through.
    for _key, draft in build_fixed_questions(job):
        assert "{" not in draft.question_en
        assert "{" not in draft.question_ar


# --- JobCreateRequest contract ----------------------------------------------


def test_create_request_no_longer_accepts_screening_fields() -> None:
    # screening_fields was removed; extra='forbid' rejects it.
    with pytest.raises(ValidationError):
        JobCreateRequest.model_validate(_create_payload(screening_fields=["min_salary"]))


def test_create_request_valid_without_screening_fields() -> None:
    req = JobCreateRequest.model_validate(_create_payload())
    assert req.title == "Analyst"
    assert not hasattr(req, "screening_fields")
