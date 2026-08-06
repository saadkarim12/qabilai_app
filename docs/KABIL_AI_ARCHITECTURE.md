# Kabil.ai — Architecture & Implementation Plan

> AI-driven hiring automation platform. This document is the source of truth for the backend architecture, database design, technology choices, and step-by-step implementation plan. Hand individual steps to Claude Code one at a time.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Database ERD](#2-database-erd)
3. [Technology Stack](#3-technology-stack)
4. [Where AI is Called](#4-where-ai-is-called)
5. [Implementation Steps](#5-implementation-steps)
6. [Cross-Cutting Concerns](#6-cross-cutting-concerns)

---

## 1. System Architecture

### High-Level Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                          CLIENTS                                      │
├──────────────────────────────────────┬───────────────────────────────┤
│  Next.js Frontend                    │  Candidate                     │
│  (HR Dashboard + Public Upload)      │  (WhatsApp / Email)            │
└─────────────────┬────────────────────┴──────────────┬─────────────────┘
                  │ HTTPS                              │ Webhooks (HTTPS)
                  ▼                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend (Azure Container Apps)             │
│  ┌────────────────┐  ┌──────────────────┐  ┌────────────────────┐   │
│  │ REST Routes    │  │ Webhook Handlers │  │ Auth Middleware     │   │
│  │ (HR Dashboard) │  │ (WhatsApp)       │  │ (JWT)               │   │
│  └────────┬───────┘  └────────┬─────────┘  └─────────────────────┘   │
│           │                    │                                       │
│  ┌────────▼────────────────────▼─────────────────────────────────┐    │
│  │              Service Layer (Business Logic)                   │    │
│  └────────┬─────────────────────┬───────────────────┬────────────┘    │
└───────────┼─────────────────────┼───────────────────┼─────────────────┘
            │                     │                   │
            ▼                     ▼                   ▼
   ┌────────────────┐    ┌──────────────────┐  ┌──────────────────┐
   │ LangGraph      │    │ Celery Workers   │  │ Direct Async     │
   │ Agents         │    │ (2 queues)       │  │ Calls            │
   │ - WhatsApp     │    │ - default        │  │ (fast path)      │
   │ - Scheduling   │    │ - slow (LLM)     │  │                  │
   └────────┬───────┘    └────────┬─────────┘  └────────┬─────────┘
            │                     │                     │
            └─────────────────────┴─────────────────────┘
                                  │
            ┌─────────────────────┼──────────────────────────┐
            ▼                     ▼                          ▼
   ┌──────────────────┐  ┌──────────────────┐    ┌────────────────────┐
   │ Postgres +       │  │ Redis            │    │ Azure Blob Storage │
   │ pgvector         │  │ (Cache + Broker) │    │ (CV PDFs)          │
   │ - relational     │  │                  │    │                    │
   │ - JD/CV vectors  │  │                  │    │                    │
   └──────────────────┘  └──────────────────┘    └────────────────────┘

   ┌────────────────────────────────────────────────────────────────┐
   │                    EXTERNAL SERVICES                            │
   ├────────────────┬──────────────┬──────────────┬─────────────────┤
   │ OpenAI         │ Claude API   │ Meta         │ Google APIs     │
   │ (Embeddings)   │ (Sonnet +    │ WhatsApp     │ (Meet,          │
   │                │  Haiku)      │ Business     │  Calendar,      │
   │                │              │ API          │  SMTP-relay)    │
   └────────────────┴──────────────┴──────────────┴─────────────────┘
```

### Component Responsibilities

| Component | Responsibility |
|-----------|----------------|
| **Next.js Frontend** | HR dashboard, public CV upload page, no business logic |
| **FastAPI Backend** | REST API, webhook handlers, auth, orchestration |
| **Service Layer** | All business logic — pure Python, no FastAPI imports |
| **LangGraph Agents** | Stateful multi-step flows (WhatsApp Q&A, interview scheduling) |
| **Celery Workers** | Background jobs (CV parsing, scoring, embeddings, messaging) |
| **Postgres + pgvector** | Single source of truth — relational data + vector embeddings |
| **Redis** | Celery broker, response cache, session storage |
| **Azure Blob** | Raw CV PDF storage (private bucket, signed URLs) |

### Why Two Celery Queues?

- **`default` queue** — fast tasks (text extraction, message sends, DB writes). Many workers, low timeout.
- **`slow` queue** — LLM-heavy tasks (Claude calls, embeddings). Fewer workers, longer timeout. Prevents a Claude API slowdown from blocking time-sensitive sends.

---

## 2. Database ERD

### Tables Overview

**Core entities:**
- `users` — HR user(s)
- `jobs` — job postings
- `candidates` — people (deduped by email + phone)
- `cv_documents` — CV file + extracted text + embedding (1:1 with latest candidate CV)
- `applications` — candidate × job join with stage/status

**Process tables:**
- `application_scores` — append-only score history
- `whatsapp_sessions` — one per screening attempt
- `interview_slots` — slots offered + selection state
- `talent_pool_entries` — accepted candidates after job close

**Supporting tables:**
- `audit_logs` — every HR action
- `sessions` — auth tokens
- `public_upload_tokens` — slug → job mapping for `/apply/{slug}` URLs
- `consent_records` — GDPR/PDPL consent trail

### Detailed Schema

#### `users`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| email | varchar UNIQUE | login identifier |
| password_hash | varchar | bcrypt |
| full_name | varchar | |
| role | varchar | 'admin' / 'hiring_manager' (single role for v1) |
| created_at | timestamptz | |
| last_login_at | timestamptz | nullable |

#### `jobs`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| created_by | UUID FK users | |
| title | varchar | |
| hiring_company | varchar | |
| country | varchar(2) | ISO code |
| city | varchar | |
| employment_type | varchar | enum: permanent/contract/temporary |
| work_mode | varchar | enum: onsite/hybrid/remote |
| currency | varchar(3) | ISO 4217 |
| min_salary | int | nullable |
| max_salary | int | nullable |
| notice_period | varchar | enum: any/immediate/30d/60d/90d, nullable |
| min_experience_years | int | hard filter |
| required_skills | jsonb | `["Python", "Django"]` |
| preferred_skills | jsonb | `["AWS"]` |
| visa_requirement | varchar | enum, nullable |
| nationality_preference | jsonb | `[]` = any |
| languages_required | jsonb | `["english", "arabic"]` |
| ai_verify_flags | jsonb | `{salary: true, notice: false, ...}` |
| job_description | text | long text, source for embedding |
| jd_embedding | vector(1536) | pgvector, HNSW index, cosine ops |
| whatsapp_questions | jsonb | AI-generated, HR-editable, see schema below |
| status | varchar | enum: draft/open/inactive/archived/closed (closed = legacy) |
| public_slug | varchar UNIQUE | for /apply/{slug} URL |
| closed_at | timestamptz | nullable |
| created_at | timestamptz | |
| updated_at | timestamptz | |

`whatsapp_questions` JSONB shape:
```json
[
  {
    "id": "q_abc123",
    "order": 1,
    "category": "background_validation",
    "subcategory": "Experience",
    "question_en": "...",
    "question_ar": "...",
    "reasoning": "Validates X...",
    "is_ai_generated": true,
    "source_field": null,
    "ai_verifies_response": true
  }
]
```

#### `candidates`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| email | varchar | not unique — fuzzy match with phone |
| phone_e164 | varchar | E.164 normalized |
| full_name | varchar | |
| parsed_profile | jsonb | structured CV: skills, experience, education |
| authenticity_score | float | 0-100 weighted total |
| authenticity_band | varchar | enum: authentic/review/fabricated |
| authenticity_breakdown | jsonb | per-signal scores + reasoning + `computed_by` |
| authenticity_computed_at | timestamptz | |
| created_at | timestamptz | |
| updated_at | timestamptz | |

Composite UNIQUE index on `(LOWER(email), phone_e164)` for dedup.

#### `cv_documents`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| candidate_id | UUID FK candidates | |
| blob_url | varchar | Azure Blob path |
| blob_sha256 | varchar | for dedup on re-upload |
| extracted_text | text | pypdf output |
| cv_embedding | vector(1536) | pgvector, HNSW index |
| language | varchar | 'en' / 'ar' / 'mixed' |
| is_current | bool | only one true per candidate |
| uploaded_at | timestamptz | |

#### `applications`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| job_id | UUID FK jobs | |
| candidate_id | UUID FK candidates | |
| stage | varchar | enum: vector_screen/hard_filter/whatsapp/interview/done |
| status | varchar | enum: active/rejected/accepted/no_response |
| similarity_score | float | step 1 result, denormalized for fast lists |
| hard_filter_score | float | step 2 result, denormalized |
| hard_filter_breakdown | jsonb | per-signal scores |
| stage_updated_at | timestamptz | |
| created_at | timestamptz | |

UNIQUE on `(job_id, candidate_id)` — same candidate can't apply to same job twice.

#### `application_scores` (append-only audit)
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| application_id | UUID FK applications | |
| score_type | varchar | 'similarity' / 'hard_filter' / 'authenticity' |
| value | float | |
| breakdown | jsonb | full per-signal data |
| prompt_version | varchar | for prompt versioning |
| model_used | varchar | 'claude-sonnet' / 'claude-haiku' / 'deterministic' |
| computed_at | timestamptz | |

#### `whatsapp_sessions`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| application_id | UUID FK applications | |
| status | varchar | enum: sent/awaiting_reply/parsed/no_response/failed |
| questions_sent | jsonb | snapshot of questions used |
| message_id_meta | varchar | for matching webhook callbacks |
| raw_reply | text | nullable |
| parsed_reply | jsonb | `[{question_id, answer_text, confidence}]` |
| langgraph_checkpoint_id | varchar | LangGraph state ref |
| sent_at | timestamptz | |
| replied_at | timestamptz | nullable |
| timeout_at | timestamptz | sent_at + 24h |

#### `interview_slots`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| application_id | UUID FK applications | |
| slot_start | timestamptz | |
| duration_minutes | int | default 30 |
| status | varchar | enum: offered/selected/confirmed/expired/cancelled |
| meet_link | varchar | populated after confirmation |
| google_event_id | varchar | nullable, for cancellation |
| created_at | timestamptz | |

#### `talent_pool_entries`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| candidate_id | UUID FK candidates | UNIQUE |
| source_job_id | UUID FK jobs | nullable (direct upload) |
| added_by | UUID FK users | |
| added_at | timestamptz | |
| expires_at | timestamptz | added_at + 1 year |
| is_active | bool | |

#### `audit_logs`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK users | nullable for system actions |
| entity_type | varchar | 'application' / 'job' / 'candidate' / etc. |
| entity_id | UUID | |
| action | varchar | 'stage_changed' / 'manually_accepted' / etc. |
| before_state | jsonb | nullable |
| after_state | jsonb | |
| ip_address | varchar | nullable |
| created_at | timestamptz | |

Index on `(entity_type, entity_id, created_at DESC)`.

#### `sessions`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK users | |
| token_hash | varchar UNIQUE | hashed JWT |
| ip_address | varchar | |
| expires_at | timestamptz | |
| revoked_at | timestamptz | nullable |

#### `public_upload_tokens`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| job_id | UUID FK jobs | |
| slug | varchar UNIQUE | short random string |
| is_active | bool | |
| created_at | timestamptz | |

#### `consent_records`
| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| candidate_id | UUID FK candidates | |
| consent_type | varchar | 'data_processing' / 'talent_pool' |
| granted | bool | |
| ip_address | varchar | |
| user_agent | varchar | |
| granted_at | timestamptz | |

### Relationships Summary

```
users 1──┬──N jobs
         └──N audit_logs
         └──N sessions

jobs 1──┬──N applications
        └──N public_upload_tokens
        └──N talent_pool_entries (as source)

candidates 1──┬──N cv_documents (only 1 is_current=true)
              └──N applications
              └──1 talent_pool_entries
              └──N consent_records

applications 1──┬──N application_scores
                └──N whatsapp_sessions
                └──N interview_slots
                └──N audit_logs
```

---

## 3. Technology Stack

### Backend
| Layer | Technology | Reason |
|---|---|---|
| Language | Python 3.12 | LangGraph + LangChain ecosystem |
| Web framework | FastAPI | Async-native, OpenAPI generation |
| ORM | SQLAlchemy 2.0 (async) | Mature, async support |
| DB driver | asyncpg | Fastest async Postgres driver |
| Migrations | Alembic | Standard with SQLAlchemy |
| Validation | Pydantic v2 | FastAPI-native |
| Auth | python-jose + bcrypt | JWT + password hashing |
| Task queue | Celery 5 | Battle-tested, two-queue setup |
| Broker | Redis | Celery broker + response cache |
| HTTP client | httpx (async) | For external API calls |

### AI / ML
| Layer | Technology | Reason |
|---|---|---|
| Embeddings | OpenAI `text-embedding-3-small` | 1536-dim, multilingual, cheap |
| Reasoning LLM | Anthropic Claude Sonnet | Hard filter scoring, question generation |
| Lightweight LLM | Anthropic Claude Haiku | CV parsing, authenticity, reply parsing |
| Agent framework | LangGraph | Stateful WhatsApp + scheduling agents |
| Retrieval | LangChain PGVector only | Vector search; nothing else from LangChain |
| Prompt templating | Jinja2 | Plain Python, easy to test |

### Infrastructure (Azure)
| Layer | Technology | Reason |
|---|---|---|
| Compute | Azure Container Apps | Managed containers, scale-to-zero |
| Database | Azure Database for PostgreSQL Flexible Server | pgvector supported |
| Cache | Azure Cache for Redis | Managed Redis |
| Object storage | Azure Blob Storage | CV PDFs |
| Secrets | Azure Key Vault | API keys, DB credentials |
| Logs | Azure Monitor + App Insights | Telemetry |
| Container registry | Azure Container Registry | Docker images |

### Document Processing
| Layer | Technology | Reason |
|---|---|---|
| PDF text extract | pypdf | Lightweight, handles English + Arabic |
| Phone normalization | phonenumbers | E.164 + fuzzy match |
| Language detection | langdetect | EN vs AR auto-detect |

### External Integrations
| Service | Purpose |
|---|---|
| Meta WhatsApp Business API | Candidate messaging (templates + free-form) |
| Google Calendar API | Meet link creation + ICS generation |
| Azure Communication Services SMTP (or SendGrid) | Email confirmations |

### Frontend (out of scope here, for reference)
- Next.js 14+ (App Router)
- TanStack Query (server state)
- Shadcn UI / Radix
- Zustand or Jotai (light client state)

---

## 4. Where AI is Called

| # | When | Tool | Model | Why |
|---|---|---|---|---|
| 1 | Job open | Direct Claude SDK | Haiku | Generate ≤3 `background_validation` WhatsApp questions (required + preferred skills only — no work history); the 6 fixed canonical questions are deterministic, not AI |
| 2 | Job creation | OpenAI Embeddings | `text-embedding-3-small` | Embed JD (cached) |
| 3 | CV upload | Direct Claude SDK | Haiku | Parse CV → structured JSON |
| 4 | CV upload | Direct Claude SDK | Haiku | Authenticity scoring (3 of 5 signals) |
| 5 | CV upload | Pure Python | — | Authenticity scoring (2 deterministic signals: timeline coherence, linguistic genericity, structural templating) |
| 6 | CV upload | OpenAI Embeddings | `text-embedding-3-small` | Embed CV (cached) |
| 7 | Step 1 scoring | LangChain PGVector retriever | — | Cosine similarity, no LLM call |
| 8 | Step 1→2 transition | Direct Claude SDK | Sonnet | Hard filter scoring (1 call, all 5 signals) |
| 9 | Step 3 send | Pure Python | — | Send pre-approved Meta template |
| 10 | Step 3 reply | LangGraph node → Claude SDK | Haiku | Parse free-text WhatsApp reply |
| 11 | Step 4 scheduling | LangGraph agent | — | State machine: send slots → wait → confirm |
| 12 | Talent pool search | LangChain PGVector retriever | — | Semantic search query → CV vectors |

### Authenticity Signal Computation

| Signal | Method | Weight |
|---|---|---|
| Achievement Specificity | Claude Haiku | 25% (redistributed) |
| Skill Timeline Coherence | Python regex/string match | 20% (redistributed) |
| Internal Consistency | Claude Haiku | 20% (redistributed) |
| Linguistic Genericity | Python word-list density | 15% (redistributed) |
| Structural Templating | Python pattern detection | 10% (redistributed) |
| ~~JD Keyword Mirroring~~ | DROPPED for v1 | ~~10%~~ |

After dropping #6, weights renormalized to sum to 100%:
- Achievement Specificity: 27.8% (Claude)
- Timeline Coherence: 22.2% (Python)
- Internal Consistency: 22.2% (Claude)
- Linguistic Genericity: 16.7% (Python)
- Structural Templating: 11.1% (Python)

**One Claude Haiku call** handles signals 1 and 3 in a single prompt with CoT instruction.

### Hard Filter Scoring Strategy

**One Claude Sonnet call** scores all 5 signals (Skills 35%, Experience 35%, Industry 10%, Regional 10%, Role/Seniority 10%) with chain-of-thought reasoning. Output is structured JSON with per-signal score + reasoning. Weighted total is computed in Python.

---

## 5. Implementation Steps

Hand these to Claude Code **one step at a time**. Each step ends with verifiable acceptance criteria. Do not start step N+1 until step N's acceptance criteria are met and committed.

> **Convention for each step:**
> - **Goal** — what we're building
> - **Files** — what gets created/modified
> - **Caching** — what to cache, where, for how long
> - **Security** — what checks to add
> - **Acceptance** — testable criteria to mark step done

---

### Phase 0: Foundation

#### Step 0.1 — Project Skeleton

**Goal:** Working FastAPI app with health check, dockerized, dependencies installed.

**Files:**
- `pyproject.toml` with all deps (FastAPI, SQLAlchemy 2, asyncpg, Pydantic v2, Celery, redis, httpx, jinja2, python-jose, bcrypt, pypdf, phonenumbers, langdetect, langchain-postgres, langgraph, anthropic, openai, alembic, pytest, pytest-asyncio, ruff)
- `Dockerfile` (multi-stage, Python 3.12-slim)
- `docker-compose.yml` for local dev: postgres-16 with pgvector, redis-7
- `src/main.py` — FastAPI app with `/health` endpoint
- `src/config.py` — Pydantic Settings reading env vars
- `.env.example` listing all required env vars
- `Makefile` with `dev`, `test`, `lint`, `migrate` targets

**Caching:** N/A

**Security:**
- `.env` in `.gitignore`
- All secrets read from env (never hardcoded)
- CORS middleware configured but restrictive by default

**Acceptance:**
- `docker compose up` brings up postgres, redis, api
- `curl localhost:8000/health` returns `{"status":"ok","version":"x.y.z"}`
- `ruff check src/` passes
- `pytest` runs (even with zero tests)

---

#### Step 0.2 — Database & Migrations Setup

**Goal:** Alembic configured, pgvector extension enabled, async SQLAlchemy session factory.

**Files:**
- `alembic.ini`, `alembic/env.py` (async-aware)
- `alembic/versions/0001_enable_pgvector.py` — `CREATE EXTENSION IF NOT EXISTS vector;`
- `src/db/base.py` — `Base = DeclarativeBase()`
- `src/db/session.py` — async engine, session factory, `get_db()` dep
- `src/db/types.py` — pgvector column type wrapper

**Caching:** N/A

**Security:**
- DB connection pool sized for container (default 10, configurable)
- Connection requires SSL in production (`sslmode=require`)
- DB user has only DML + DDL on app schema (not superuser)

**Acceptance:**
- `alembic upgrade head` runs cleanly
- `psql` connection shows `vector` extension installed
- A throwaway script can `INSERT` and `SELECT` from a vector column

---

#### Step 0.3 — Settings, Logging, Error Handling

**Goal:** Production-grade config, structured logging, global exception handlers.

**Files:**
- `src/config.py` — full Settings class with validation
- `src/logging_config.py` — structured JSON logs in prod, pretty in dev
- `src/api/exception_handlers.py` — global handlers for `HTTPException`, validation errors, unhandled exceptions
- `src/utils/correlation_id.py` — middleware that adds `X-Correlation-ID` to all logs and responses

**Caching:** N/A

**Security:**
- All exceptions logged with correlation ID
- Stack traces NEVER returned to client in production
- 500 errors return generic message + correlation ID for support
- Rate-limit-style errors return 429 (placeholder, real rate limiting comes later)

**Acceptance:**
- Logs include correlation ID, timestamp, log level, message
- An intentional 500 in dev returns sanitized response in prod mode
- Validation errors return clean 422 with field details

---

### Phase 1: Auth & Users

#### Step 1.1 — User Model & Migration

**Goal:** `users` table created.

**Files:**
- `src/db/models/user.py` — SQLAlchemy model
- `alembic/versions/0002_users.py`
- `src/enums/roles.py` — `UserRole` enum

**Caching:** N/A

**Security:**
- `password_hash` column never logged or returned
- Email stored lowercased on write

**Acceptance:**
- Migration runs
- A raw INSERT + SELECT works
- Model has `__repr__` that excludes password_hash

---

#### Step 1.2 — Sessions Table & Password Hashing

**Goal:** Bcrypt password hashing utility + `sessions` table.

**Files:**
- `src/db/models/session.py`
- `alembic/versions/0003_sessions.py`
- `src/utils/password.py` — `hash_password()`, `verify_password()` (bcrypt cost 12)
- `src/utils/jwt.py` — sign/verify JWT (HS256, 8h default expiry)

**Caching:** N/A

**Security:**
- JWT secret minimum 32 bytes, from env
- Bcrypt cost 12 (≈250ms per hash)
- Constant-time password comparison
- JWT carries only `user_id` + `session_id` + `exp` — no roles/permissions baked in

**Acceptance:**
- Unit tests: hash/verify roundtrip, JWT sign/verify, expired token rejected
- Hashing a password twice yields different hashes (salt works)

---

#### Step 1.3 — Auth Routes

**Goal:** `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`.

**Files:**
- `src/api/routes/auth.py`
- `src/schemas/auth.py` — Pydantic in/out models
- `src/services/auth_service.py`
- `src/api/deps.py` — `get_current_user()` dependency
- `tests/integration/test_auth.py`

**Caching:**
- Cache `user_id → User` lookup in Redis for 5 minutes after auth (skip on auth-related routes)

**Security:**
- Login: rate-limit attempts in-memory per IP (3 attempts → 30s lockout) — placeholder, IP can be spoofed via headers, real rate limiting comes via Azure Front Door
- Failed login returns generic "invalid credentials" (no leak of which field is wrong)
- Logout revokes session (sets `revoked_at`)
- `/auth/me` requires valid JWT + active session
- Session revocation check runs on every authenticated request

**Acceptance:**
- Integration tests: happy path login → /me → logout → /me 401
- Invalid password returns 401 (not 403 or 422)
- Revoked session → 401 even with valid JWT

---

#### Step 1.4 — Seed Script

**Goal:** Bootstrap script to create the single HR admin user.

**Files:**
- `scripts/seed_admin.py` — interactive prompt for email + password
- Documented in README

**Caching:** N/A

**Security:**
- Won't run if any user already exists (idempotent)
- Password input masked
- Minimum password length enforced (12 chars)

**Acceptance:**
- Running once creates admin
- Running second time refuses politely
- Admin can log in via the API

---

### Phase 2: Jobs

#### Step 2.1 — Job Model & Enums

**Goal:** All job-related enums and the `jobs` table.

**Files:**
- `src/enums/jobs.py` — `JobStatus`, `EmploymentType`, `WorkMode`, `NoticePeriod`, `VisaRequirement`
- `src/enums/hard_filters.py` — `HardFilterKey` + `HARD_FILTER_WEIGHTS` + `HARD_FILTER_RUBRICS`
- `src/enums/whatsapp_questions.py` — `QuestionCategory`
- `src/enums/thresholds.py` — `SIMILARITY_REJECTION_THRESHOLD = 0.4`, `AUTHENTICITY_BAND_THRESHOLDS`, structural templating thresholds
- `src/db/models/job.py` (with vector column for jd_embedding)
- `alembic/versions/0004_jobs.py` — includes HNSW index on `jd_embedding`
- `src/db/models/public_upload_token.py`
- `alembic/versions/0005_public_upload_tokens.py`

**Caching:** N/A

**Security:**
- `public_slug` minimum 16 chars, generated via `secrets.token_urlsafe`
- Slug never derived from job title (no enumeration)

**Acceptance:**
- Migrations apply
- HNSW index visible via `\d+ jobs` in psql
- All enum values documented in docstrings

---

#### Step 2.2 — JD Embedding Service

**Goal:** Reusable utility to embed text via OpenAI.

**Files:**
- `src/ai/embeddings.py` — async function `embed_text(text: str) -> list[float]`
- Configurable model from env (`text-embedding-3-small` default)
- Returns 1536-dim vector
- Built-in retry (3x exp backoff) on transient errors
- `tests/unit/test_embeddings.py` with mocked OpenAI

**Caching:**
- Cache embedding by SHA-256 of input text in Redis for 30 days
- Key: `embedding:v1:sha256:{hash}`
- Reduces cost when JD/CV text is identical (e.g., re-upload of same CV)

**Security:**
- OpenAI API key from env / Key Vault
- Truncate input to max 8000 tokens before sending (prevents accidental cost spikes)
- Never log embedding inputs (PII)

**Acceptance:**
- Unit test mocks OpenAI and verifies embed_text returns 1536-dim list
- Cache hit returns same vector without OpenAI call
- 8001-token input is truncated, not erroring

---

#### Step 2.3 — Claude SDK Wrapper

**Goal:** Single wrapper around Anthropic SDK for all Claude calls.

**Files:**
- `src/ai/claude.py` — `call_claude(model, system, messages, max_tokens, response_format='json'|'text')`
- `src/ai/prompts/` — jinja templates directory
- Built-in retry on `RateLimitError` and `OverloadedError`
- Optional `prompt_version` parameter logged with every call

**Caching:**
- Cache Claude response by SHA-256(model + system + messages) in Redis for 7 days
- Configurable per-call: `cache=True/False`
- Off by default for chat-like calls, ON for deterministic scoring

**Security:**
- Anthropic API key from env / Key Vault
- Token usage logged per call for cost monitoring
- Refuse to send messages > N tokens (configurable cap)
- Strip PII from logs (email, phone via regex)

**Acceptance:**
- Mocked unit test verifies retries on 429
- Cache hit short-circuits the SDK call
- Response with `response_format='json'` returns parsed dict, raises on invalid JSON

---

#### Step 2.4 — WhatsApp Question Generation Prompt

**Goal:** Compose the bilingual question list = a **fixed canonical set of 6
deterministic questions** + up to 3 **AI-authored** `background_validation`
questions. HR no longer selects which fields to ask about; the fixed set is the
same for every job. HR can append **custom** questions afterward via
`PATCH /jobs/{id}/whatsapp-questions`.

The final list is assembled server-side when the job opens:
1. The **6 fixed questions**, drawn from `src.enums.fixed_screening_questions`
   (`{placeholder}` tokens filled from the Job's own fields). All are bilingual
   (`question_en` + `question_ar`), carry `is_ai_generated=false`,
   `ai_verifies_response=false`, and a `source_field` key:
   | Topic | category | source_field |
   |---|---|---|
   | reason for considering a new opportunity | `commitment` | `commitment` |
   | salary expectation (current + expected, in `{currency}`) | `salary` | `salary` |
   | notice period in days | `commitment` | `notice_period` |
   | visa/residency status in `{city}` | `background_validation` | `visa` |
   | employment type (`{employment_type}`) | `commitment` | `employment_type` |
   | work mode (`{work_mode}` in `{city}`) | `commitment` | `work_mode` |
2. Up to **3** Claude-authored `background_validation` questions
   (`is_ai_generated=true`, `source_field=null`, `ai_verifies_response=true`)
   that verify the candidate's hands-on experience with the role's listed
   **required + preferred skills** — skills only (no work history), each
   targeting a specific named skill and phrased so answers can be cross-checked
   against the CV. The AI must **not** ask about work history or duplicate the
   fixed topics (salary, visa, notice period, commitment, employment type, work
   mode).
3. Custom questions HR adds later are `is_ai_generated=false`,
   `source_field=null`, `ai_verifies_response=false`.
4. Fixed questions (canonical order) then AI questions; 1-based `order` assigned.

`ai_verifies_response` **gates answer scoring**: only questions with
`ai_verifies_response=true` (in practice the AI `background_validation`
questions) have the candidate's WhatsApp reply scored by Claude. Fixed and
custom answers are stored verbatim, never AI-scored.

Categories are limited to exactly three: `commitment`, `salary`,
`background_validation` (`src/enums/whatsapp_questions.py`); the specific topic
lives in `subcategory`.

**Files:**
- `src/enums/fixed_screening_questions.py` — the 6 canonical bilingual
  templates + their categories/`source_field`s + canonical order
- `src/ai/prompts/generate_whatsapp_questions.j2` — Jinja prompt (AI questions)
- `src/ai/whatsapp_question_generator.py` — service function (merges fixed + AI;
  `prompt_version` `generate_whatsapp_questions:base:v6` / `:strict:v6`)
- `src/schemas/whatsapp_question.py` — Pydantic models
- `tests/unit/test_whatsapp_question_generator.py`

**Caching:**
- Use Claude wrapper's cache (7-day), keyed by prompt + `prompt_version`. The
  `:v6` suffix bumps the cache when the prompt wording changes.

**Security:**
- Validate output JSON against Pydantic schema; reject malformed
- Cap total questions at 15 (6 fixed + ≤3 AI + custom; AI hard-capped at
  `WHATSAPP_MAX_AI_QUESTIONS=3`)
- No PII expected in inputs (JD + JD-derived fields only), but log redacted

**Acceptance:**
- Every job yields the same 6 fixed questions, placeholders filled from its
  own fields
- AI output is ≤3 questions, all `background_validation`,
  `ai_verifies_response=true`, naming the role's required skills, never
  duplicating a fixed topic
- Output validates against `list[WhatsAppQuestion]`
- Arabic version is non-empty for every question

---

#### Step 2.5 — Job CRUD Routes

**Goal:** Create/read/update/list jobs.

**Files:**
- `src/api/routes/jobs.py`
- `src/services/job_service.py`
- `src/schemas/job.py`
- `src/utils/slug.py` — generate unique slug
- `tests/integration/test_jobs.py`

**Endpoints:**
- `POST /jobs` — creates job with status='draft', returns 202 + job_id
- `GET /jobs` — list with filters (status, search, pagination)
- `GET /jobs/{id}` — full detail + funnel counts
- `PATCH /jobs/{id}/status` — open / closed (closing triggers talent pool sweep, deferred to phase 5)
- `GET /jobs/{id}/whatsapp-questions`
- `PATCH /jobs/{id}/whatsapp-questions` — HR edits

**Caching:**
- Cache `GET /jobs/{id}` response in Redis for 60s, key `job:detail:{id}:v{updated_at_epoch}`
- Invalidate by version bump on `UPDATE`

**Security:**
- All routes require HR auth except where noted
- Validate `country` is real ISO code
- `min_salary <= max_salary` if both set
- Sanitize `job_description` (strip script tags, no rendering HTML)
- Audit log every status change

**Acceptance:**
- Integration tests: create job → fetch → list → update questions
- A draft job cannot be applied to (returns 404 on public URL)
- Opening a draft job triggers JD embedding + question generation (next step)

---

#### Step 2.6 — Job Creation Background Pipeline

**Goal:** When job moves to status='open', enqueue Celery tasks for embedding + question gen.

**Files:**
- `src/workers/celery_app.py` — Celery setup, two queues
- `src/workers/tasks/job_pipeline.py`
  - `embed_jd_task(job_id)` (slow queue)
  - `generate_questions_task(job_id)` (slow queue)
- `src/services/job_service.py` updates: on open, enqueue both tasks
- `tests/integration/test_job_pipeline.py` (with Celery in eager mode)

**Caching:**
- Embedding & questions both cached via underlying utilities — re-running is idempotent
- After both tasks succeed, mark job `ready_for_applications=true` (additional bool column added in this step's migration)

**Security:**
- Tasks idempotent — safe to retry
- On failure, job status remains 'open' but `pipeline_status` field shows error
- Errors visible to HR via `GET /jobs/{id}`

**Acceptance:**
- Creating + opening a job triggers two Celery tasks
- After tasks complete, `jd_embedding` is populated and `whatsapp_questions` is filled
- Test verifies retry on transient OpenAI failure

---

### Phase 3: Candidates & CV Pipeline

#### Step 3.1 — Candidate & CV Document Models

**Goal:** Tables + dedup logic.

**Files:**
- `src/db/models/candidate.py`
- `src/db/models/cv_document.py`
- `alembic/versions/0006_candidates.py` (with composite unique index)
- `alembic/versions/0007_cv_documents.py` (with HNSW index)
- `src/utils/phone.py` — normalize to E.164, fuzzy compare

**Caching:** N/A

**Security:**
- Email stored lowercased
- Phone normalized to E.164 on write
- Composite unique enforced at DB level

**Acceptance:**
- Migrations apply
- Duplicate insert with same email + phone raises IntegrityError
- Phone normalization tested for UAE, Pakistan, India formats

---

#### Step 3.2 — Azure Blob Storage Integration

**Goal:** Upload/download CVs to Azure Blob.

**Files:**
- `src/integrations/azure_blob.py` — async client wrapper
- Functions: `upload_pdf()`, `get_signed_url(ttl_minutes)`, `delete_pdf()`
- `tests/unit/test_azure_blob.py` (with mocked client)

**Caching:**
- Cache signed URL by blob path for 80% of its TTL in Redis

**Security:**
- Container is **private** — no public reads
- All access via short-lived signed URLs (default 15 min)
- Validate PDF content (magic bytes) before upload
- Reject files > 10MB
- Path scheme: `cvs/{candidate_id}/{sha256}.pdf` (sha256 enables dedup)

**Acceptance:**
- Upload returns blob URL
- Signed URL works for 15 min, fails after
- Non-PDF file rejected with 400

---

#### Step 3.3 — CV Text Extraction Service

**Goal:** Extract text from PDF, detect language.

**Files:**
- `src/utils/pdf.py` — `extract_text(pdf_bytes) -> str`
- `src/utils/language.py` — `detect_language(text) -> 'en'|'ar'|'mixed'`
- `tests/unit/test_pdf_extract.py` with sample PDFs (EN + AR fixtures)

**Caching:**
- Cache by sha256(pdf_bytes) → extracted_text in Redis for 30 days
- Same PDF re-uploaded skips extraction

**Security:**
- Hard timeout on extraction (10s)
- Catch and log parsing failures gracefully (some PDFs are corrupt)
- Limit extracted text to 50K chars (sanity)

**Acceptance:**
- English CV extracts > 100 chars
- Arabic CV extracts Arabic chars (Unicode range U+0600-U+06FF detected)
- Corrupt PDF returns empty string + logs warning (doesn't crash)

---

#### Step 3.4 — CV Parsing Prompt & Service

**Goal:** Claude Haiku call to extract structured fields from CV text.

**Files:**
- `src/ai/prompts/parse_cv.j2`
- `src/ai/cv_parser.py` — `parse_cv(text, language) -> ParsedCV`
- `src/schemas/parsed_cv.py` — Pydantic model with full_name, email, phone, skills, work_history, education, total_experience_years, languages
- `tests/unit/test_cv_parser.py`

**Caching:**
- Use Claude wrapper cache (7 days) — same CV text = same parse

**Security:**
- Validate output JSON against schema
- Sanitize extracted email (basic regex validation)
- Sanitize extracted phone (must normalize to E.164 or null)
- If schema validation fails, retry once with stricter system prompt; on second failure, store partial parse + flag for HR review

**Acceptance:**
- Sample CV produces non-empty skills array + experience years
- Malformed Claude output triggers retry
- Empty CV produces empty fields, not crash

---

#### Step 3.5 — Authenticity Deterministic Signals

**Goal:** Pure-Python computation for signals #2, #4, #5.

**Files:**
- `src/services/authenticity/deterministic.py`
  - `compute_timeline_coherence(parsed_cv, cv_text) -> SignalResult`
  - `compute_linguistic_genericity(cv_text) -> SignalResult`
  - `compute_structural_templating(cv_text) -> SignalResult`
- `src/enums/authenticity_word_list.py` — LLM-marker phrases (English only for v1, Arabic gets neutral 75 score)
- `tests/unit/test_authenticity_deterministic.py`

**Caching:** N/A (already fast)

**Security:**
- Pure functions, no external calls
- Bounded computation (regex with timeout if needed)

**Acceptance:**
- High-genericity text scores < 50 on linguistic genericity
- CV with all skills in work history scores > 80 on timeline coherence
- AI-templated CV (uniform bullets, em-dashes) scores < 60 on structural templating
- Arabic CV returns neutral 75 for linguistic genericity

---

#### Step 3.6 — Authenticity Claude Signals

**Goal:** One Claude Haiku call for signals #1 and #3.

**Files:**
- `src/ai/prompts/authenticity_judge.j2`
- `src/services/authenticity/llm_judge.py` — `judge_signals(cv_text, parsed_cv) -> {specificity, consistency}`
- Uses Claude wrapper with caching ON

**Caching:**
- 7-day Claude cache (deterministic for same input)

**Security:**
- Validate output JSON strictly
- Cap input size sent to Claude

**Acceptance:**
- Specific achievement-laden CV scores > 80 on specificity
- Vague CV scores < 50
- Inconsistent claims (lead at 1yr exp) score low on consistency

---

#### Step 3.7 — Authenticity Aggregation Service

**Goal:** Combine 5 signals into final score + band.

**Files:**
- `src/services/authenticity/aggregator.py`
- Uses `AUTHENTICITY_WEIGHTS` (renormalized after dropping #6)
- Returns `AuthenticityResult` with score, band, breakdown
- `tests/unit/test_authenticity_aggregator.py`

**Caching:** N/A (computed once per CV upload)

**Security:**
- All values clamped to [0, 100]

**Acceptance:**
- Weights sum to 1.0
- Band assignment matches thresholds (75/50)
- Breakdown JSON includes `computed_by` per signal

---

#### Step 3.8 — Public CV Upload Endpoint

**Goal:** `POST /public/apply/{slug}/upload` — anonymous CV upload.

**Files:**
- `src/api/routes/public.py`
- `src/services/application_intake_service.py`
- `src/schemas/public_apply.py`
- `tests/integration/test_public_apply.py`

**Flow:**
1. Validate slug is active
2. Validate consent checkbox = true
3. Accept multipart form: pdf + email + phone + full_name + consent
4. Run validations (PDF, size, fields)
5. Compute pdf_sha256
6. Find or create candidate (dedup by email+phone)
7. Compare new pdf_sha256 with current cv_document; if same, skip CV ingest, just create application
8. Otherwise: upload to blob, create cv_document (mark old `is_current=false`)
9. Create application with stage='vector_screen', status='active'
10. Enqueue Celery chain: extract_text → parse_cv → (auth_deterministic ∥ auth_claude) → embed_cv → score_similarity
11. Return 202 with reference number

**Caching:**
- Slug → job_id lookup cached in Redis for 5 min
- PDF sha256 dedup uses DB unique check

**Security:**
- Slug active check
- Consent must be true (boolean strictly)
- Max file size enforced
- Magic-byte PDF check
- Email + phone basic format validation
- Hcaptcha / Turnstile placeholder — for v1 just hidden honeypot field; real captcha in phase 2
- No info leak: same response for "duplicate application" as "new application" (just returns ref number)
- Audit log: candidate created, application created, CV uploaded
- Consent record stored with IP + user agent

**Acceptance:**
- Happy path: returns 202 with ref number, Celery chain enqueued
- Duplicate sha256 PDF: doesn't re-upload, doesn't crash
- Inactive slug: returns 410 Gone
- Missing consent: returns 400
- Same candidate applying twice to same job: returns 202 with original ref (idempotent)

---

#### Step 3.9 — CV Processing Celery Chain

**Goal:** Wire all the pieces into a Celery chain.

**Files:**
- `src/workers/tasks/cv_pipeline.py`
  - `extract_text_task(cv_document_id)`
  - `parse_cv_task(cv_document_id)`
  - `compute_authenticity_task(candidate_id)`
  - `embed_cv_task(cv_document_id)`
  - `score_similarity_task(application_id)`
  - `auto_reject_below_threshold_task(application_id)`
- `tests/integration/test_cv_pipeline.py`

**Caching:**
- Each step uses underlying caches (text extract, claude, embedding)

**Security:**
- All tasks idempotent
- Retries: 3x with exp backoff (default), 5x for embedding tasks (transient OpenAI failures)
- Failed task surfaces via `pipeline_status` field on application

**Acceptance:**
- Full chain runs end-to-end on a sample CV
- Application has `similarity_score`, candidate has `authenticity_score`, after pipeline
- Below-threshold app auto-marked `status='rejected'` with stage staying at 'vector_screen'

---

### Phase 4: Applications & Hard Filter Scoring

#### Step 4.1 — Application Model & Listing

**Goal:** `applications` table + list/detail routes.

**Files:**
- `src/db/models/application.py`
- `alembic/versions/0008_applications.py`
- `src/db/models/application_score.py`
- `alembic/versions/0009_application_scores.py`
- `src/api/routes/applications.py`
- `src/schemas/application.py`
- `src/services/application_service.py`

**Endpoints:**
- `GET /jobs/{job_id}/applications?stage=&status=&order=` — paginated
- `GET /applications/{id}` — full detail with scores, sessions, slots
- `GET /applications/{id}/audit-log`

**Caching:**
- List endpoint cached for 15s per (job_id, filter set)
- Invalidate on stage/status change

**Security:**
- HR auth required
- Verify HR has access to that job (always true for single HR v1, but pattern in place)

**Acceptance:**
- Lists filter by stage and status
- Detail returns full nested object
- Audit log returns chronological actions

---

#### Step 4.2 — Stage Transitions & Manual Overrides

**Goal:** `PATCH /applications/{id}/stage` and `/status`.

**Files:**
- `src/api/routes/applications.py` (additions)
- `src/services/application_service.py` (additions)
- `src/enums/stages.py` — allowed transitions

**Stage transitions allowed:**
- `vector_screen → hard_filter` (manual or auto on score)
- `hard_filter → whatsapp` (manual)
- `whatsapp → interview` (manual)
- `interview → done` (manual)
- Any → rejected/accepted (manual, status change)
- HR can move from rejected back to active for the *same stage* (override)

**Caching:**
- Invalidate application detail + list cache on transition

**Security:**
- Validate transition is in allowed list
- Audit log before + after state for every transition
- HR identity recorded

**Acceptance:**
- Invalid transition returns 422 with reason
- Audit log shows transition
- Moving to `hard_filter` triggers hard filter Celery task (next step)

---

#### Step 4.3 — Hard Filter Scoring

**Goal:** Single Claude Sonnet call scoring all 5 signals.

**Files:**
- `src/ai/prompts/hard_filter_score.j2` — comprehensive prompt with all 5 rubrics
- `src/services/scoring/hard_filter_service.py`
- `src/workers/tasks/scoring.py` — `score_hard_filters_task(application_id)`
- `tests/unit/test_hard_filter_score.py`

**Caching:**
- Claude wrapper cache (7 days)

**Security:**
- Validate output: 5 signals present, each 0-100 with reasoning
- Persist to `application_scores` (audit) AND denormalize to `applications.hard_filter_score` + `breakdown`

**Acceptance:**
- Sample CV vs JD produces 5 signal scores
- Weighted total saved
- Breakdown JSON includes reasoning per signal
- Failed Claude output triggers retry, then surfaces error via app status

---

#### Step 4.4 — Manual Re-score Endpoint

**Goal:** `POST /applications/{id}/rescore?type=hard_filter|similarity` for HR.

**Files:**
- `src/api/routes/applications.py` (addition)

**Caching:**
- This bypasses Claude cache via `cache=False` param

**Security:**
- HR auth required
- Audit logged

**Acceptance:**
- Returns 202, task enqueued
- Result visible in detail endpoint after task completes

---

### Phase 5: WhatsApp Integration

#### Step 5.1 — Meta WhatsApp Client

**Goal:** Wrapper around Meta WhatsApp Business API.

**Files:**
- `src/integrations/meta_whatsapp.py`
- Functions: `send_template(to, template_name, language, params)`, `send_interactive_buttons(...)`, `verify_webhook_signature(...)`
- `tests/unit/test_meta_whatsapp.py`

**Caching:** N/A (sending is one-shot)

**Security:**
- API token from env / Key Vault
- Webhook signature verification (HMAC-SHA256 with app secret) MANDATORY
- Token rotation supported (read from env on each call)
- All outbound messages logged with `message_id_meta` for matching

**Acceptance:**
- Send template returns Meta message ID
- Invalid webhook signature is rejected before any processing
- Phone number normalization to E.164

---

#### Step 5.2 — WhatsApp Webhook Endpoints

**Goal:** Receive WhatsApp callbacks.

**Files:**
- `src/api/routes/webhooks.py`
- `src/services/whatsapp_inbound_service.py`
- `tests/integration/test_whatsapp_webhook.py`

**Endpoints:**
- `GET /webhooks/whatsapp` — Meta verification (returns hub.challenge)
- `POST /webhooks/whatsapp` — receives all WhatsApp events

**Caching:** N/A

**Security:**
- Signature verification before any logic
- Reject replay attacks: store webhook event ID, dedup in Redis for 24h
- Log raw payload (sans phone) for debugging
- Per-IP rate limit (placeholder)

**Acceptance:**
- Verification GET returns challenge
- Invalid signature POST returns 401
- Duplicate event ID is deduped

---

#### Step 5.3 — WhatsApp Screening LangGraph Agent

**Goal:** Stateful agent for sending questions + waiting + parsing.

**Files:**
- `src/agents/whatsapp_agent.py` — LangGraph definition
- `src/agents/nodes/whatsapp_nodes.py` — `send_template_node`, `await_reply_node`, `parse_reply_node`, `timeout_node`, `persist_node`
- `src/ai/prompts/parse_whatsapp_reply.j2`
- Checkpoints stored in Postgres (LangGraph Postgres checkpoint adapter)
- `src/workers/tasks/whatsapp.py`
  - `start_whatsapp_screening_task(application_id)` — initiates agent
  - `whatsapp_timeout_task(session_id)` — scheduled for sent_at + 24h
- Inbound webhook handler resumes graph from checkpoint when message arrives

**Caching:**
- Parse-reply Claude call uses 7-day cache (same reply text = same parse)

**Security:**
- Match inbound message to session via `message_id_meta` (not just phone, since multiple sessions per candidate are possible)
- Validate language template approved on Meta before send
- Idempotent: receiving same webhook twice doesn't double-process

**Acceptance:**
- Move application to `whatsapp` stage → template sent → session row created
- Inbound webhook with matching message_id → graph resumes → parse_reply executes → session.parsed_reply populated
- After 24h no reply → status = 'no_response', HR sees in UI

---

#### Step 5.4 — Re-trigger / Resend Endpoints

**Goal:** HR can manually resend WhatsApp questions.

**Files:**
- `src/api/routes/applications.py` (additions)
- `POST /applications/{id}/whatsapp/resend`

**Caching:** N/A

**Security:**
- Per-application limit: max 3 resends total
- 1-hour cooldown between resends
- Audit logged

**Acceptance:**
- First resend succeeds
- Resend within 1h returns 429
- 4th resend returns 422

---

### Phase 6: Interview Scheduling

#### Step 6.1 — Google Calendar Integration

**Goal:** Create Meet links and calendar events.

**Files:**
- `src/integrations/google_calendar.py`
- Uses service account credentials
- Functions: `create_event_with_meet(start, duration, attendees, summary)`, `cancel_event(event_id)`
- `tests/unit/test_google_calendar.py` (mocked)

**Caching:** N/A

**Security:**
- Service account JSON from Key Vault
- Domain-wide delegation scoped to single Calendar user
- All events created on a dedicated kabil-interviews@ calendar
- Failed creation surfaces error to HR

**Acceptance:**
- Mocked test creates event, returns event_id + meet_link
- Cancellation works

---

#### Step 6.2 — Interview Slots Routes

**Goal:** HR offers + confirms slots.

**Files:**
- `src/db/models/interview_slot.py`
- `alembic/versions/0010_interview_slots.py`
- `src/api/routes/interviews.py`
- `src/services/interview_service.py`

**Endpoints:**
- `POST /applications/{id}/interview-slots` — HR offers 2-5 slots, triggers WA send
- `GET /applications/{id}/interview-slots`
- `PATCH /interview-slots/{slot_id}/confirm` — HR confirms (after candidate picks)
- `DELETE /interview-slots/{slot_id}`

**Caching:** N/A

**Security:**
- Validate slot_start is in the future
- Validate 2-5 slots per offer
- No two confirmed slots per application

**Acceptance:**
- Offering 5 slots succeeds
- 6 slots returns 422
- Past slot returns 422
- Confirm creates Meet link + sends email

---

#### Step 6.3 — Interview Scheduling LangGraph Agent

**Goal:** Stateful agent for slots → wait → confirm → invite.

**Files:**
- `src/agents/scheduling_agent.py`
- `src/agents/nodes/scheduling_nodes.py`
- Templates: slot offer (interactive buttons), confirmation, no-slot-works follow-up

**Flow nodes:**
- `send_slots_node` → `await_selection_node` → (`create_meet_node` → `send_invites_node` → done | `notify_hr_node` → wait for new slots)

**Caching:** N/A

**Security:**
- 48h timeout
- Slot mid-flight: if HR cancels slot while candidate is selecting, handle gracefully (mark expired, notify both)

**Acceptance:**
- Happy path: HR offers → candidate picks button → Meet created → both get email with ICS
- "None work" path: candidate replies, HR gets notification to add more slots

---

#### Step 6.4 — Email Sending

**Goal:** Send confirmation emails with ICS attachments.

**Files:**
- `src/integrations/smtp.py` (or SendGrid client)
- `src/utils/ics.py` — generate ICS file
- Email templates (jinja) for: candidate confirmation, HR confirmation

**Caching:** N/A

**Security:**
- SMTP credentials from Key Vault
- SPF/DKIM/DMARC configured on sending domain
- Email content sanitized (no script injection via candidate name)

**Acceptance:**
- Email sent to both parties on slot confirmation
- ICS file opens in Google Calendar / Outlook
- Email failure does not prevent slot confirmation (logged, retry queued)

---

### Phase 7: Talent Pool

#### Step 7.1 — Talent Pool Models & Routes

**Goal:** Add candidates to pool, list, expire.

**Files:**
- `src/db/models/talent_pool_entry.py`
- `alembic/versions/0011_talent_pool.py`
- `src/api/routes/talent_pool.py`
- `src/services/talent_pool_service.py`

**Endpoints:**
- `GET /talent-pool?expired=&page=` — list
- `POST /talent-pool/upload` — direct CV upload (no job)
- `PATCH /talent-pool/entries/{id}` — extend expiry / deactivate
- `POST /talent-pool/search` — semantic search

**Caching:**
- Search results cached for 30s per query

**Security:**
- HR auth on all endpoints
- Direct upload uses same security as public upload (consent, validation)

**Acceptance:**
- Direct upload creates candidate + CV doc + talent pool entry
- Listing paginates correctly
- Search returns ranked results

---

#### Step 7.2 — Talent Pool Search Service

**Goal:** Semantic search using LangChain PGVector.

**Files:**
- `src/services/talent_pool/search.py`
- Uses `langchain_postgres.PGVector` retriever
- Embeds query, runs cosine search on `cv_documents.cv_embedding` filtered to talent pool members

**Caching:**
- Query → results cached for 30s (key = SHA256 of query string)

**Security:**
- Strip PII from query before logging
- Cap query length (1000 chars)

**Acceptance:**
- "Senior Python backend, 5 years" returns relevant candidates
- Scores monotonically decreasing
- Only active talent pool entries returned

---

#### Step 7.3 — Job Closure → Talent Pool Sweep

**Goal:** On job close, move accepted candidates to talent pool.

**Files:**
- `src/workers/tasks/talent_pool.py`
  - `sweep_job_to_talent_pool_task(job_id)`
- Triggered on `PATCH /jobs/{id}/status` to closed

**Caching:** N/A

**Security:**
- Idempotent: re-running on same job doesn't duplicate entries
- Audit logged per candidate added

**Acceptance:**
- Closing job moves all `status='accepted'` applications' candidates to pool
- Already-in-pool candidates aren't duplicated, expires_at refreshed

---

#### Step 7.4 — Talent Pool Expiry Cron

**Goal:** Nightly task deactivates expired entries.

**Files:**
- `src/workers/tasks/talent_pool.py` (addition)
- `expire_talent_pool_entries_task()` — scheduled via Celery beat

**Caching:** N/A

**Security:**
- Soft delete (`is_active=false`), data retained for audit
- Run once per day at 03:00 UTC

**Acceptance:**
- Entries past `expires_at` become `is_active=false`
- Search excludes inactive entries

---

### Phase 8: Hardening

#### Step 8.1 — Audit Log Coverage Review

**Goal:** Ensure every HR mutation writes to audit_logs.

**Files:**
- `src/services/audit_service.py` — central helper
- Refactor all services to use it
- `tests/integration/test_audit_coverage.py`

**Acceptance:**
- Test asserts every PATCH/POST/DELETE in routes results in at least one audit entry

---

#### Step 8.2 — Observability

**Goal:** OpenTelemetry traces, structured logs, error reporting.

**Files:**
- `src/observability.py` — OTEL setup
- App Insights integration
- Sentry SDK (optional)

**Acceptance:**
- Traces visible in App Insights
- Errors grouped in Sentry

---

#### Step 8.3 — Health Checks for Dependencies

**Goal:** `/health/live` and `/health/ready` differentiate.

**Files:**
- `src/api/routes/health.py`

**Checks:**
- `/health/live` — process alive
- `/health/ready` — Postgres reachable, Redis reachable, Anthropic API reachable

**Acceptance:**
- Live always 200 unless container dying
- Ready returns 503 if DB down

---

#### Step 8.4 — Backup & Restore

**Goal:** Verify backups work.

**Files:**
- `docs/backup-restore.md`
- Azure Postgres: daily automated backups, 7-day retention
- Azure Blob: soft-delete enabled, 14-day retention
- Test restore procedure documented

**Acceptance:**
- Document restoration tested in staging at least once

---

## 6. Cross-Cutting Concerns

### Caching Strategy Summary

| Layer | Cache | TTL | Invalidation |
|---|---|---|---|
| OpenAI embeddings | Redis (key=sha256 of input) | 30 days | Never (deterministic) |
| Claude responses | Redis (key=sha256 of prompt) | 7 days | Configurable per call |
| PDF text extraction | Redis (key=sha256 of bytes) | 30 days | Never |
| Azure Blob signed URLs | Redis (key=blob path) | 80% of URL TTL | Auto-expire |
| `GET /jobs/{id}` | Redis (key=job:detail:{id}:v{epoch}) | 60s | Version bump |
| `GET /jobs/{id}/applications` | Redis (key=apps:list:{job_id}:{filter_hash}) | 15s | On any app mutation |
| Slug → job_id | Redis | 5 min | On slug deactivation |
| User lookup post-auth | Redis | 5 min | On password change |
| Talent pool search | Redis (key=sha256 of query) | 30s | Background refresh |

### Security Checklist

| Concern | Mitigation |
|---|---|
| Secrets in code | All from env / Key Vault, `.env` in gitignore |
| SQL injection | SQLAlchemy parameterized everywhere |
| XSS in stored data | Sanitize on write; FE renders as text not HTML |
| CSRF | Stateless JWT, no cookies for API |
| PII in logs | Regex redaction (email, phone) in log formatter |
| Webhook tampering | HMAC signature verification |
| Replay attacks (webhooks) | Event ID dedup in Redis (24h) |
| Upload abuse | Size limit, magic-byte check, content-type check |
| Authentication brute force | Login attempt lockout (IP-based v1, real WAF phase 2) |
| Token theft | Short JWT expiry (8h), session revocation, IP logged |
| Cross-user data access | Single HR v1, but pattern: every query filters on `user_id` already |
| Backup integrity | Tested restore procedure |
| Audit gaps | Centralized audit helper, test coverage |
| Mass-PII exfiltration | No "export all" endpoint, pagination enforced |
| Public upload abuse | Slug rotation, max upload size, honeypot |
| OpenAI / Claude key leak | Server-side only, never in FE; rotated quarterly |
| GDPR / PDPL deletion | Soft delete pattern, `candidates DELETE` cascades softly |
| Consent | Explicit consent record per candidate, IP + UA stored |

### Environment Variables Required

```
# App
APP_ENV=production
APP_PORT=8000
APP_SECRET_KEY=  # JWT signing, 32+ bytes
APP_PUBLIC_URL=https://api.kabil.ai

# DB
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/kabil
DATABASE_POOL_SIZE=10

# Redis
REDIS_URL=redis://...

# Azure
AZURE_BLOB_CONNECTION_STRING=
AZURE_BLOB_CONTAINER=cvs

# AI
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# WhatsApp
META_WA_PHONE_NUMBER_ID=
META_WA_ACCESS_TOKEN=
META_WA_APP_SECRET=         # for webhook signature
META_WA_VERIFY_TOKEN=       # for webhook verification

# Google
GOOGLE_SERVICE_ACCOUNT_JSON=
GOOGLE_CALENDAR_ID=

# SMTP
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
SMTP_FROM=noreply@kabil.ai

# Tuning
SIMILARITY_REJECTION_THRESHOLD=0.4
CV_MAX_FILE_SIZE_MB=10
WHATSAPP_REPLY_TIMEOUT_HOURS=24
INTERVIEW_REPLY_TIMEOUT_HOURS=48
TALENT_POOL_EXPIRY_DAYS=365
```

### Folder Structure (Reference)

```
kabil-backend/
├── alembic/
│   └── versions/
├── docs/
│   ├── KABIL_AI_ARCHITECTURE.md   ← this file
│   └── backup-restore.md
├── scripts/
│   └── seed_admin.py
├── src/
│   ├── main.py
│   ├── config.py
│   ├── logging_config.py
│   ├── observability.py
│   ├── db/
│   │   ├── base.py
│   │   ├── session.py
│   │   ├── types.py
│   │   └── models/
│   ├── schemas/
│   ├── api/
│   │   ├── deps.py
│   │   ├── exception_handlers.py
│   │   └── routes/
│   ├── services/
│   │   ├── authenticity/
│   │   ├── scoring/
│   │   └── talent_pool/
│   ├── agents/
│   │   ├── whatsapp_agent.py
│   │   ├── scheduling_agent.py
│   │   └── nodes/
│   ├── ai/
│   │   ├── claude.py
│   │   ├── embeddings.py
│   │   ├── cv_parser.py
│   │   └── prompts/
│   ├── integrations/
│   │   ├── azure_blob.py
│   │   ├── meta_whatsapp.py
│   │   ├── google_calendar.py
│   │   └── smtp.py
│   ├── workers/
│   │   ├── celery_app.py
│   │   └── tasks/
│   ├── enums/
│   └── utils/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│       ├── sample_cv_en.pdf
│       └── sample_cv_ar.pdf
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── README.md
```

---

## How to Use This Document with Claude Code

For each step:

1. Open a Claude Code session.
2. Paste the **step's section** (e.g. just Step 2.5).
3. Add: "Implement this step. Don't proceed past the acceptance criteria. Write tests."
4. Review the output. Run the acceptance tests.
5. Commit. Move to next step.

Do **not** ask Claude Code to implement multiple steps at once — each step has its own surface area and reviewing in chunks keeps quality high.

Recommended commit message format: `feat(step-X.Y): <step name>`

---

*End of document.*
