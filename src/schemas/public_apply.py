"""Pydantic schemas for the public CV upload endpoint (Step 3.8).

The request itself is a multipart form, so its fields are declared
inline on the route as :class:`fastapi.Form` parameters — no Pydantic
model for the input. This module covers the response shape plus the
consent-audit payload that gets persisted onto
``applications.consent_context``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

# Public-facing identifier returned to the candidate. We emit the
# Application UUID as a hex string (32 chars, no dashes) — short enough
# to read aloud, long enough to be effectively unguessable, and a stable
# pointer to the row in the DB. See doc Step 3.8 step 11.
REFERENCE_NUMBER_LENGTH: Final[int] = 32

# Sanity caps on the consent metadata strings — both come from the HTTP
# request and need bounds before they hit JSONB.
CONSENT_IP_MAX_LENGTH: Final[int] = 45  # IPv6 textual max (8 groups, 4 hex each + 7 colons)
CONSENT_USER_AGENT_MAX_LENGTH: Final[int] = 512


class ConsentContext(BaseModel):
    """Audit snapshot captured at the moment of upload.

    Persisted verbatim into ``applications.consent_context`` (JSONB).
    Keeping it as a Pydantic model means the intake service can't
    accidentally write a typo'd key — and the schema doubles as
    documentation of what's recorded.
    """

    model_config = ConfigDict(extra="forbid")

    ip: str = Field(min_length=1, max_length=CONSENT_IP_MAX_LENGTH)
    user_agent: str = Field(min_length=1, max_length=CONSENT_USER_AGENT_MAX_LENGTH)
    accepted_at: datetime


class PublicApplyResponse(BaseModel):
    """202 body returned by ``POST /public/apply/{slug}/upload``.

    Per the doc's "No info leak" requirement, the same shape is
    returned whether the upload created a brand-new application, found
    a duplicate, or short-circuited the honeypot — the candidate sees
    only their reference number.
    """

    model_config = ConfigDict(extra="forbid")

    reference_number: str = Field(min_length=1, max_length=REFERENCE_NUMBER_LENGTH)
