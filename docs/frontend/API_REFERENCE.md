# Kabil.ai API Reference (Frontend)

The complete HTTP contract for the Kabil.ai backend, written for frontend
engineers. For end-to-end journeys and the async pipeline timing, see
[`WORKFLOWS.md`](./WORKFLOWS.md) and [`ASYNC_AND_POLLING.md`](./ASYNC_AND_POLLING.md).
For the closed sets (stages, statuses, enums), see [`ENUMS.md`](./ENUMS.md).
TypeScript types for every shape below live in
[`kabil-api.types.ts`](./kabil-api.types.ts).

> The backend also serves **live, auto-generated OpenAPI docs** at
> `GET /docs` (Swagger UI) and `GET /openapi.json`. This file is the curated,
> task-oriented companion; the OpenAPI spec is the byte-exact source of truth.

---

## 1. Base URL & environments

| Environment | Base URL |
|---|---|
| Local dev | `http://localhost:8000` |
| Production | _(set by deployment; Railway)_ |

All paths below are relative to the base URL. There is **no `/api` prefix** and
**no version prefix** — routes are mounted at the root (`/auth`, `/jobs`, …).

---

## 2. Authentication

HR endpoints use a **Bearer token** obtained from `POST /auth/login`.

```
Authorization: Bearer <access_token>
```

- The token lives **8 hours** (`expires_at` in the login response). Schedule a
  silent re-login or prompt before it lapses; an expired/revoked token returns
  `401 unauthorized`.
- `POST /auth/logout` revokes the current session server-side.
- The **public apply** route (`/public/apply/...`) is anonymous — no token.

Roles: `admin`, `hiring_manager`. Both can use all HR routes today (no
per-role gating in the current surface).

### CORS / credentials

CORS is restricted to the origins configured on the backend
(`Settings.cors_origins`). Allowed methods: `GET, POST, PATCH, DELETE,
OPTIONS`. Allowed headers: `Authorization`, `Content-Type`, `X-Correlation-ID`.
`allow_credentials` is on. If your Next.js dev origin isn't in the allow-list,
add it to the backend env before debugging CORS.

---

## 3. Conventions

### 3.1 Content types

- JSON endpoints: send `Content-Type: application/json`.
- File endpoints (public apply, bulk upload): send `multipart/form-data`.
  **Do not set `Content-Type` manually** when posting `FormData` from the
  browser — let fetch set the multipart boundary.

### 3.2 IDs & timestamps

- All ids are UUID strings. Timestamps are ISO-8601 UTC strings.
- `null` = not set / unknown.

### 3.3 Pagination

List endpoints accept `page` (≥1, default `1`) and `page_size` (1..100,
default `20`) and return:

```json
{ "items": [ ... ], "total": 137, "page": 1, "page_size": 20 }
```

### 3.4 Correlation ID

Every response carries an `X-Correlation-ID` header, and every **error** body
repeats it as `correlation_id`. You may send your own `X-Correlation-ID` on a
request to thread it through the logs. Surface it in error toasts so support can
trace a report to a server log line.

### 3.5 Status codes you'll see

| Code | Meaning |
|---|---|
| `200` | OK (GET / PATCH success) |
| `202` | Accepted — work enqueued asynchronously (job create, status→open, public apply, bulk upload, rescore) |
| `204` | No content (logout) |
| `400` | Bad request (e.g. invalid email/phone on apply, file-count out of bounds) |
| `401` | Missing/invalid/expired token |
| `404` | Not found |
| `409` | Conflict (illegal job status transition) |
| `410` | Gone (inactive/unknown public slug) |
| `422` | Validation error (bad body, illegal stage/status transition) |

---

## 4. Error envelope

Every non-2xx response has the same JSON shape:

```json
{
  "error": "validation_error",
  "message": "Request validation failed.",
  "details": [ /* present on 422 and some domain errors */ ],
  "correlation_id": "8c2a0b14-..."
}
```

- `error` — machine code (`unauthorized`, `not_found`, `conflict`,
  `validation_error`, … or a domain-specific code).
- `message` — human-readable. In **production** the catch-all 500 hides the
  detail; in dev/test it includes the exception type + message.
- `details` — on `422` this is FastAPI's field-level error list (`loc`, `msg`,
  `type`). Use it to map errors back onto form fields.

> See [`kabil-api.types.ts`](./kabil-api.types.ts) → `ApiError`.

---

## 5. Endpoints

### Health

#### `GET /health`
No auth. → `200` `{ "status": "ok", "version": "0.1.0" }`

---

### Auth

#### `POST /auth/login`
No auth. Body `LoginRequest`:
```json
{ "email": "hr@example.com", "password": "••••••••" }
```
→ `200` `LoginResponse`:
```json
{ "access_token": "ey...", "token_type": "bearer", "expires_at": "2026-06-08T20:00:00Z" }
```
Wrong credentials → `401`.

#### `GET /auth/me`
Auth required. → `200` `MeResponse` (id, email, full_name, role, created_at,
last_login_at).

#### `POST /auth/logout`
Auth required. Revokes the session. → `204` (no body).

---

### Dashboard

#### `GET /dashboard`
Auth. Workspace-wide aggregate counts for the HR home screen — there are no
query params and nothing to scope by (the system is a single shared
workspace). Read-only; safe to poll on the home screen.

→ `200` `DashboardSummaryResponse`:
```json
{
  "jobs": {
    "total": 12,
    "by_status": { "draft": 2, "open": 7, "closed": 3 }
  },
  "applications": {
    "total": 184,
    "by_stage": {
      "vector_screen": 90, "hard_filter": 40,
      "whatsapp": 30, "interview": 20, "done": 4
    },
    "by_status": { "active": 150, "rejected": 30, "accepted": 4, "archived": 0 }
  },
  "candidates": { "total": 161 },
  "talent_pool": { "active": 22 }
}
```
- `by_status` / `by_stage` always contain **every** enum value as a key,
  zero-filled — no need to guard a missing key.
- `jobs.total` / `applications.total` equal the sum of their breakdown.
- `candidates.total` counts unique people (one row per email+phone, shared
  across their applications). `talent_pool.active` excludes deactivated
  entries.
- A candidate's stage/status maps 1:1 to their application; a person with no
  application isn't in `applications.*` but is in `candidates.total`.

The `GET /dashboard` summary above feeds the **Overview** cards. The four
endpoints below feed the rest of the home screen (Performance, Candidate
Pipeline, Upcoming Interviews, Pending Feedback). All are HR-authed, read-only,
and safe to poll.

#### `GET /dashboard/performance`
Auth. One row per **non-draft** job (open + closed) for the Performance table.
Rows are sorted **at-risk first, then by `days_open` descending** so the rows
HR must act on float to the top.

→ `200` `JobPerformanceResponse`:
```json
{
  "rows": [
    {
      "job_id": "<uuid>",
      "title": "Product Designer",
      "status": "open",
      "candidates": 18,
      "shortlisted": 0,
      "days_open": 21,
      "health": "at_risk"
    }
  ]
}
```
- `candidates` / `shortlisted` **exclude archived** (moved-to-pool) stints.
  `shortlisted` counts applications that reached the terminal `done` stage.
- `days_open` = `(closed_at or now) − created_at` in whole calendar days (jobs
  have no separate `opened_at`, so creation time is the proxy).
- `health` is `JobHealth` — see [`ENUMS.md`](./ENUMS.md#dashboard--job-health--jobhealth).
  Age-based on **open** jobs in **working days** (Mon–Fri): `unhealthy` = open
  > 20 working days, `at_risk` = open ≥ 18 working days, else `healthy`. Rows
  are sorted worst-health-first.

#### `GET /dashboard/pipeline`
Auth. Query: `job_id` (uuid, optional). The Candidate Pipeline funnel. Omit
`job_id` for the **All Jobs** (workspace-wide) view; pass it to scope to one
job (the panel's dropdown).

→ `200` `CandidatePipelineResponse`:
```json
{
  "job_id": "<uuid|null>",
  "by_bucket": { "sourcing": 3, "screening": 2, "interview": 0, "final_shortlist": 0 },
  "by_stage": { "vector_screen": 2, "hard_filter": 1, "whatsapp": 2, "interview": 0, "done": 0 },
  "applied": 12,
  "offers": 0,
  "conversion_rate": 0.0
}
```
- `by_bucket` counts **active** applications collapsed into the four
  `PipelineBucket` columns, always fully populated (zero-filled). Stage→bucket
  mapping in [`ENUMS.md`](./ENUMS.md#dashboard--pipeline-bucket--pipelinebucket).
- `by_stage` is the same **active** counts kept at raw `ApplicationStage`
  granularity (every stage key present, zero-filled) — use it to mirror the
  kanban board's five columns without collapsing.
- Applied→Offer conversion: `applied` = every **non-archived** application in
  scope; `offers` = those with `status == accepted`; `conversion_rate` =
  `offers / applied` as a 0–100 percentage (`0.0` when none applied).

#### `GET /dashboard/upcoming-interviews`
Auth. Query: `limit` (1..200, default **3**). The nearest booked, future
interviews (soonest first). The dashboard card uses the default (top 3); the
**"show all"** view passes a larger `limit`.

→ `200` `UpcomingInterviewsResponse`:
```json
{
  "interviews": [
    {
      "application_id": "<uuid>",
      "job_id": "<uuid>",
      "candidate_name": "Sara Al-Mansouri",
      "job_title": "Senior Product Designer",
      "scheduled_start_at": "2026-06-19T10:00:00Z",
      "scheduled_end_at": "2026-06-19T10:45:00Z",
      "location_type": "zoom",
      "join_url": "https://zoom.us/j/...",
      "location_text": null,
      "invitee_timezone": "Asia/Dubai"
    }
  ],
  "total": 7
}
```
- Only `booked` interviews with `scheduled_start_at` in the future are
  returned (archived applications excluded). `total` is the full count of
  future booked interviews, so you know whether "show all" has more than the
  returned page.
- Meeting location: show `join_url` for virtual meetings, else `location_text`
  (physical address / phone). `invitee_timezone` lets you localize the slot.

#### `GET /dashboard/pending-feedback`
Auth. Applications stalled at the `interview` stage awaiting a post-interview
decision — active, still at `stage == interview`, and last moved **more than 3
days ago** (`INTERVIEW_PENDING_FEEDBACK_DAYS`). Oldest first.

→ `200` `PendingFeedbackResponse`:
```json
{
  "items": [
    {
      "application_id": "<uuid>",
      "job_id": "<uuid>",
      "candidate_name": "Rania Khalil",
      "job_title": "Backend Engineer",
      "stage": "interview",
      "stage_updated_at": "2026-06-13T09:00:00Z",
      "days_waiting": 10
    }
  ]
}
```
- `days_waiting` is whole days since `stage_updated_at` (when the app entered
  the interview stage).

---

### Jobs

#### `POST /jobs`
Auth. Body `JobCreateRequest` (see types). Creates a job in `draft`.
→ `202` `{ "id": "<uuid>" }`.
Validation: `country` must be ISO-3166 alpha-2; `currency` 3 letters;
`min_salary ≤ max_salary`; skills ≤50 etc. (→ `422`).
The WhatsApp question list is not part of this body — it is generated
server-side when the job opens (see `PATCH /jobs/{job_id}/status`).

#### `POST /jobs/generate-description`
Auth. **AI JD Builder.** Body `JobDescriptionGenerateRequest` — the Role-Basics
fields only (same shape as `JobCreateRequest` minus `job_description`;
unknown fields → `422`). Drafts a complete job description
from those fields with Claude. **Synchronous and stateless** — no job is
created; the create-job wizard calls it on the JD Builder step to pre-fill the
description, which HR can then edit.
Query: `regenerate` (bool, default `false`) — bypasses the server-side Claude
cache so a repeat click yields a fresh draft.
→ `200` `{ "job_description": "<text>" }`.
- `503 ai_service_unavailable` if Claude isn't configured.
- `502 job_description_generation_failed` on a transient AI error or an
  unusably short draft.

#### `GET /jobs`
Auth. Query: `status` (`draft|open|inactive|archived|closed`), `search`
(1..120 chars), `order` (`updated_at` (default, most-recently-touched first) |
`title` (case-insensitive alphabetical — use for job-selector dropdowns so they
list jobs identically)), `page`, `page_size`. → `200` `JobListResponse`
(paginated `JobListItem`).

Each `JobListItem` carries `applications_by_stage` — the per-job pipeline
funnel, counting that job's applications at each `ApplicationStage`. It is
always fully populated (every stage key present, zero-filled — same contract
as the dashboard's `by_stage`). All applications are counted regardless of
status; rejected/accepted rows keep the stage they stopped at.
```json
"applications_by_stage": {
  "vector_screen": 12, "hard_filter": 5,
  "whatsapp": 3, "interview": 1, "done": 0
}
```

#### `GET /jobs/{job_id}`
Auth. → `200` `JobDetail` (full job incl. `whatsapp_questions`,
`pipeline_status`, `ready_for_applications`, `public_slug`).

#### `PATCH /jobs/{job_id}/status`
Auth. Body `{ "status": "open" | "inactive" | "archived" | "closed" }`
(`open` = "Active" in the UI; `inactive` = paused; `archived` = ended terminal).
Allowed edges: `draft→{open,archived}`, `open→{inactive,archived,closed}`,
`inactive→{open,archived}`, `archived→{open}`, `closed→{open,archived}`.
- `draft → open` triggers the **job pipeline** (embed JD + generate WhatsApp
  questions, async). `ready_for_applications` flips `true` when both finish.
  The generated list = a fixed canonical set of **6 deterministic questions**
  (reason for considering a move, salary expectation, notice period, visa/
  residency, employment type, work mode — placeholders filled from the job's
  own fields) + up to **3 AI-authored `background_validation`** questions that
  verify hands-on experience with the role's listed required + preferred skills
  (skills only — no work history). HR can append custom questions afterward via
  `PATCH /jobs/{id}/whatsapp-questions`.
- Illegal transition → `409 conflict`.
- `status` outside `{open, inactive, archived, closed}` (e.g. `draft`) → `422`.
- Archiving (or closing) stamps `closed_at`; pausing to `inactive` does not;
  reactivating to `open` clears it.
→ `200` `JobDetail`.

#### `GET /jobs/{job_id}/whatsapp-questions`
Auth. → `200` `{ "questions": WhatsAppQuestion[] }`.

#### `PATCH /jobs/{job_id}/whatsapp-questions`
Auth. Body `{ "questions": WhatsAppQuestion[] }` — the **full ordered list**
(1..15). `order` and `id` must each be unique. → `200` `{ "questions": [...] }`.
Each question carries `ai_verifies_response` (default `false`); only questions
with `ai_verifies_response=true` (in practice the AI `background_validation`
questions) have the candidate's reply AI-scored. Fixed and custom answers are
stored verbatim and never scored.

---

### Applications (HR)

#### `GET /jobs/{job_id}/applications`
Auth. Query:
- `stage` — `ApplicationStage` filter
- `status` — `ApplicationStatus` filter
- `order` — `ApplicationListOrder` (default `-created_at`)
- `page`, `page_size`

→ `200` `ApplicationListResponse` (paginated `ApplicationListItem`, with the
denormalized `similarity_score` / `hard_filter_score` for sorting/triage).

> **Archived stints are hidden by default.** With no `status` filter, rows with
> `status=archived` (candidates moved back to the pool) are excluded — they're
> not part of the active pipeline. Pass `status=archived` explicitly to list
> them, or use the candidate-history endpoint for the full cross-job picture.

#### `GET /applications/{application_id}`
Auth. → `200` `ApplicationDetail` — full nested object: candidate snapshot,
current CV reference, denormalized scores, `rejection_reason`,
`pipeline_status`, and the chronological `scores[]` (incl. the synthesized
`authenticity` entry). → `404` if unknown.

#### `GET /applications/{application_id}/audit-log`
Auth. → `200` `AuditLogResponse` (newest-first audit entries).

#### `PATCH /applications/{application_id}/stage`
Auth. Body `{ "stage": ApplicationStage, "reason"?: string }`.
Forward-only transitions (see [`WORKFLOWS.md`](./WORKFLOWS.md) state machine).
Entering `hard_filter` enqueues **Claude Opus** hard-filter scoring (async).
Illegal edge → `422`. → `200` `ApplicationDetail`.

#### `PATCH /applications/{application_id}/status`
Auth. Body `{ "status": ApplicationStatus, "reason"?: string }`.
`active → rejected|accepted`, `rejected → active` (HR override, stage
preserved). `accepted` is terminal. Illegal edge → `422`.
→ `200` `ApplicationDetail`.

#### `POST /applications/{application_id}/rescore?type=similarity|hard_filter`
Auth. Query `type` required. Enqueues a forced rescore (bypasses idempotency;
hard_filter also bypasses the Claude cache). → `202`
`{ application_id, type, enqueued: true }`. The new score appears on the detail
endpoint when the worker finishes — poll for it.

#### `POST /applications/{application_id}/move-to-pool`
Auth. Moves the candidate out of this job's pipeline and back into the talent
pool. **Soft-archives** the application (`status=archived`) — its scores +
WhatsApp transcript are **retained** as cross-job history (see the candidate
history endpoint), and the stage is preserved so HR sees where the stint ended.
(Re-)adds the candidate to the pool. Only this job is affected — other jobs the
candidate is on are untouched. The candidate can be sourced back onto the job
afterwards for a fresh stint (a new application, no "already applied" conflict),
the archived one surviving alongside it. → `200` `TalentPoolEntry`. `404` if the
application doesn't exist; `401` without auth.

> Use this for the pipeline→pool drag — **not** `POST /talent-pool/entries`,
> which only adds a pool entry and leaves the application in place.
> Archived stints no longer appear in `GET /jobs/{job_id}/applications` by
> default (pass `status=archived` to list them).

#### `GET /talent-pool/candidates/{candidate_id}/history`
Auth. The candidate's **entire cross-job journey** for the talent-pool view:
one stint per application they ever had — live, rejected, accepted, or archived
(moved back to the pool) — each carrying the scores and WhatsApp screening
digest computed against *that* job. Candidate-level **authenticity** is surfaced
once on the `candidate` snapshot (it's shared across stints, not per-job).
→ `200` `CandidateHistoryResponse`; `404` if the candidate is unknown; `401`
without auth.

This is what powers "see the candidate's full history, scored against every job
they were part of" from the pool. Stints are newest-first by creation;
`in_pool` reports whether the candidate currently sits in the active pool.

#### `POST /jobs/{job_id}/applications/bulk-upload`
Auth. `multipart/form-data` with `files` (1..50 PDFs, ≤10 MB each).
Fail-soft per file. → `202` `BulkUploadResponse`:
- `applications[]` — newly created
- `already_applied[]` — candidate already applied to this job
- `rejected[]` — `{ filename, reason }` (`not_pdf` / `too_large` /
  `duplicate_in_batch` / `parse_failed_no_contact`)

Whole request fails `400` only when file count is out of bounds; `401` without
auth; `422` if the job is closed.

---

### Public apply (anonymous candidate)

#### `POST /public/apply/{slug}/upload`
No auth. `multipart/form-data` fields:

| Field | Type | Notes |
|---|---|---|
| `pdf` | file | the CV (PDF, ≤10 MB) |
| `email` | text | candidate email |
| `phone` | text | raw phone (normalized server-side to E.164) |
| `full_name` | text | |
| `consent` | text `"true"` | must be truthy |
| `honeypot` | text | hidden anti-bot field — leave empty |

→ `202` `{ "reference_number": "<32-hex>" }`.

**Privacy by design:** the response is identical for new vs. duplicate
submissions and for honeypot hits — never branch UI on "already applied".
- `410` — inactive/unknown slug
- `400` — missing consent, invalid email, invalid phone

The `slug` comes from a job's `public_slug` (on `JobDetail` / `JobListItem`).

---

## 6. Quick fetch example (Next.js)

```ts
import type { LoginResponse, JobListResponse } from "@/lib/kabil/types";

const BASE = process.env.NEXT_PUBLIC_KABIL_API!; // e.g. http://localhost:8000

async function login(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await res.json(); // ApiError
  return res.json();
}

async function listJobs(token: string): Promise<JobListResponse> {
  const res = await fetch(`${BASE}/jobs?status=open&page=1&page_size=20`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw await res.json();
  return res.json();
}

// Public apply — note: do NOT set Content-Type for FormData
async function apply(slug: string, form: FormData): Promise<{ reference_number: string }> {
  const res = await fetch(`${BASE}/public/apply/${slug}/upload`, {
    method: "POST",
    body: form, // append pdf, email, phone, full_name, consent
  });
  if (!res.ok) throw await res.json();
  return res.json();
}
```

A reusable typed client wrapper pattern is sketched in
[`WORKFLOWS.md`](./WORKFLOWS.md#appendix-a-minimal-typed-client).
