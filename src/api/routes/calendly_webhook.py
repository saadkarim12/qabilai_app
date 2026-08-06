"""Calendly webhook (Step 6.4).

One anonymous endpoint, ``POST /webhooks/calendly`` (no HR auth — Calendly is
the caller; authenticity comes from the HMAC signature on every event). We
verify ``Calendly-Webhook-Signature`` against ``CALENDLY_WEBHOOK_SIGNING_KEY``,
decode the ``invitee.created`` / ``invitee.canceled`` event, and **enqueue one
handler task** so the route stays thin (verify → parse → dispatch → 200) and
Calendly gets a fast ack while all DB work happens in the worker.

The whole endpoint 404s when ``interview_scheduling_enabled`` is False, so a
deployment without Calendly configured exposes no live webhook surface.

Webhook bodies carry the candidate's name/email; nothing here logs them — only
the event kind and resource URIs appear (URIs are non-PII Calendly ids).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from src.config import settings
from src.integrations.calendly_client import (
    parse_webhook_event,
    verify_webhook_signature,
)
from src.workers.tasks.interviews import handle_calendly_event_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["calendly-webhook"])


@router.post("/calendly")
async def receive_calendly_webhook(
    request: Request,
    calendly_webhook_signature: Annotated[str | None, Header()] = None,
) -> Response:
    """Receive an inbound Calendly ``invitee.*`` event.

    Signature is checked over the *raw* body (re-serializing would break the
    HMAC). On success we always return 200 — even for an event kind we ignore —
    so Calendly doesn't queue redeliveries for events we intentionally skip.
    Returns 404 when interview scheduling is disabled.
    """
    if not settings.interview_scheduling_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    raw_body = await request.body()
    if not _signature_ok(raw_body, calendly_webhook_signature):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        payload = json.loads(raw_body)
        event = parse_webhook_event(payload)
    except Exception:
        # A verified-but-undecodable body is nothing we can act on; ack so
        # Calendly stops retrying. No body content is logged.
        logger.exception("calendly.webhook.parse_failed")
        return Response(status_code=status.HTTP_200_OK)

    if event is None:
        # An event kind we don't subscribe to / can't decode — ack and move on.
        return Response(status_code=status.HTTP_200_OK)

    logger.info(
        "calendly.webhook.inbound",
        extra={
            "kind": event.kind,
            "event_uri": event.event_uri,
            "has_token": event.tracking_token is not None,
        },
    )
    # Hand the parsed event to the worker as JSON-safe primitives (datetimes as
    # ISO strings); all booking state writes happen there so this route is fast.
    handle_calendly_event_task.apply_async(
        kwargs={
            "kind": event.kind,
            "tracking_token": event.tracking_token,
            "invitee_uri": event.invitee_uri,
            "event_uri": event.event_uri,
            "start_time": event.start_time.isoformat() if event.start_time else None,
            "end_time": event.end_time.isoformat() if event.end_time else None,
            "invitee_email": event.invitee_email,
            "invitee_timezone": event.invitee_timezone,
            "location_type": event.location_type,
            "join_url": event.join_url,
            "location_text": event.location_text,
            "rescheduled": event.rescheduled,
            "cancel_reason": event.cancel_reason,
            "canceled_by": event.canceled_by,
        }
    )
    return Response(status_code=status.HTTP_200_OK)


def _signature_ok(raw_body: bytes, signature_header: str | None) -> bool:
    """Apply the signature policy for an inbound webhook.

    With ``CALENDLY_WEBHOOK_SIGNING_KEY`` set, the HMAC must verify. Without it,
    we reject in production (fail closed) but allow with a loud warning in
    development so the webhook can be exercised before the key is wired.
    """
    key = settings.calendly_webhook_signing_key
    if not key:
        if settings.is_production:
            logger.error("calendly.webhook.signature.unconfigured")
            return False
        logger.warning("calendly.webhook.signature.skipped_no_key")
        return True

    if not verify_webhook_signature(raw_body, signature_header, key):
        logger.warning("calendly.webhook.signature.invalid")
        return False
    return True
