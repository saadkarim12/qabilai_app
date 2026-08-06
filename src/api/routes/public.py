"""Public (anonymous) CV upload route (Step 3.8).

Single endpoint: ``POST /public/apply/{slug}/upload`` — accepts a
multipart form with the candidate's CV plus the minimum identity
fields (email, phone, full_name) and the consent flag.

No HR authentication is required; the slug is the only credential. The
route is intentionally thin — all business logic (slug validation,
candidate dedup, CV upload, application creation, Celery enqueue) lives
in :mod:`src.services.application_intake_service`. The route's job is
to extract form fields, hand them to the service, and translate the
returned :class:`PublicApplyResponse` into an HTTP 202.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, File, Form, Path, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_db
from src.schemas.job import PublicJobResponse
from src.schemas.public_apply import PublicApplyResponse
from src.services import job_service
from src.services.application_intake_service import intake_application

router = APIRouter(prefix="/public/apply", tags=["public-apply"])

# --- Form field bounds ------------------------------------------------------
# Enforced at the route layer via FastAPI's ``Form(min_length=...)`` so
# malformed input is rejected before it reaches the service. The service
# does its own normalization + final validation for the cases the route
# can't catch (e.g. email format).

SLUG_MIN_LENGTH: Final[int] = 16  # mirrors src/utils/slug.py
SLUG_MAX_LENGTH: Final[int] = 64

EMAIL_MIN_LENGTH: Final[int] = 5
EMAIL_MAX_LENGTH: Final[int] = 254

PHONE_MIN_LENGTH: Final[int] = 4
PHONE_MAX_LENGTH: Final[int] = 32  # generous: raw input before E.164 normalization

FULL_NAME_MIN_LENGTH: Final[int] = 1
FULL_NAME_MAX_LENGTH: Final[int] = 255

# Honeypot — hidden field bots tend to fill. We allow it to be missing,
# empty, or up to a generous cap; the service treats any non-empty
# value as bot activity.
HONEYPOT_MAX_LENGTH: Final[int] = 512


@router.get("/{slug}", response_model=PublicJobResponse)
async def get_public_job(
    db: Annotated[AsyncSession, Depends(get_db)],
    slug: Annotated[str, Path(min_length=SLUG_MIN_LENGTH, max_length=SLUG_MAX_LENGTH)],
) -> PublicJobResponse:
    """Return the candidate-facing job view for a public apply link.

    No HR auth — the slug is the only credential, mirroring the upload
    route below. Unknown or inactive slugs return 410 Gone (see
    :class:`~src.exceptions.SlugNotFoundError`). The payload is a trimmed
    projection (no HR/internal fields) so the apply page can render the
    role before the candidate uploads a CV.
    """
    return await job_service.get_public_job_by_slug(db, slug)


@router.post(
    "/{slug}/upload",
    response_model=PublicApplyResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def public_apply_upload(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    slug: Annotated[str, Path(min_length=SLUG_MIN_LENGTH, max_length=SLUG_MAX_LENGTH)],
    pdf: Annotated[UploadFile, File()],
    email: Annotated[str, Form(min_length=EMAIL_MIN_LENGTH, max_length=EMAIL_MAX_LENGTH)],
    phone: Annotated[str, Form(min_length=PHONE_MIN_LENGTH, max_length=PHONE_MAX_LENGTH)],
    full_name: Annotated[
        str, Form(min_length=FULL_NAME_MIN_LENGTH, max_length=FULL_NAME_MAX_LENGTH)
    ],
    consent: Annotated[bool, Form()],
    honeypot: Annotated[str | None, Form(max_length=HONEYPOT_MAX_LENGTH)] = None,
) -> PublicApplyResponse:
    """Accept a CV upload from an anonymous candidate.

    Returns 202 with a reference number on success. See the doc's
    Step 3.8 acceptance criteria — same response for new vs. duplicate
    submissions so a third party can't enumerate state.
    """
    pdf_bytes = await pdf.read()
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")

    response = await intake_application(
        db,
        slug=slug,
        pdf_bytes=pdf_bytes,
        email=email,
        phone=phone,
        full_name=full_name,
        consent=consent,
        ip=client_ip,
        user_agent=user_agent,
        honeypot=honeypot,
    )
    await db.commit()
    return response
