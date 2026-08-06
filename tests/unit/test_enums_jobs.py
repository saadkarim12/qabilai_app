"""Unit tests for the job enums.

Locks the exact string values down because they are part of the API
contract (JSONB payloads, public URLs, downstream agents).
"""

from __future__ import annotations

import pytest

from src.enums.jobs import (
    EmploymentType,
    JobStatus,
    NoticePeriod,
    VisaRequirement,
    WorkMode,
)


def test_job_status_values() -> None:
    assert {e.value for e in JobStatus} == {"draft", "open", "closed"}


def test_employment_type_values() -> None:
    assert {e.value for e in EmploymentType} == {"permanent", "contract", "temporary"}


def test_work_mode_values() -> None:
    assert {e.value for e in WorkMode} == {"onsite", "hybrid", "remote"}


def test_notice_period_values() -> None:
    assert {e.value for e in NoticePeriod} == {"any", "immediate", "30d", "60d", "90d"}


def test_visa_requirement_values() -> None:
    assert {e.value for e in VisaRequirement} == {
        "any",
        "citizen_or_resident",
        "sponsorship_offered",
    }


@pytest.mark.parametrize(
    ("enum_cls", "value"),
    [
        (JobStatus, "draft"),
        (EmploymentType, "permanent"),
        (WorkMode, "remote"),
        (NoticePeriod, "30d"),
        (VisaRequirement, "sponsorship_offered"),
    ],
)
def test_enum_constructible_from_value(enum_cls: type, value: str) -> None:
    assert enum_cls(value).value == value


def test_unknown_value_rejected() -> None:
    with pytest.raises(ValueError):
        JobStatus("archived")
