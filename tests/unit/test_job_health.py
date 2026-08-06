"""Unit tests for the dashboard job-health verdict and its working-day clock.

These exercise the pure helpers directly (no DB), so the band boundaries and
the Mon-Fri working-day count are pinned deterministically — the integration
test only covers the coarse, weekday-independent cases.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.enums.dashboard import JobHealth
from src.enums.jobs import JobStatus
from src.enums.thresholds import JOB_AT_RISK_WORKING_DAYS, JOB_UNHEALTHY_WORKING_DAYS
from src.services.dashboard_service import _job_health, _working_days_between

# 2024-01-01 is a Monday; anchor everything off it so weekdays are explicit.
MON = datetime(2024, 1, 1, tzinfo=UTC)  # Monday
FRI = datetime(2024, 1, 5, tzinfo=UTC)  # Friday
SAT = datetime(2024, 1, 6, tzinfo=UTC)  # Saturday
NEXT_MON = datetime(2024, 1, 8, tzinfo=UTC)  # following Monday


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (MON, MON, 0),  # empty range
        (SAT, MON, 0),  # end before start
        (MON, SAT, 5),  # Mon..Fri (half-open) = 5 working days
        (MON, NEXT_MON, 5),  # a full 7-day week = 5 working days
        (FRI, NEXT_MON, 1),  # Fri, Sat, Sun -> only Friday counts
        (SAT, NEXT_MON, 0),  # Sat, Sun -> no working days
        (MON, datetime(2024, 1, 15, tzinfo=UTC), 10),  # two full weeks = 10
    ],
)
def test_working_days_between(start: datetime, end: datetime, expected: int) -> None:
    assert _working_days_between(start, end) == expected


@pytest.mark.parametrize(
    ("working_days", "expected"),
    [
        (0, JobHealth.HEALTHY),
        (JOB_AT_RISK_WORKING_DAYS - 1, JobHealth.HEALTHY),  # 17 -> healthy
        (JOB_AT_RISK_WORKING_DAYS, JobHealth.AT_RISK),  # 18 -> at risk
        (JOB_UNHEALTHY_WORKING_DAYS, JobHealth.AT_RISK),  # 20 -> still at risk
        (JOB_UNHEALTHY_WORKING_DAYS + 1, JobHealth.UNHEALTHY),  # 21 -> unhealthy
        (100, JobHealth.UNHEALTHY),
    ],
)
def test_open_job_health_bands(working_days: int, expected: JobHealth) -> None:
    assert _job_health(status=JobStatus.OPEN, working_days_open=working_days) == expected


@pytest.mark.parametrize(
    "status",
    [JobStatus.CLOSED, JobStatus.INACTIVE, JobStatus.ARCHIVED],
)
def test_non_open_jobs_are_always_healthy(status: JobStatus) -> None:
    # Even far past the unhealthy threshold, a job that isn't OPEN reads healthy.
    assert _job_health(status=status, working_days_open=100) == JobHealth.HEALTHY
