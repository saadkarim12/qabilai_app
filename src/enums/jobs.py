"""Job-related enumerations.

Each enum stores its ``.value`` (lowercase identifier) in the database via
``values_callable`` on the SQLAlchemy ``Enum`` column — see ``src/db/models/job.py``.
The values are part of the API contract: changing a value requires a data
migration, not just an enum edit.
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    """Lifecycle state of a job posting.

    - ``DRAFT`` — created but not live yet.
    - ``OPEN`` — live and accepting applications (surfaced as "Active" in the UI).
    - ``INACTIVE`` — temporarily paused; not accepting applications, easily
      reactivated to ``OPEN``.
    - ``ARCHIVED`` — ended/closed-out; terminal but still reopenable.
    - ``CLOSED`` — legacy terminal state, predates ``ARCHIVED``. Retained so old
      rows keep resolving; new flows archive instead of close.

    The column is a plain ``VARCHAR`` (``native_enum=False``, no CHECK
    constraint), so adding values needs no data migration.
    """

    DRAFT = "draft"
    OPEN = "open"
    INACTIVE = "inactive"
    ARCHIVED = "archived"
    CLOSED = "closed"


class JobListOrder(StrEnum):
    """Ordering options for the ``GET /jobs`` list.

    - ``UPDATED_AT`` — most recently touched first (the default; what the Jobs
      list page shows).
    - ``TITLE`` — alphabetical by title (case-insensitive), the canonical order
      for job-selector dropdowns so every selector lists jobs the same way.

    Not persisted — a request-only query param, so no data migration on change.
    """

    UPDATED_AT = "updated_at"
    TITLE = "title"


class EmploymentType(StrEnum):
    """How the role is engaged."""

    PERMANENT = "permanent"
    CONTRACT = "contract"
    TEMPORARY = "temporary"


class WorkMode(StrEnum):
    """Where the role is performed."""

    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"


class NoticePeriod(StrEnum):
    """Maximum notice period the hiring side will tolerate.

    NULL on the column means "not specified"; ``ANY`` means the HM
    explicitly accepts any notice period.
    """

    ANY = "any"
    IMMEDIATE = "immediate"
    DAYS_30 = "30d"
    DAYS_60 = "60d"
    DAYS_90 = "90d"


class VisaRequirement(StrEnum):
    """Visa / work-authorization stance for the role.

    NULL on the column means "not specified"; ``ANY`` means the HM
    explicitly has no preference.
    """

    ANY = "any"
    CITIZEN_OR_RESIDENT = "citizen_or_resident"
    SPONSORSHIP_OFFERED = "sponsorship_offered"
