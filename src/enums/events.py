"""Realtime event enumerations for the HR Server-Sent-Events stream.

Single source of truth for the shared pub/sub channel name and the closed
set of event *types* and *states* that flow over it. Background jobs (hard
filter, authenticity, similarity, WhatsApp ingest) publish a small envelope
to :data:`HR_EVENTS_CHANNEL`; the SSE endpoint relays it to every connected
HR browser, which then refetches the affected resource through the normal
authed REST endpoints.

Design law (enforced structurally by :mod:`src.schemas.events`): the
envelope is a *pointer*, never the *payload*. It names what changed
(``event`` + ``application_id``), never the value that changed — no score
numbers, no breakdowns, and above all no candidate-authored WhatsApp text.
Those are PII / confidential and must not traverse Redis pub/sub or the
stream; they stay behind the authed endpoints the FE calls afterward.

The whole feature is best-effort: ``applications.pipeline_status`` (and the
underlying rows) remain the source of truth, so a dropped or missed event
only ever means "the UI updates a beat later", never "the UI is wrong".
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# Single shared channel for the whole HR workspace. The system has no
# org/tenant boundary today (every HR user sees every application), so one
# channel is correct: all background jobs publish here, all HR streams
# subscribe here. Move to per-user channels (``events:user:{id}``) only when
# real tenancy is introduced — that fan-out is the painful retrofit, this
# constant is the single seam where it would change.
HR_EVENTS_CHANNEL: Final[str] = "events:hr"


class AppEventType(StrEnum):
    """What kind of change a published envelope announces.

    Each value maps to one background producer. Adding a new realtime
    signal is an enum entry plus a publish call at that producer's terminal
    commit — the SSE relay and the single FE stream need no changes because
    they are type-agnostic (they forward the envelope; the FE routes on
    ``event`` + ``application_id``).
    """

    HARD_FILTER = "hard_filter"
    AUTHENTICITY = "authenticity"
    SIMILARITY = "similarity"
    WHATSAPP_MESSAGE = "whatsapp_message"
    # Presence-style, like WHATSAPP_MESSAGE: the interview booking for an
    # application changed (invite sent/failed, slot booked, or canceled). The
    # FE refetches the application's interview block — which carries the real
    # state — so no ``state`` is needed on the envelope.
    INTERVIEW = "interview"


class AppEventState(StrEnum):
    """Terminal outcome for score-style events.

    Mirrors the ``ok`` / ``failed`` vocabulary already used in
    ``pipeline_status`` (:mod:`src.enums.cv_pipeline`) so the FE reads one
    consistent set of states. Omitted entirely for presence-style events
    such as :attr:`AppEventType.WHATSAPP_MESSAGE`, where the arrival of the
    event *is* the signal and there is no success/failure to report.
    """

    OK = "ok"
    FAILED = "failed"
