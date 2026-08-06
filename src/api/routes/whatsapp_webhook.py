"""Meta WhatsApp Cloud API webhook (Step 5.2).

Two anonymous endpoints under ``/webhooks/whatsapp`` (no HR auth — Meta is
the caller; authenticity comes from the verify token on subscribe and the
HMAC signature on every event):

- ``GET`` — the one-time subscription handshake. Meta calls with
  ``hub.mode=subscribe``, ``hub.verify_token`` and ``hub.challenge``; we echo
  the challenge back verbatim iff the token matches ``META_WA_VERIFY_TOKEN``.
- ``POST`` — inbound events (candidate messages, button taps, delivery
  receipts). We verify ``X-Hub-Signature-256`` against ``META_WA_APP_SECRET``,
  then decode the messages and **enqueue one screening-router task per
  message** (Step 5.3). The route stays thin — verify, parse, dispatch, 200 —
  so Meta gets a fast ack and all DB / Meta-send work happens off the request
  path in the Celery worker.

All inbound content (message text, button payloads) is candidate PII and is
never logged — only ``from``, message id and type appear in log records. The
payload primitives we hand the task are the parsed metadata + content the
worker needs to advance the conversation.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from src.config import settings
from src.integrations.whatsapp_client import (
    parse_inbound_messages,
    verify_webhook_signature,
)
from src.workers.tasks.whatsapp import handle_whatsapp_inbound_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["whatsapp-webhook"])

# Meta sends this exact value as ``hub.mode`` on the subscription handshake.
HUB_MODE_SUBSCRIBE = "subscribe"


@router.get("/whatsapp")
async def verify_whatsapp_webhook(
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    """Answer Meta's subscription verification GET.

    Returns the raw challenge with 200 on a token match (Meta requires the
    body to be exactly the challenge), 403 on mismatch, and 503 if no verify
    token is configured (so a misconfiguration is loud, not a silent 403).
    """
    expected = settings.meta_wa_verify_token
    if not expected:
        logger.error("whatsapp.webhook.verify.unconfigured")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    if mode == HUB_MODE_SUBSCRIBE and challenge is not None and token == expected:
        logger.info("whatsapp.webhook.verify.ok")
        return PlainTextResponse(challenge)

    logger.warning("whatsapp.webhook.verify.rejected", extra={"mode": mode})
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/whatsapp")
async def receive_whatsapp_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> Response:
    """Receive an inbound WhatsApp event.

    Signature is checked over the *raw* body (re-serializing would break the
    HMAC). On success we always return 200 — even for an unrecognized or
    status-only payload — so Meta doesn't queue redeliveries for events we
    intentionally ignore.
    """
    raw_body = await request.body()

    if not _signature_ok(raw_body, x_hub_signature_256):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        payload = json.loads(raw_body)
        messages = parse_inbound_messages(payload)
    except Exception:
        # A verified-but-undecodable body is nothing we can act on; ack so
        # Meta stops retrying. No body content is logged.
        logger.exception("whatsapp.webhook.parse_failed")
        return Response(status_code=status.HTTP_200_OK)

    for message in messages:
        # Metadata only, never message content.
        logger.info(
            "whatsapp.webhook.inbound",
            extra={
                "from": message.from_number,
                "wa_message_id": message.message_id,
                "message_type": message.type,
                "is_button_reply": message.button_reply_id is not None,
            },
        )
        # Hand the parsed message to the worker; all conversation state, DB
        # writes, and reply sends happen there so this route returns fast.
        handle_whatsapp_inbound_task.apply_async(
            kwargs={
                "from_number": message.from_number,
                "message_id": message.message_id,
                "message_type": message.type,
                "text": message.text,
                "button_reply_id": message.button_reply_id,
                "button_reply_title": message.button_reply_title,
            }
        )

    return Response(status_code=status.HTTP_200_OK)


def _signature_ok(raw_body: bytes, signature_header: str | None) -> bool:
    """Apply the signature policy for an inbound POST.

    With ``META_WA_APP_SECRET`` set, the HMAC must verify. Without it, we
    reject in production (fail closed) but allow with a loud warning in
    development so the webhook can be exercised before the secret is wired.
    """
    secret = settings.meta_wa_app_secret
    if not secret:
        if settings.is_production:
            logger.error("whatsapp.webhook.signature.unconfigured")
            return False
        logger.warning("whatsapp.webhook.signature.skipped_no_secret")
        return True

    if not verify_webhook_signature(raw_body, signature_header, secret):
        logger.warning("whatsapp.webhook.signature.invalid")
        return False
    return True
