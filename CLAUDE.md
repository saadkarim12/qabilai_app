# CLAUDE.md

Guidance for Claude Code when working in this repository. Read this first.

## What this is

**Kabil.ai backend** — an AI-driven hiring-automation API. HR posts jobs;
candidates apply with a CV via a public link; the system parses, embeds,
scores, and screens each CV through an async pipeline, surfacing an
explainable verdict (relevancy + CV fit + authenticity) so HR can triage.

Full design lives in [`docs/KABIL_AI_ARCHITECTURE.md`](docs/KABIL_AI_ARCHITECTURE.md);
work is delivered in numbered steps (the codebase references "Step 3.9",
"Step 4.3", etc. — these map to that doc).

## Tech stack

- **Python 3.12** (pinned `>=3.12,<3.13`; CI + Docker enforce it)
- **FastAPI** + Pydantic v2 (`pydantic-settings` for config)
- **SQLAlchemy 2.0 async** + asyncpg + **Alembic** migrations
- **PostgreSQL 16 + pgvector** (1536-d embeddings)
- **Celery 5** + **Redis** (broker/result backend + caches)
- **Anthropic Claude** (LLM reasoning, via **LangChain** `langchain-anthropic`)
  + **OpenAI** (embeddings only)
- **Azure Blob Storage** (CV PDFs)
- Tooling: **ruff** (lint+format), **mypy --strict**, **pytest** (+asyncio)

## Common commands

| Command | Purpose |
|---|---|
| `make install` | Create `.venv`, install runtime + dev deps |
| `make dev` | Run uvicorn with `--reload` on :8000 |
| `make up` / `make down` | `docker compose up -d --build` / tear down |
| `make logs` | Tail the API container |
| `make test` | Full pytest (needs Postgres + Redis up) |
| `make test-unit` | pytest excluding `-m integration` |
| `make lint` | `ruff check` + `ruff format --check` |
| `make typecheck` | `mypy --strict` against `src/` |
| `make check` | lint + typecheck + test |
| `make migrate` | `alembic upgrade head` |
| `make seed` | Create the first admin user (idempotent, interactive) |

Direct invocations (when not using make): `.venv/bin/pytest`,
`.venv/bin/ruff check`, `.venv/bin/mypy --strict src`. Run a single test:
`.venv/bin/pytest tests/integration/test_applications_routes.py::<name>`.

**Docker note:** `src/` is baked into the image as a wheel (not volume-mounted).
After changing `src/`, rebuild to see it in the container:
`docker compose build api && docker compose up -d --force-recreate api`.
API is on host **:8000**; Postgres on host **:5433** (→ container 5432, to
avoid clashing with a host Postgres on 5432).

## Project layout

```
src/
├── main.py            # FastAPI factory (create_app), CORS + correlation-id mw
├── config.py          # Pydantic Settings (env-driven; get_settings is lru_cached)
├── logging_config.py  # structured logging setup
├── api/
│   ├── routes/        # auth, jobs, applications, public
│   ├── deps.py        # auth dependencies (get_current_hr_user, etc.)
│   └── exception_handlers.py
├── services/          # business logic (see below)
│   ├── scoring/       # hard_filter_service, similarity_breakdown
│   └── authenticity/  # aggregator, llm_judge (single Sonnet judge)
├── workers/           # celery_app + tasks/ (cv_pipeline, job_pipeline, scoring)
├── ai/                # claude.py, embeddings.py, cv_parser, prompts/ (*.j2)
├── db/                # models/, session.py, base.py, types.py
├── schemas/           # Pydantic request/response shapes
├── enums/             # ALL constants/thresholds live here (no inline literals)
├── integrations/      # azure_blob
└── utils/             # phone, pdf, jwt, password, language, sanitization, ...
tests/{unit,integration}/   # integration tests need the dev containers up
alembic/versions/           # numbered migrations 0001..0012
```

## Domain model & core concepts

**Entities** (`src/db/models/`): `User`, `AuthSession`, `Job`, `Candidate`,
`CvDocument`, `Application`, `ApplicationScore`, `PublicUploadToken`, `AuditLog`.

- A **Candidate** is keyed by (email, phone); CV-level properties
  (`parsed_profile`, and the job-agnostic authenticity *baseline*
  `authenticity_*`) live here and are shared across all their applications.
  The job-aware authenticity score is per-application (`applications.authenticity_*`).
- An **Application** = one candidate's submission to one job. It has a
  **stage** (`vector_screen → hard_filter → whatsapp → interview → done`) and
  an orthogonal **status** (`active / rejected / accepted / archived`).
  Rejection preserves the stage so HR sees *where* it happened. `archived` is
  set only by move-to-pool (a history-preserving alternative to deletion);
  uniqueness on `(candidate_id, job_id)` is a **partial** index over
  non-archived rows, so a candidate can hold one live application per job plus
  any number of archived stints.
- **`application_scores`** is an **append-only audit table** — one row per
  score-computation attempt (`score_type`, `value`, `breakdown`,
  `prompt_version`, `model_used`, `computed_at`). Never updated; a rescore
  inserts a new row.

### The three score families (`src/enums/score_types.py` → `ScoreType`)

| Family | Computed by | Model | Persisted to |
|---|---|---|---|
| **similarity** | CV pipeline, automatic | OpenAI embedding cosine | `applications.similarity_score` + an `application_scores` row |
| **hard_filter** | HR moves app to `hard_filter` stage | **Claude Opus** (5-signal weighted aggregate, the "CV score") | `applications.hard_filter_score` + an `application_scores` row |
| **authenticity** | CV pipeline, automatic | **Claude Sonnet** (single LLM judge, evidence-quoting) | **two rows**: candidate *baseline* (`candidates.authenticity_*`) + application *job-aware* (`applications.authenticity_*`) — NOT in `application_scores` |

- Hard-filter signals: skills/experience/industry/regional/role-seniority,
  weighted 35/35/10/10/10.
- Authenticity (0–100, banded **authentic ≥75 / review ≥50 / fabricated <50**;
  higher = more authentic). One Sonnet call scores each signal 0–100 with an
  evidence-quoting `finding`; the aggregator weights them and derives a
  per-signal `concern` (score < 60) + a `top_concerns` list + keeps the
  judge's `rationale`. **Two calibrations:**
  - **baseline** (5 job-agnostic signals: specificity, timeline_coherence,
    consistency, linguistic_genericity, structural_templating; weights
    25/20/20/15/10 renormalised over 0.90) — a CV-level property on the
    **candidate** row, what the talent pool surfaces. Runs for pool-only CV
    uploads (no job).
  - **job-aware** (all 6 signals: baseline + `jd_keyword_mirroring`; weights
    25/20/20/15/10/10 summing to 1.0) — per **application**, needs the job for
    the mirroring signal. The CV pipeline's authenticity step is
    application-keyed: it runs the job-aware judge and, on the candidate's
    first scored application, derives the baseline from the same result (no
    extra Claude call). `model_used` is `claude-sonnet`. Prompt versions:
    `authenticity_judge:{baseline,jobaware}:{base,strict}:v2`. On a double
    schema-bind failure every signal falls back to 50.
- `ScoreModel` (`model_used`) is open-ended varchar in DB; the enum captures
  today's values: `claude-opus/sonnet/haiku`, `openai-text-embedding-3-small`,
  `deterministic`, `blended`. `prompt_version` is `"-"` when no LLM prompt applies.
- The application detail endpoint (`GET /applications/{id}`) synthesizes the
  authenticity entry into the `scores[]` list (with `id=None` since no row
  backs it), preferring the application's job-aware verdict and falling back
  to the candidate baseline, so every score's breakdown lives in one place.

## Async pipelines (Celery)

Tasks are **idempotent** (each checks if its output already exists and returns
early) and bridge sync Celery → async via `asyncio.run(...)`. Per-step state is
recorded in a `pipeline_status` JSONB column (`pending`/`ok`/`failed`, with
`{step}_error` on failure). Transient provider errors re-raise for Celery retry
(3×, 60s backoff; embeddings get 5×); permanent errors record `failed` and let
the chain continue (downstream tasks self-skip on missing prerequisites).

**Job pipeline** (on `draft → open`, two parallel tasks): `embed_jd_task`
(→ `jobs.jd_embedding`) and `generate_questions_task` (→ `jobs.whatsapp_questions`,
Claude). `ready_for_applications` flips true only when both are `ok`.

**CV pipeline** (on CV upload, a linear `chain()` of immutable `.si()` signatures):
```
extract_text → parse_cv → compute_application_authenticity → embed_cv
  → score_similarity → auto_reject_below_threshold
```
(The talent-pool CV chain runs `compute_pool_authenticity` — baseline only —
in place of the application-keyed authenticity step and stops after `embed_cv`.)
Auto-reject flips `status=rejected` when cosine *distance* exceeds
`SIMILARITY_REJECTION_THRESHOLD` (stage stays `vector_screen`). Hard-filter
scoring is **not** in this chain — it runs when HR PATCHes the app to the
`hard_filter` stage (or via the rescore endpoint with `force=True`, which also
bypasses the Claude cache).

## Realtime events (SSE)

So the FE learns a slow async result landed (e.g. a hard-filter score after an
L1 move) without polling, the backend pushes notifications over **Server-Sent
Events** on a single shared channel.

- **One shared channel** `events:hr` (`src/enums/events.py`), Redis pub/sub on
  db 0 (`redis_url`). The system is a single shared workspace (no org/tenancy),
  so all producers publish here and all HR streams subscribe here. Per-user
  channels are the retrofit point if tenancy is ever added.
- **Pointer-not-payload**, enforced structurally. Producers publish a tiny
  `AppEvent` envelope (`src/schemas/events.py`, `extra="forbid"`):
  `{event, application_id, job_id?, state?}` — never a score value, breakdown,
  or WhatsApp text. The FE refetches the affected resource through the normal
  authed REST endpoint. **Never** put PII/scores on this channel.
- **Best-effort.** `publish_app_event` (`src/services/events_service.py`)
  swallows+logs any Redis failure and is a no-op when `settings.sse_enabled` is
  False. Postgres (`pipeline_status` + the rows) stays the source of truth; a
  missed event only means the UI updates a beat later. Publishers fire **after
  the commit** so a refetch sees committed data.
- **Producers** (each at its terminal commit): hard filter `ok`/`failed`
  (`workers/tasks/scoring.py`); authenticity `ok` (for the scored application —
  the job-aware score is per-application now; the pool-only baseline fires no
  event) and similarity `ok` (`workers/tasks/cv_pipeline.py`); a
  `whatsapp_message` nudge when an inbound message is recorded
  (`workers/tasks/whatsapp.py`).
- **Transport** is `sse-starlette` (`src/api/routes/events.py`); it owns
  heartbeats (`ping=sse_heartbeat_seconds`) and disconnect cleanup.
- **Connection auth**: `EventSource` can't send the `Authorization` header, so
  an HR caller `POST /events/ticket` (Bearer-authed) for a short-lived
  **single-use** ticket (Redis `GETDEL`, `src/services/sse_ticket_service.py`)
  and opens `GET /events/stream?ticket=…`. The real token never enters a URL.
- **FE contract**: open one stream per tab on mount (after a REST fetch to seed
  state); on each event, refetch just the named `application_id`; on
  disconnect/timeout, reconnect or fall back to one poll. Knobs:
  `sse_enabled` / `sse_ticket_ttl_seconds` / `sse_heartbeat_seconds`.

## AI provider conventions — IMPORTANT

- **Anthropic (Claude) only for LLM work; OpenAI only for embeddings.**
  Never use `openai.chat.completions` / OpenAI for reasoning.
- **LangChain is the transport for Claude** — `src/ai/claude.py` calls Claude
  through `langchain_anthropic.ChatAnthropic` (`.ainvoke`) using `langchain_core`
  message/prompt types. The Anthropic SDK's own retry handles transient errors
  (`max_retries=ANTHROPIC_MAX_RETRIES`). Treat LangChain as an internal
  implementation detail of this module — don't reach for `langchain_*` elsewhere;
  go through the `claude.py` wrappers. (`langchain-postgres` is a declared dep but
  currently **unused**; pgvector is accessed via raw SQLAlchemy.)
- All Claude calls go through **`src/ai/claude.py`** — `call_claude` (text/JSON)
  or `call_claude_structured` (tool-use → validated Pydantic schema). Both handle
  Redis caching (7-day TTL, keys `claude:v1:sha256:` / `claude:struct:v1:sha256:`,
  sound only at `temperature=0.0`), SDK retries, and PII-safe logging;
  `call_claude` also does optional JSON parsing (code-fence stripping). Prompts
  are Jinja2 templates in `src/ai/prompts/*.j2` rendered via `render_prompt`
  (raw Jinja) or `load_chat_prompt` (LangChain `ChatPromptTemplate`), both
  `StrictUndefined` so a missing variable fails loud.
- **Model selection by kind of work** (`src/enums/claude_models.py`): `HAIKU` =
  high-volume structured judging (WhatsApp answer scoring, contact extraction);
  `SONNET` = routine extraction/classification + the authenticity judge (the
  default); `OPUS` = heavyweight reasoning (hard-filter scoring). When choosing
  `OPUS`, leave a one-line comment saying why.
  Note: Opus rejects the `temperature` param — `call_claude` omits it for Opus.

## Conventions & guardrails — FOLLOW THESE

**Constants / enums**
- **No hardcoded magic values.** Tunable numbers live in
  `src/enums/thresholds.py`; per-env overridable knobs live in `config.py` as
  `Settings` fields (services read `settings.<field>`). Model IDs, score types,
  stages, bands, etc. all come from enums — never inline a string/number that
  belongs to a closed set.

**Security & PII**
- `.env` is gitignored; **never hardcode secrets** — read from `Settings`.
- **Never log** prompt content, model responses, CV text, or raw bytes — they're
  PII or confidential JD detail. Logs carry only model + token counts +
  optional `prompt_version`.

**Data integrity**
- **Never fabricate values** (scores, IDs, sample data). If a value is unknown,
  ask rather than inventing one.

**Git / commits**
- **Do not commit unless the user explicitly authorizes it.** Propose a commit
  message; let the user run the commit.
- **Never `git add -A` / `git add .`** — stage specific files only.
- **Never skip pre-commit hooks**; never amend already-committed work.
- If on `main`, branch before committing.
- Commit-message trailer:
  `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`

**Workflow**
- Work **one step at a time**; ask before assuming scope. Prefer the smallest
  change that satisfies the request.
- After changes, keep it green: `make lint && make typecheck && make test`
  (or the direct `.venv/bin/...` equivalents). Integration tests need the
  dev Postgres + Redis containers up.

## Data layer & migrations

- Models register on `Base.metadata` (`src/db/models/__init__.py`); Alembic
  `env.py` imports it for autogenerate. Migrations are numbered
  `0001_…` … `0012_…` and applied with `make migrate`.
- The Celery **worker** sets `DB_USE_NULL_POOL=true` (see `config.py` /
  `db/session.py`): each task calls `asyncio.run`, spinning a fresh event loop,
  and a pooled asyncpg connection is invalid across loops — `NullPool` creates a
  connection per session. The API container keeps the normal pool.
- Redis DB index map: `0` app cache, `1/2` AI prompt/embedding caches,
  `3` Celery broker, `4` Celery result backend.

## API surface

All HR routes require auth via `get_current_hr_user` (Bearer token; roles
`admin` / `hiring_manager`). The public apply route is anonymous (slug-based);
the SSE stream is ticket-authed (see Realtime events).

- **`/auth`** — `POST /login`, `POST /logout`, `GET /me`
- **`/dashboard`** — HR-authed, read-only home-screen aggregates (single shared
  workspace, so nothing to scope by). All in `dashboard_service.py` /
  `schemas/dashboard.py`; dashboard-only closed sets + stage groupings live in
  `enums/dashboard.py`, tunable day-counts in `enums/thresholds.py`.
  - `GET /dashboard` — **Overview**: jobs by status, applications by stage +
    status, candidate + active talent-pool totals. `by_status`/`by_stage` maps
    are zero-filled across every enum value.
  - `GET /dashboard/performance` — **Performance** table: one row per non-draft
    job with `candidates`/`shortlisted` (non-archived; shortlisted = reached
    `done`), `days_open` (`(closed_at or now) − created_at`, calendar days), and
    a `JobHealth` verdict. Health is **age-based on open jobs, in working days
    (Mon–Fri)**: `unhealthy` (open > `JOB_UNHEALTHY_WORKING_DAYS`=20 working
    days) → `at_risk` (open ≥ `JOB_AT_RISK_WORKING_DAYS`=18 working days) →
    `healthy` (younger open jobs, and any non-open job). Sorted worst-health
    first, then `days_open` desc. (`shortlisted` is still a per-row *count*, no
    longer a health input.)
  - `GET /dashboard/pipeline?job_id=…` — **Candidate Pipeline** funnel. Active
    apps collapsed into four `PipelineBucket`s (`STAGE_TO_BUCKET`: sourcing =
    vector_screen+hard_filter, screening = whatsapp, interview, final_shortlist
    = done; zero-filled). Plus Applied→Offer conversion (`applied` = non-archived,
    `offers` = accepted). Omit `job_id` for the All-Jobs view.
  - `GET /dashboard/upcoming-interviews?limit=…` — nearest booked, future
    `interview_bookings` (soonest first; `limit` default
    `DASHBOARD_UPCOMING_INTERVIEWS_PREVIEW`=3, max 200) + a `total` of all future
    booked interviews for the "show all" view.
  - `GET /dashboard/pending-feedback` — apps stalled at the `interview` stage
    (active, `stage_updated_at` older than `INTERVIEW_PENDING_FEEDBACK_DAYS`=3),
    oldest first.
- **`/jobs`** — `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`,
  `PATCH /jobs/{id}/status` (opening triggers the job pipeline),
  `GET|PATCH /jobs/{id}/whatsapp-questions`,
  `POST /jobs/generate-description` (**AI JD Builder** — drafts a **bilingual**
  JD from the Role-Basics fields via Claude Sonnet: returns a native English
  (`job_description`) **and** native Gulf-Arabic (`job_description_ar`) draft —
  the Arabic is written natively, not machine-translated — via tool-forced
  structured output (`JdDraft`). GCC-anchored prompt (Emaar/STC/ADNOC register,
  active verbs, no filler, salary only if provided). **Synchronous & stateless**,
  no job row is created; the create-job wizard calls it on the JD Builder step
  before the job exists, so unlike WhatsApp-question generation it runs
  in-request, not in the Celery pipeline. On create, both drafts persist to the
  job (`jobs.job_description` + nullable `jobs.job_description_ar`).
  `?regenerate=true` bypasses the Claude cache. Lives in
  `src/ai/job_description_generator.py`)
- **applications** — `GET /jobs/{job_id}/applications` (list),
  `POST /jobs/{job_id}/applications/bulk-upload` (multipart, fail-soft per file),
  `GET /applications/{id}`, `GET /applications/{id}/audit-log`,
  `PATCH /applications/{id}/stage` (entering `hard_filter` enqueues scoring),
  `PATCH /applications/{id}/status`,
  `PATCH /applications/{id}/candidate` (edit the candidate's name/email/phone;
  allowed only while the app is in `vector_screen`/`hard_filter` — locked from
  `whatsapp` on, since screening owns the contact channel. The candidate row is
  shared, so the edit propagates to all their applications; 409 on an identity
  collision, 422 `candidate_contact_locked` past the window),
  `POST /applications/{id}/rescore?type=…`,
  `POST /applications/{id}/move-to-pool` (move candidate pipeline→pool:
  **soft-archives** the application — `status=archived`, retaining its scores +
  WhatsApp transcript as cross-job history — and (re-)adds the candidate to the
  pool; only this job is affected. The candidate can be sourced back onto the
  same job for a fresh stint, the archived one surviving as history)
- **`/talent-pool`** — `POST /entries` (add candidate), `POST /upload`
  (CV→pool), `GET /search` (two modes; one of `q`/`job_id` required, `job_id`
  wins): **`q` is lexical** — substring-match the phrase against name + parsed
  role titles + skills (so "software engineer" returns the software engineers,
  not a noisy short-query embedding ranking; hits have `similarity_score=None`,
  newest-first). **`job_id` is semantic** — rank the pool by cosine vs the job's
  stored JD embedding ("candidates relevant to this job", no extra embed call),
  gated to the relevant ones (`TALENT_POOL_SEARCH_MIN_SIMILARITY` = 50, a
  stricter bar than the CV pipeline's ingestion auto-reject floor of 40);
  `409 job_not_embedded` if the job has no JD vector yet.
  `GET ""` (list). Both list + search items carry `source_job_title` and an
  enriched candidate snapshot (`role` from the parsed work history, top `skills`,
  candidate-level `authenticity_score`/`_band`).
  `GET /candidates/{id}/history` (the candidate's full cross-job journey: one
  scored stint per application they ever had — live, rejected, accepted, or
  archived — each with its similarity/hard-filter scores + WhatsApp screening
  digest; candidate-level authenticity surfaced once), `POST /source`
  (source a pooled candidate onto a job — a *move*: creates a fresh L1
  application flagged `sourced_from_talent_pool` **and deactivates the pool
  entry** so the candidate lives only in the pipeline afterward)
- **`/public/apply`** — `POST /public/apply/{slug}/upload` (anonymous CV upload
  with consent; enqueues the CV pipeline)
- **`/events`** — `POST /events/ticket` (HR-authed; mints a single-use SSE
  ticket), `GET /events/stream?ticket=…` (ticket-authed; the shared realtime
  stream). Both 404 when `sse_enabled=False`.

Pydantic response schemas use `ConfigDict(from_attributes=True)` and are built
with `.model_validate(orm_row)`.
