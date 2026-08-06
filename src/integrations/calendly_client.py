"""Calendly API client (Step 6.4).

A thin async wrapper over the Calendly v2 REST API plus the inbound-webhook
signature/parse helpers. Like the WhatsApp client this module is **transport
only** — it mints links, registers subscriptions, and decodes webhook bodies;
it holds no scheduling-flow logic (which application, what email to send), so
the service and worker layers stay testable against a single monkeypatchable
seam.

Public surface:

- :func:`create_single_use_link` — mint a one-time scheduling link off the
  shared "Interview" event type; returns its ``booking_url``.
- :func:`verify_webhook_signature` / :func:`parse_webhook_event` — authenticate
  and decode an inbound ``invitee.created`` / ``invitee.canceled`` webhook.
- :func:`get_current_user` / :func:`list_event_types` /
  :func:`create_webhook_subscription` — one-time setup helpers used by the
  bootstrap script to discover URIs and register the webhook.

Configuration (from :class:`~src.config.Settings`): ``CALENDLY_PERSONAL_ACCESS_TOKEN``
authorizes every call; ``CALENDLY_EVENT_TYPE_URI`` is the event we mint links
against; ``CALENDLY_WEBHOOK_SIGNING_KEY`` authenticates inbound webhooks. Missing
the token surfaces as :class:`CalendlyConfigError` before any network round-trip.

Webhook bodies carry the candidate's name/email, so they're **never logged** —
only the event kind, the resource URIs, and HTTP status appear.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


# --- Constants --------------------------------------------------------------

CALENDLY_API_BASE_URL: Final[str] = "https://api.calendly.com"

# Single-use links are tied to an *event type* (not a user/org), so Calendly
# requires this discriminator on the create call.
SCHEDULING_LINK_OWNER_TYPE: Final[str] = "EventType"

# A scheduling link the candidate uses exactly once.
SINGLE_USE_EVENT_COUNT: Final[int] = 1

# Inbound webhook event kinds we subscribe to and decode. Calendly's vocabulary
# is larger; we only act on a booking being made or canceled.
EVENT_INVITEE_CREATED: Final[str] = "invitee.created"
EVENT_INVITEE_CANCELED: Final[str] = "invitee.canceled"
SUBSCRIBED_EVENTS: Final[tuple[str, ...]] = (EVENT_INVITEE_CREATED, EVENT_INVITEE_CANCELED)

# Generous ceiling for a single API round-trip; bounds a hung Celery task.
REQUEST_TIMEOUT_SECONDS: Final[float] = 15.0

# Calendly signs webhooks ``Calendly-Webhook-Signature: t=<unix>,v1=<hexdigest>``;
# the HMAC-SHA256 is computed over ``"{t}.{raw_body}"``. We reject signatures
# whose timestamp is older than this to blunt replay of a captured payload.
WEBHOOK_SIGNATURE_TOLERANCE_SECONDS: Final[int] = 300


# --- Exceptions -------------------------------------------------------------


class CalendlyConfigError(RuntimeError):
    """Raised when required Calendly settings are missing."""


class CalendlyApiError(RuntimeError):
    """Raised when Calendly rejects a request or the transport fails.

    ``status_code`` carries Calendly's HTTP status (``None`` for a
    connection/timeout error) so the caller can decide transient-vs-permanent
    for Celery retry.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# --- Value types ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalendlyInviteeEvent:
    """A decoded inbound ``invitee.*`` webhook.

    ``tracking_token`` is the ``utm_content`` we embedded in the single-use
    link — the key that maps this booking back to an interview booking row.
    ``start_time`` / ``end_time`` are populated on a ``created`` event (the
    booked slot) and may be ``None`` on a ``canceled`` event.

    The remaining fields enrich the HR view of a booking:

    * ``invitee_email`` / ``invitee_timezone`` — who booked and in which zone.
    * ``location_type`` / ``join_url`` / ``location_text`` — the meeting medium
      (``zoom`` / ``google_conference`` / ``physical`` / ``custom`` / …) and
      either its join URL (virtual) or its address/phone text (physical/call).
    * ``rescheduled`` — true on a ``created`` event that replaces an earlier
      slot, so the service records it as a reschedule rather than a first book.
    * ``cancel_reason`` / ``canceled_by`` — populated on a ``canceled`` event
      (the reason text and whether the ``invitee`` or ``host`` canceled).
    """

    kind: str  # EVENT_INVITEE_CREATED | EVENT_INVITEE_CANCELED
    tracking_token: str | None
    invitee_uri: str | None
    event_uri: str | None
    start_time: datetime | None
    end_time: datetime | None
    invitee_email: str | None = None
    invitee_timezone: str | None = None
    location_type: str | None = None
    join_url: str | None = None
    location_text: str | None = None
    rescheduled: bool = False
    cancel_reason: str | None = None
    canceled_by: str | None = None


# --- Helpers ----------------------------------------------------------------


def _access_token() -> str:
    """Return the configured PAT or raise.

    Validated lazily (not at import) so the app/worker boot without Calendly
    configured — only an actual API call requires it.
    """
    token = settings.calendly_personal_access_token
    if not token:
        raise CalendlyConfigError(
            "CALENDLY_PERSONAL_ACCESS_TOKEN is not configured; cannot call Calendly"
        )
    return token


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
    }


def _raise_for_status(response: httpx.Response, *, action: str) -> None:
    """Translate a non-2xx Calendly response into :class:`CalendlyApiError`.

    Calendly error bodies echo a ``message`` / ``title`` that can include
    request detail, so we surface the status and our own ``action`` label, not
    the server text.
    """
    if response.is_success:
        return
    raise CalendlyApiError(
        f"Calendly {action} failed (HTTP {response.status_code})",
        status_code=response.status_code,
    )


async def _request(
    method: str, url: str, *, action: str, json: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Issue one authorized request and return the parsed JSON body.

    A fresh :class:`httpx.AsyncClient` is created per call (no pooling): Celery
    bridges sync→async with ``asyncio.run`` (a new event loop per task), and a
    connection pool bound to a closed loop raises "Event loop is closed" — the
    same constraint documented in the WhatsApp and Azure Blob clients.
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        try:
            response = await client.request(method, url, headers=_auth_headers(), json=json)
        except httpx.HTTPError as exc:
            # Connection/timeout errors are transient — let the caller retry.
            raise CalendlyApiError(f"Calendly {action} transport error: {exc}") from exc

    _raise_for_status(response, action=action)
    body = response.json()
    return body if isinstance(body, dict) else {}


# --- Public API: scheduling links -------------------------------------------


async def create_single_use_link(event_type_uri: str) -> str:
    """Mint a single-use scheduling link for ``event_type_uri``.

    Returns the ``booking_url`` (valid 90 days, usable once). The caller appends
    its own ``utm_content`` tracking param before sending, so the inbound
    webhook can map the booking back to an application.
    """
    payload = {
        "max_event_count": SINGLE_USE_EVENT_COUNT,
        "owner": event_type_uri,
        "owner_type": SCHEDULING_LINK_OWNER_TYPE,
    }
    body = await _request(
        "POST",
        f"{CALENDLY_API_BASE_URL}/scheduling_links",
        action="create_scheduling_link",
        json=payload,
    )
    resource = body.get("resource")
    booking_url = resource.get("booking_url") if isinstance(resource, dict) else None
    if not isinstance(booking_url, str) or not booking_url:
        raise CalendlyApiError("Calendly returned 2xx but no booking_url")
    logger.info("calendly.scheduling_link.created")
    return booking_url


# --- Public API: one-time setup helpers -------------------------------------


async def get_current_user() -> dict[str, Any]:
    """Return the ``resource`` from ``GET /users/me``.

    Used by the bootstrap script to discover the user + organization URIs the
    webhook subscription needs. Includes ``uri`` and ``current_organization``.
    """
    body = await _request("GET", f"{CALENDLY_API_BASE_URL}/users/me", action="get_current_user")
    resource = body.get("resource")
    return resource if isinstance(resource, dict) else {}


async def list_event_types(user_uri: str) -> list[dict[str, Any]]:
    """Return the active event types owned by ``user_uri``.

    A setup convenience so the bootstrap script can print event-type URIs for
    the operator to pick the "Interview" one.
    """
    url = f"{CALENDLY_API_BASE_URL}/event_types?user={user_uri}&active=true"
    body = await _request("GET", url, action="list_event_types")
    collection = body.get("collection")
    return collection if isinstance(collection, list) else []


async def create_webhook_subscription(
    *, callback_url: str, organization_uri: str, signing_key: str
) -> dict[str, Any]:
    """Register an organization-scoped ``invitee.*`` webhook subscription.

    ``signing_key`` is a secret we generate and keep: Calendly signs every
    delivery with it, and :func:`verify_webhook_signature` checks against it.
    Returns the created subscription ``resource``.
    """
    payload = {
        "url": callback_url,
        "events": list(SUBSCRIBED_EVENTS),
        "organization": organization_uri,
        "scope": "organization",
        "signing_key": signing_key,
    }
    body = await _request(
        "POST",
        f"{CALENDLY_API_BASE_URL}/webhook_subscriptions",
        action="create_webhook_subscription",
        json=payload,
    )
    resource = body.get("resource")
    logger.info("calendly.webhook_subscription.created")
    return resource if isinstance(resource, dict) else {}


# --- Inbound webhook: signature + parsing -----------------------------------


def _parse_signature_header(signature_header: str) -> tuple[str, str] | None:
    """Pull ``(t, v1)`` out of a ``t=<unix>,v1=<hex>`` header, or ``None``."""
    parts: dict[str, str] = {}
    for segment in signature_header.split(","):
        key, _, value = segment.partition("=")
        parts[key.strip()] = value.strip()
    timestamp, signature = parts.get("t"), parts.get("v1")
    if not timestamp or not signature:
        return None
    return timestamp, signature


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str | None,
    signing_key: str,
    *,
    tolerance_seconds: int = WEBHOOK_SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """Return whether ``signature_header`` authenticates ``raw_body``.

    Calendly computes ``HMAC-SHA256(signing_key, "{t}.{raw_body}")`` and sends
    it as ``Calendly-Webhook-Signature: t=<unix>,v1=<hexdigest>``. The HMAC is
    taken over the **exact bytes received** (re-serializing parsed JSON would
    change the digest). Comparison is constant-time, and a timestamp older than
    ``tolerance_seconds`` is rejected to blunt replay.
    """
    if not signature_header:
        return False
    parsed = _parse_signature_header(signature_header)
    if parsed is None:
        return False
    timestamp, provided = parsed

    try:
        age = time.time() - int(timestamp)
    except ValueError:
        return False
    if age > tolerance_seconds:
        return False

    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    expected = hmac.new(signing_key.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_calendly_time(value: Any) -> datetime | None:
    """Parse a Calendly ISO-8601 UTC timestamp (``...Z``) to an aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _str_or_none(value: Any) -> str | None:
    """A non-empty string from a payload field, else ``None``."""
    return value if isinstance(value, str) and value else None


def parse_webhook_event(body: dict[str, Any]) -> CalendlyInviteeEvent | None:
    """Decode a webhook POST body into a :class:`CalendlyInviteeEvent`, or ``None``.

    Returns ``None`` for event kinds we don't handle. The booked slot and
    tracking token live under ``payload`` (``payload.scheduled_event``,
    ``payload.tracking.utm_content``); the meeting location is on
    ``payload.scheduled_event.location`` and the cancellation detail on
    ``payload.cancellation``.
    """
    kind = body.get("event")
    if kind not in SUBSCRIBED_EVENTS:
        return None

    payload = _as_dict(body.get("payload"))
    tracking = _as_dict(payload.get("tracking"))
    scheduled_event = _as_dict(payload.get("scheduled_event"))
    location = _as_dict(scheduled_event.get("location"))
    cancellation = _as_dict(payload.get("cancellation"))

    token = tracking.get("utm_content")
    invitee_uri = payload.get("uri")
    event_uri = scheduled_event.get("uri")

    return CalendlyInviteeEvent(
        kind=str(kind),
        tracking_token=token if isinstance(token, str) and token else None,
        invitee_uri=invitee_uri if isinstance(invitee_uri, str) else None,
        event_uri=event_uri if isinstance(event_uri, str) else None,
        start_time=_parse_calendly_time(scheduled_event.get("start_time")),
        end_time=_parse_calendly_time(scheduled_event.get("end_time")),
        invitee_email=_str_or_none(payload.get("email")),
        invitee_timezone=_str_or_none(payload.get("timezone")),
        # Calendly's location object: ``join_url`` for virtual meetings,
        # ``location`` (free text) for a physical address or a phone number.
        location_type=_str_or_none(location.get("type")),
        join_url=_str_or_none(location.get("join_url")),
        location_text=_str_or_none(location.get("location")),
        rescheduled=bool(payload.get("rescheduled")),
        cancel_reason=_str_or_none(cancellation.get("reason")),
        # ``canceler_type`` is the role (``invitee`` / ``host``); fall back to
        # the free-text ``canceled_by`` name when the role isn't present.
        canceled_by=_str_or_none(cancellation.get("canceler_type"))
        or _str_or_none(cancellation.get("canceled_by")),
    )
