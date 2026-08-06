"""Meta WhatsApp Cloud API client (Step 5.1).

A thin async wrapper over the Graph API ``/{phone_number_id}/messages``
endpoint. This module is **transport only** — it knows how to send a
message and surface Meta's errors; it holds no screening-flow logic
(which conversation step we're on, what to send next) so the worker and
service layers stay testable against a single monkeypatchable seam.

Two public coroutines:

- :func:`send_text` — send a plain text message; returns the ``wamid.*``
  message id Meta assigns.
- :func:`send_interactive_buttons` — send a body with up to three quick
  *reply buttons* (used for the Yes/No screening prompt). Reply buttons are
  more robust than parsing free-text "yes"/"no" across languages and typos.

Configuration (all from :class:`~src.config.Settings`):
``META_WA_PHONE_NUMBER_ID`` identifies the sending number; the bearer
``META_WA_ACCESS_TOKEN`` authorizes the call. Missing either surfaces as
:class:`WhatsAppConfigError` before any network round-trip.

Message bodies are candidate-facing and may reference confidential JD
detail, so they're **never logged** — only the recipient (already PII we
hold), the message type, and Meta's returned message id appear in logs.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


# --- Constants --------------------------------------------------------------

# Graph API version pinned so a Meta-side default bump can't silently change
# request/response semantics. Bump deliberately when validating a new version.
GRAPH_API_VERSION: Final[str] = "v21.0"
GRAPH_API_BASE_URL: Final[str] = "https://graph.facebook.com"

# Meta's fixed product discriminator on every Cloud API message payload.
MESSAGING_PRODUCT: Final[str] = "whatsapp"

# Network timeout for a single send. Meta usually answers in well under a
# second; a generous ceiling avoids spurious failures on a slow hop while
# still bounding a hung Celery task.
SEND_TIMEOUT_SECONDS: Final[float] = 15.0

# Cloud API caps a reply-button group at three buttons; titles at 20 chars,
# ids at 256. We validate before the call so the failure is local and clear
# rather than an opaque Graph 400.
MAX_REPLY_BUTTONS: Final[int] = 3
MAX_BUTTON_TITLE_LEN: Final[int] = 20
MAX_BUTTON_ID_LEN: Final[int] = 256

# --- Inbound webhook ---
# Meta signs every webhook POST with HMAC-SHA256 of the raw body, presented
# as ``X-Hub-Signature-256: sha256=<hexdigest>``.
SIGNATURE_HEADER_PREFIX: Final[str] = "sha256="

# Inbound message ``type`` discriminators we branch on. Meta's vocabulary is
# larger (image, audio, location, …); we only decode the kinds the screening
# flow understands and ignore the rest.
INBOUND_TYPE_TEXT: Final[str] = "text"
INBOUND_TYPE_INTERACTIVE: Final[str] = "interactive"  # tap on send_interactive_buttons
INBOUND_TYPE_BUTTON: Final[str] = "button"  # tap on a template quick-reply button


# --- Exceptions -------------------------------------------------------------


class WhatsAppConfigError(RuntimeError):
    """Raised when required Meta WhatsApp settings are missing."""


class WhatsAppSendError(RuntimeError):
    """Raised when Meta rejects a send (non-2xx) or returns no message id.

    ``status_code`` and ``meta_code`` carry Meta's HTTP status and the
    application-level error code from the ``error`` envelope (when present),
    so the caller can decide transient-vs-permanent for Celery retry.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        meta_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.meta_code = meta_code


# --- Value types ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReplyButton:
    """One quick-reply button.

    ``id`` is echoed back verbatim in the inbound webhook when the candidate
    taps the button — we route on it, so make it stable and meaningful
    (e.g. ``"interest_yes"``). ``title`` is what the candidate sees.
    """

    id: str
    title: str


# --- Helpers ----------------------------------------------------------------


def _credentials() -> tuple[str, str]:
    """Return ``(phone_number_id, access_token)`` or raise.

    Validated lazily here (not at import) so the app and worker boot without
    WhatsApp configured — only an actual send requires the keys.
    """
    phone_number_id = settings.meta_wa_phone_number_id
    access_token = settings.meta_wa_access_token
    if not phone_number_id:
        raise WhatsAppConfigError(
            "META_WA_PHONE_NUMBER_ID is not configured; cannot send WhatsApp messages"
        )
    if not access_token:
        raise WhatsAppConfigError(
            "META_WA_ACCESS_TOKEN is not configured; cannot send WhatsApp messages"
        )
    return phone_number_id, access_token


def _normalize_recipient(to: str) -> str:
    """Strip a leading ``+`` from an E.164 number.

    We store numbers as ``+9665…`` (``candidate.phone_e164``); the Cloud API
    accepts the country-code-prefixed number without the plus, which is the
    form Meta's docs use. Whitespace is trimmed defensively.
    """
    return to.strip().removeprefix("+")


def _messages_url(phone_number_id: str) -> str:
    return f"{GRAPH_API_BASE_URL}/{GRAPH_API_VERSION}/{phone_number_id}/messages"


def _extract_message_id(response_body: dict[str, Any]) -> str:
    """Pull the ``wamid.*`` id out of a successful send response.

    Shape: ``{"messages": [{"id": "wamid.XXX"}], ...}``. A 2xx without an id
    is anomalous enough to treat as a failure rather than return ``""``.
    """
    messages = response_body.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            message_id = first.get("id")
            if isinstance(message_id, str) and message_id:
                return message_id
    raise WhatsAppSendError("Meta returned 2xx but no message id in response")


def _raise_for_meta_error(response: httpx.Response) -> None:
    """Translate a non-2xx Graph response into :class:`WhatsAppSendError`.

    The error body is ``{"error": {"message", "type", "code", ...}}``. We
    surface Meta's ``code`` (not its human ``message``, which can echo
    payload detail) so logs stay free of message content.
    """
    if response.is_success:
        return
    meta_code: int | None = None
    try:
        error = response.json().get("error", {})
        raw_code = error.get("code")
        meta_code = raw_code if isinstance(raw_code, int) else None
    except Exception:  # malformed/empty error body; status alone is enough
        meta_code = None
    raise WhatsAppSendError(
        f"Meta WhatsApp send failed (HTTP {response.status_code}, code={meta_code})",
        status_code=response.status_code,
        meta_code=meta_code,
    )


async def _post_message(payload: dict[str, Any], *, message_type: str) -> str:
    """POST a fully-formed message payload and return the assigned id.

    A fresh :class:`httpx.AsyncClient` is created per call and closed on exit.
    We deliberately do **not** cache the client: Celery bridges sync→async
    with ``asyncio.run`` (a new event loop per task), and an httpx connection
    pool bound to a closed loop raises "Event loop is closed" — the same
    constraint documented in ``ai/claude.py`` and ``integrations/azure_blob``.
    """
    phone_number_id, access_token = _credentials()
    url = _messages_url(phone_number_id)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            # Connection/timeout errors are transient — let the caller retry.
            raise WhatsAppSendError(f"WhatsApp send transport error: {exc}") from exc

    _raise_for_meta_error(response)
    message_id = _extract_message_id(response.json())

    logger.info(
        "whatsapp.send",
        extra={
            "message_type": message_type,
            "wa_message_id": message_id,
            "to": payload.get("to"),
        },
    )
    return message_id


# --- Public API -------------------------------------------------------------


async def send_text(to: str, body: str) -> str:
    """Send a plain text message to ``to`` (E.164) and return its ``wamid``.

    Note: outside an active 24h customer-service window, free-form text to a
    user who hasn't messaged first is rejected by Meta and a pre-approved
    template must be used instead. The initial screening prompt is sent via
    :func:`send_interactive_buttons`; this helper serves follow-ups within
    the open window.
    """
    payload: dict[str, Any] = {
        "messaging_product": MESSAGING_PRODUCT,
        "recipient_type": "individual",
        "to": _normalize_recipient(to),
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    return await _post_message(payload, message_type="text")


async def send_interactive_buttons(to: str, body: str, buttons: list[ReplyButton]) -> str:
    """Send a body with quick-reply buttons; return the ``wamid``.

    ``buttons`` must be 1-3 entries. The tapped button's ``id`` comes back in
    the inbound webhook, which is how the screening flow routes Yes/No without
    parsing free text.
    """
    if not 1 <= len(buttons) <= MAX_REPLY_BUTTONS:
        raise ValueError(f"buttons must be 1..{MAX_REPLY_BUTTONS}, got {len(buttons)}")
    for button in buttons:
        if not button.title or len(button.title) > MAX_BUTTON_TITLE_LEN:
            raise ValueError(
                f"button title must be 1..{MAX_BUTTON_TITLE_LEN} chars: {button.title!r}"
            )
        if not button.id or len(button.id) > MAX_BUTTON_ID_LEN:
            raise ValueError(f"button id must be 1..{MAX_BUTTON_ID_LEN} chars: {button.id!r}")

    payload: dict[str, Any] = {
        "messaging_product": MESSAGING_PRODUCT,
        "recipient_type": "individual",
        "to": _normalize_recipient(to),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b.id, "title": b.title}} for b in buttons
                ]
            },
        },
    }
    return await _post_message(payload, message_type="interactive_buttons")


async def send_template(
    to: str,
    *,
    template_name: str,
    language: str,
    body_params: list[str] | None = None,
    quick_reply_payloads: list[str] | None = None,
) -> str:
    """Send a pre-approved **template** message; return the ``wamid``.

    This is the only way to reach a candidate **outside** an open 24h
    customer-service window — i.e. the *first* screening contact, before the
    candidate has ever replied. ``template_name`` / ``language`` must match a
    template already approved in WhatsApp Manager.

    ``body_params`` fill the template body's positional placeholders
    (``{{1}}``, ``{{2}}``, …) in order. ``quick_reply_payloads`` attach a
    routing payload to each quick-reply button by index — the tapped button's
    payload returns in the inbound webhook as a ``button`` message, which the
    screening flow routes on exactly like an interactive button tap.
    """
    components: list[dict[str, Any]] = []
    if body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            }
        )
    for index, payload_value in enumerate(quick_reply_payloads or []):
        components.append(
            {
                "type": "button",
                "sub_type": "quick_reply",
                "index": str(index),
                "parameters": [{"type": "payload", "payload": payload_value}],
            }
        )

    payload: dict[str, Any] = {
        "messaging_product": MESSAGING_PRODUCT,
        "recipient_type": "individual",
        "to": _normalize_recipient(to),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": components,
        },
    }
    return await _post_message(payload, message_type="template")


# --- Inbound webhook: signature + parsing -----------------------------------


def verify_webhook_signature(
    raw_body: bytes, signature_header: str | None, app_secret: str
) -> bool:
    """Return whether ``signature_header`` authenticates ``raw_body``.

    Meta computes ``HMAC-SHA256(app_secret, raw_request_body)`` and sends it
    as ``X-Hub-Signature-256: sha256=<hexdigest>``. The HMAC must be taken
    over the **exact bytes received** — re-serializing parsed JSON would
    change whitespace/key-order and break the check, so the caller passes the
    raw body. Comparison is constant-time.
    """
    if not signature_header or not signature_header.startswith(SIGNATURE_HEADER_PREFIX):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len(SIGNATURE_HEADER_PREFIX) :]
    return hmac.compare_digest(expected, provided)


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A single decoded inbound WhatsApp message.

    ``text`` carries the candidate's message body and ``button_reply_*`` the
    payload of a tapped quick-reply button — both candidate content, so the
    webhook route logs only the metadata (``from_number``, ``message_id``,
    ``type``), never these fields.
    """

    from_number: str
    message_id: str
    type: str
    text: str | None = None
    button_reply_id: str | None = None
    button_reply_title: str | None = None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_one_message(msg: dict[str, Any]) -> InboundMessage | None:
    """Decode one entry from a ``value.messages[]`` array, or ``None``.

    Returns ``None`` for messages missing the identity fields or of a type we
    don't decode — the caller skips them rather than failing the batch.
    """
    from_number = msg.get("from")
    message_id = msg.get("id")
    msg_type = msg.get("type")
    if not (
        isinstance(from_number, str) and isinstance(message_id, str) and isinstance(msg_type, str)
    ):
        return None

    text: str | None = None
    button_reply_id: str | None = None
    button_reply_title: str | None = None

    if msg_type == INBOUND_TYPE_TEXT:
        body = _as_dict(msg.get("text")).get("body")
        text = body if isinstance(body, str) else None
    elif msg_type == INBOUND_TYPE_INTERACTIVE:
        reply = _as_dict(_as_dict(msg.get("interactive")).get("button_reply"))
        rid, rtitle = reply.get("id"), reply.get("title")
        button_reply_id = rid if isinstance(rid, str) else None
        button_reply_title = rtitle if isinstance(rtitle, str) else None
    elif msg_type == INBOUND_TYPE_BUTTON:
        button = _as_dict(msg.get("button"))
        payload, label = button.get("payload"), button.get("text")
        button_reply_id = payload if isinstance(payload, str) else None
        button_reply_title = label if isinstance(label, str) else None

    return InboundMessage(
        from_number=from_number,
        message_id=message_id,
        type=msg_type,
        text=text,
        button_reply_id=button_reply_id,
        button_reply_title=button_reply_title,
    )


def parse_inbound_messages(payload: dict[str, Any]) -> list[InboundMessage]:
    """Flatten a webhook POST body into the inbound messages it carries.

    Meta nests messages as ``entry[].changes[].value.messages[]`` and also
    delivers delivery/read **status** receipts in the same envelope (under
    ``value.statuses[]``) — those carry no ``messages`` array and so produce
    nothing here. Returns ``[]`` for a status-only or malformed payload.
    """
    messages: list[InboundMessage] = []
    for entry in _as_list(payload.get("entry")):
        for change in _as_list(_as_dict(entry).get("changes")):
            value = _as_dict(_as_dict(change).get("value"))
            for raw in _as_list(value.get("messages")):
                parsed = _parse_one_message(_as_dict(raw))
                if parsed is not None:
                    messages.append(parsed)
    return messages
