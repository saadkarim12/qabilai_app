"""Application configuration loaded from environment variables.

Phase 0/1 requires only ``DATABASE_URL``, ``REDIS_URL`` and ``APP_SECRET_KEY``.
Later-phase fields (AI keys, integrations, SMTP) default to ``None`` and are
validated lazily — the service that needs them raises if they're missing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

import phonenumbers
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings sourced from env / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_env: Literal["development", "test", "production"] = "development"
    app_port: int = Field(8000, ge=1, le=65535)
    app_version: str = "0.1.0"
    app_public_url: str = "http://localhost:8000"
    app_secret_key: str = Field(..., min_length=32)

    # --- CORS ---
    # NoDecode keeps pydantic-settings from JSON-decoding the env value so the
    # field_validator below can split a comma-separated string instead.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- Database ---
    database_url: str
    database_pool_size: int = Field(10, ge=1, le=100)

    # --- Redis ---
    redis_url: str

    # --- Celery (Step 2.6+) ---
    # Separate Redis DB indices keep broker/result traffic isolated from the
    # app cache (db 0) and the AI prompt/embedding caches (db 1/2).
    celery_broker_url: str = "redis://localhost:6379/3"
    celery_result_backend: str = "redis://localhost:6379/4"
    # Tests flip this to True so eager-mode runs tasks in-process.
    celery_task_always_eager: bool = False

    # --- DB pool override ---
    # Set in the Celery worker container so the DB engine uses NullPool.
    # The worker bridges sync->async per task via ``asyncio.run`` which
    # spins a fresh event loop each call; a pooled asyncpg connection
    # created in one loop is invalid in the next ("Future attached to a
    # different loop"). NullPool sidesteps this by creating a connection
    # per session, in whatever loop is current at checkout. The api
    # container leaves this at False so request handlers share connections
    # via the normal pool.
    # NOTE: name deliberately avoids the ``CELERY_`` prefix — Celery reads
    # any ``CELERY_*`` env var as its own config and ``CELERY_WORKER_POOL``
    # would shadow its ``-P/--pool`` knob.
    db_use_null_pool: bool = False

    # --- Azure Blob (Step 3.2+) ---
    azure_blob_connection_string: str | None = None
    azure_blob_container: str = "kabil-cvs"

    # --- AI providers (Step 2.2 / 2.3+) ---
    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    anthropic_api_key: str | None = None

    # --- WhatsApp (Step 5.1+) ---
    meta_wa_phone_number_id: str | None = None
    meta_wa_access_token: str | None = None
    meta_wa_app_secret: str | None = None
    meta_wa_verify_token: str | None = None
    # Pre-approved message template for the screening invite. Cold candidates
    # (who have not messaged us first) cannot receive free-form text outside the
    # 24h customer-service window, so the *first* contact must be a template.
    # Leave unset in dev/test (inside the window the interactive-button greeting
    # works); set it in prod to the approved template's name once Meta approves.
    meta_wa_invite_template_name: str | None = None
    meta_wa_invite_template_language: str = "en"

    # --- Realtime events (SSE) ---
    # Shared HR event stream. Publish + subscribe both use ``redis_url``
    # (db 0); pub/sub requires producer and consumer on the same db. The
    # whole feature is best-effort, so this kill switch turns it into a no-op
    # (publishers skip, the stream endpoint can be left unmounted) without
    # touching the rest of the pipeline.
    sse_enabled: bool = True
    # Lifetime of a one-time connection ticket. The browser EventSource API
    # can't send an Authorization header, so the FE trades its Bearer token
    # for a short-lived single-use ticket (held in Redis) and opens the
    # stream with that — keeping the real token out of URLs and logs.
    sse_ticket_ttl_seconds: int = Field(60, ge=5, le=600)
    # Keepalive comment cadence on an open stream. Kept under the ~30-60s
    # idle-connection timeout of typical reverse proxies so the connection
    # isn't reaped mid-wait.
    sse_heartbeat_seconds: int = Field(25, ge=5, le=120)

    # --- Google Calendar (Step 6.1+) ---
    google_service_account_json: str | None = None
    google_calendar_id: str | None = None

    # --- Calendly (Step 6.4 interview scheduling) ---
    # One shared Calendly account drives interview availability. The PAT
    # authorizes API calls; the event-type URI is the single "Interview" event
    # we mint single-use links against; the organization URI scopes the webhook
    # subscription; the signing key (one we generate and pass when registering
    # the subscription) authenticates inbound ``invitee.*`` webhooks. All
    # validated lazily — only the scheduling flow needs them.
    calendly_personal_access_token: str | None = None
    calendly_event_type_uri: str | None = None
    calendly_organization_uri: str | None = None
    calendly_webhook_signing_key: str | None = None
    # Kill switch (mirrors ``sse_enabled``): when False the stage→interview hook
    # is a no-op and the webhook route 404s, so the rest of the pipeline runs
    # unchanged on a deployment without Calendly configured.
    interview_scheduling_enabled: bool = False

    # --- SMTP (Step 6.4+) ---
    smtp_host: str | None = None
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_from: str = "noreply@kabil.ai"

    # --- Resend (HTTP email transport) ---
    # When ``resend_api_key`` is set, email goes out via Resend's HTTPS API
    # instead of SMTP. Required on hosts that block outbound SMTP ports (e.g.
    # Railway blocks 25/465/587), where ``aiosmtplib`` can't connect at all.
    # Leave unset to use the SMTP path (local MailHog / a relay that allows it).
    # ``resend_from`` must be a Resend-valid sender: the shared
    # ``onboarding@resend.dev`` works immediately for sending to your own
    # account email; set it to an address on a verified domain to reach others.
    resend_api_key: str | None = None
    resend_from: str = "onboarding@resend.dev"

    # --- Brevo (HTTP email transport) ---
    # Preferred HTTP transport when set (chosen before Resend/SMTP). Unlike
    # Resend's test mode, Brevo can email *any* recipient once the sender
    # address is verified — single-sender verification, no domain/DNS needed —
    # so it's the path that reaches real candidates on Railway (SMTP blocked).
    # ``brevo_from`` MUST be a Brevo-verified sender (your signup email is
    # auto-verified); ``brevo_from_name`` is the display name on the envelope.
    brevo_api_key: str | None = None
    brevo_from: str | None = None
    brevo_from_name: str = "Kabil"

    # --- Tuning (per architecture doc) ---
    similarity_rejection_threshold: float = Field(60.0, ge=0.0, le=100.0)
    cv_max_file_size_mb: int = Field(10, ge=1, le=100)
    whatsapp_reply_timeout_hours: int = Field(24, ge=1)
    # Debounce window for coalescing a candidate's rapid-fire WhatsApp messages
    # into a single screening answer. People often split one answer across
    # several bubbles; without this each bubble would consume a question and
    # race the candidate ahead. After the last inbound message we wait this many
    # seconds (no newer message arriving) before scoring + advancing. 0 disables
    # debouncing (each message advances immediately).
    whatsapp_answer_debounce_seconds: float = Field(3.0, ge=0.0, le=30.0)
    interview_reply_timeout_hours: int = Field(48, ge=1)
    # Hours after the invite email before a single booking reminder is sent to a
    # candidate who still hasn't scheduled. Kept below
    # ``interview_reply_timeout_hours`` so the reminder lands before the timeout
    # flags the application for HR.
    interview_reminder_hours: int = Field(24, ge=1)
    talent_pool_expiry_days: int = Field(365, ge=1)

    # --- Phone parsing ---
    # Default region (ISO 3166-1 alpha-2, e.g. "PK", "AE") used to interpret
    # national-format CV numbers that lack a country code — a bare
    # ``03001234567`` normalizes to ``+92…`` when this is ``"PK"``. Leave unset
    # (None) to keep the country-agnostic default: every number must carry an
    # explicit ``+<cc>`` prefix or it is dropped. Set it only for single-market
    # deployments, since a guessed region produces wrong E.164 — and therefore
    # wrong candidate dedup — for applicants from other countries.
    default_phone_region: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("default_phone_region", mode="before")
    @classmethod
    def _validate_phone_region(cls, v: object) -> object:
        # Normalize to upper-case and reject codes phonenumbers doesn't know,
        # so a typo fails loud at startup rather than silently dropping every
        # national number at parse time.
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if not isinstance(v, str):
            return v  # let pydantic raise the type error
        region = v.strip().upper()
        if region not in phonenumbers.SUPPORTED_REGIONS:
            raise ValueError(
                f"DEFAULT_PHONE_REGION {v!r} is not a valid ISO 3166-1 alpha-2 "
                "region code (e.g. 'PK', 'AE')"
            )
        return region

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings instance."""
    return Settings()


settings: Settings = get_settings()
