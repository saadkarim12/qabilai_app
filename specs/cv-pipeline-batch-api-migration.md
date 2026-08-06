# CV Pipeline — Anthropic Message Batches API Migration

## Overview

The CV pipeline makes two **automatic** Claude calls per CV — `parse_cv`
(Sonnet) and the authenticity judge (Haiku). This spec migrates those two
calls to the [Anthropic Message Batches API](https://docs.anthropic.com/en/docs/build-with-claude/batch-processing)
to capture its **50% token discount**, with no change to prompts, models, or
output schemas. Because the Batch API is asynchronous-by-design (results in
minutes–hours, no sub-second SLA), this trades pipeline *latency* for cost and
is therefore gated behind a feature flag with a synchronous fallback.

> **Recommendation up front (read before building):** at the measured
> **~1.6¢/CV** (parse_cv ≈ 70% of it), batch saves ≈ **$0.008/CV** → ~$80 per
> 10k CVs. The build (new table + migration, accumulator + poller tasks, Celery
> beat, partial-failure handling, monitoring) is several engineer-days plus
> ongoing operational surface. **Breakeven is high** — this is only clearly
> worth it at sustained high volume (≳100k CVs/month) *and* only if the product
> tolerates deferred parse/authenticity/auto-reject. The cheaper lever —
> switching `parse_cv` from Sonnet to Haiku (~67% off parse, one-line change, no
> latency change) — should be evaluated first. See [Alternatives](#alternatives-considered).

## Goals & Non-Goals

**Goals**
- Route the two automatic CV-pipeline LLM calls (`parse_cv`, authenticity judge)
  through the Message Batches API to capture the 50% discount.
- Preserve today's behavior exactly when the flag is off (zero-risk default).
- Preserve idempotency and the `pipeline_status` per-step contract.
- Preserve the `call_claude` Redis cache semantics — a byte-identical re-upload
  must still short-circuit without entering a batch.
- Provide a synchronous fallback so no CV is ever permanently stuck behind a
  failed or stalled batch.

**Non-Goals**
- **`contact_extractor`** stays synchronous — it gates the bulk-upload `202`
  response inline; batch latency (minutes+) is incompatible. Out of scope.
- **`hard_filter` scoring (Opus)** stays real-time — it is HR-triggered and
  interactive (HR waits for the score); batching breaks that UX. Out of scope.
- **Embeddings (OpenAI)** stay synchronous — they sit on the auto-reject
  critical path and are cheap; OpenAI's batch API is a separate future effort.
- No prompt, model, schema, or scoring-logic changes.
- No change to the public/bulk upload request/response contracts.

## User Stories

- *As the platform operator,* I want the automatic CV LLM calls billed at the
  batch rate, so that per-CV cost drops ~50% on the two largest automatic calls
  at high volume.
- *As an HR user,* I want CV parsing/authenticity to still complete reliably
  (even if a bit later), so that triage data and auto-reject still appear
  without manual intervention.
- *As an on-call engineer,* I want a stuck or failed batch to auto-fall-back to
  synchronous calls, so that no candidate is silently dropped from the pipeline.
- *As an engineer,* I want the migration behind a flag, so that I can enable it
  per environment and roll back instantly.

## Requirements

### Functional

1. When `cv_pipeline_use_batch_api` is **off**, the pipeline behaves exactly as
   today: the linear `chain()` runs `parse_cv_task` and
   `compute_authenticity_task` via synchronous `call_claude`.
2. When **on**, `parse_cv` and authenticity requests are **accumulated** and
   submitted as Message Batches; their results resume the per-CV pipeline.
3. Before enqueuing any request into a batch, the system MUST check the existing
   `call_claude` Redis cache (same key); a cache hit bypasses the batch and
   proceeds synchronously with the cached result.
4. Batch results MUST be written back to the Redis cache under the same key/TTL,
   so subsequent identical uploads hit the cache as they do today.
5. Authenticity batching MUST occur only **after** the parse result for that CV
   exists (authenticity's prompt embeds `parsed_cv_json`) — i.e. two sequential
   batch cycles per CV.
6. Each per-step result MUST update `pipeline_status` (`pending`/`ok`/`failed`)
   identically to the synchronous path, and downstream tasks self-skip on
   missing prerequisites exactly as today.
7. The pipeline MUST resume the downstream chain
   (`embed_cv → score_similarity → auto_reject`) once authenticity completes.
8. A request that **errors** in a batch, or a batch that exceeds a configurable
   max age (`cv_pipeline_batch_max_age_seconds`), MUST fall back to a
   synchronous `call_claude` for the affected request(s).
9. Batch submission MUST be triggered by either a size threshold
   (`cv_pipeline_batch_max_requests`) or a time window
   (`cv_pipeline_batch_flush_seconds`), whichever comes first.
10. All operations MUST be idempotent: re-running a flush/poll task must not
    double-submit, double-write, or re-enqueue an already-resolved request.

### Non-Functional

- **Cost:** ~50% reduction on the input+output tokens of the two batched calls.
  No change to the non-batched calls.
- **Latency:** added end-to-end delay = flush window + Anthropic batch
  processing (typically minutes, **no SLA, up to 24h**) **× 2 sequential
  cycles**. Worst case is multi-hour-to-~48h for parse+authenticity+downstream.
  This is the central tradeoff (see [Open Questions](#open-questions)).
- **Reliability:** no CV may be permanently stuck; the stuck-batch guard (#8) is
  mandatory, not optional.
- **Observability:** reuse the structured logging just added (`claude.api_call`
  now emits `model`/`input_tokens`/`output_tokens`/`prompt_version`/`cache_bypassed`;
  `claude.cache_hit` emits `prompt_version`). Add batch lifecycle logs
  (`batch.submitted`, `batch.polled`, `batch.completed`, `batch.request_errored`,
  `batch.fallback_sync`) with counts only — never prompt/CV content.
- **Worker constraint:** the Celery worker runs `DB_USE_NULL_POOL=true` (fresh
  event loop per `asyncio.run`); beat/poller tasks follow the same
  `_run_with_session` pattern as existing tasks.
- **Security/PII:** batch request payloads contain CV text and parsed profiles
  (PII/confidential). They MUST NOT be logged; at-rest storage (DB/Redis) follows
  the same handling as `cv_documents.extracted_text` today.

## Proposed Design

### High-level flow (flag ON)

```
extract_text_task
   └─ enqueue_parse_request_task        # park: write pending parse request, status=pending
            ⇢ (Celery beat) flush_parse_batch       # submit batch when window/size hit
            ⇢ (Celery beat) poll_parse_batch        # on "ended": write parsed_profile + cache,
                                                     #   then per-CV → enqueue_authenticity_request
   └─ enqueue_authenticity_request_task # park: write pending authenticity request
            ⇢ (beat) flush_authenticity_batch
            ⇢ (beat) poll_authenticity_batch        # on "ended": write authenticity_* + cache,
                                                     #   then resume downstream chain
   └─ embed_cv_task → score_similarity_task → auto_reject_below_threshold_task   # unchanged
```

The existing `_dispatch_chain` (`src/workers/tasks/cv_pipeline.py:938`) is split:
when the flag is on it dispatches `extract_text_task` linked to
`enqueue_parse_request_task` instead of the straight 6-task chain. The downstream
tail (`embed → similarity → auto_reject`) is dispatched as a fresh chain by the
authenticity poller once results land.

### Data model changes

New migration `alembic/versions/0013_llm_batch_tables.py` adding two tables:

**`llm_batches`** — one row per submitted Anthropic batch.

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `provider_batch_id` | varchar | Anthropic `msgbatch_…` id |
| `kind` | varchar | `parse_cv` \| `authenticity` (new `BatchKind` enum) |
| `status` | varchar | `submitted` \| `ended` \| `failed` (mirror provider) |
| `request_count` | int | |
| `submitted_at` | timestamptz | for the max-age guard |
| `completed_at` | timestamptz null | |

**`llm_batch_requests`** — one row per pending/in-flight per-CV request.

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `custom_id` | varchar | maps result → entity (`cv_document_id` for parse, `candidate_id` for authenticity) |
| `kind` | varchar | `BatchKind` |
| `model` | varchar | `ScoreModel` value |
| `prompt_version` | varchar | for cache key + provenance |
| `cache_key` | varchar | the `_cache_key(...)` digest, so result write-back hits the same slot |
| `payload` | jsonb | `{system, messages, max_tokens, response_format}` (PII — handle as CV text) |
| `status` | varchar | `pending` \| `submitted` \| `done` \| `errored` |
| `batch_id` | uuid null fk → `llm_batches.id` | |
| `created_at` | timestamptz | for window-based flush |

Enums: add `BatchKind` and batch/request `status` values to `src/enums/`
(no inline literals, per project convention). Models register on
`Base.metadata` via `src/db/models/__init__.py`.

> **Alternative store:** Redis (lists + hashes) instead of Postgres tables —
> lower durability, simpler. Rejected for the primary design because a worker
> crash mid-batch must not lose in-flight requests; Postgres gives us
> transactional resume. Noted as an option in [Alternatives](#alternatives-considered).

### `call_claude` integration (`src/ai/claude.py`)

- Extract the cache-key build + cache read/write into reusable helpers so the
  batch path shares **exactly** the same key (`_cache_key`, already present) and
  TTL (`CACHE_TTL_SECONDS`). No duplication of cache semantics.
- Add `call_claude_sync_fallback(...)` = today's `call_claude` with
  `use_cache=True`, used by the stuck-batch guard (#8) — it already exists; we
  just call it for fallback.
- New module `src/ai/claude_batch.py` wrapping
  `client.messages.batches.create / retrieve / results`, building per-request
  bodies in the SDK's `Request(custom_id=…, params=MessageCreateParams(…))`
  shape, mirroring the model/temperature rules in `call_claude` (Opus omits
  `temperature`; not relevant here since both batched models accept it).

### New Celery tasks (`src/workers/tasks/cv_batch.py`)

- `enqueue_parse_request_task(cv_document_id)` / `enqueue_authenticity_request_task(candidate_id)`
  — cache-check; on miss, insert an `llm_batch_requests` row (`pending`); on hit,
  resume immediately with the cached result.
- `flush_parse_batch` / `flush_authenticity_batch` (beat) — gather `pending`
  rows up to `cv_pipeline_batch_max_requests`, submit one batch, mark
  `submitted`, store `llm_batches` row.
- `poll_parse_batch` / `poll_authenticity_batch` (beat) — for each `submitted`
  batch, retrieve status; on `ended`, stream results, write each result to its
  entity + cache, mark `done`, and trigger the next stage; on per-request
  `errored` or batch over max age, route to sync fallback.

Celery **beat** must be introduced (none today): add `beat_schedule` to
`src/workers/celery_app.py` for the four flush/poll tasks at
`cv_pipeline_batch_poll_seconds` intervals, and run a `celery beat` process
(new container/service in `docker-compose.yml` and Railway).

### Config (`src/config.py`) — new `Settings` fields

```python
cv_pipeline_use_batch_api: bool = False          # master flag, default off
cv_pipeline_batch_max_requests: int = 100         # flush when this many pending
cv_pipeline_batch_flush_seconds: int = 300        # …or when oldest pending is this old
cv_pipeline_batch_poll_seconds: int = 60          # beat cadence for poll tasks
cv_pipeline_batch_max_age_seconds: int = 3600     # over this → sync fallback (stuck guard)
```

Tunable numbers that are not per-env knobs (if any) go in
`src/enums/thresholds.py`, per convention.

### Affected modules/files

- `src/workers/tasks/cv_pipeline.py` — `_dispatch_chain` branches on the flag;
  `parse_cv_task` / `compute_authenticity_task` gain a "park into batch" path.
- `src/workers/tasks/cv_batch.py` *(new)* — enqueue/flush/poll tasks.
- `src/ai/claude.py` — factor out cache helpers (no behavior change).
- `src/ai/claude_batch.py` *(new)* — Batches SDK wrapper.
- `src/workers/celery_app.py` — beat schedule.
- `src/config.py` — flag + knobs.
- `src/db/models/` + `src/enums/` + `alembic/versions/0013_…` — new tables/enums.
- `docker-compose.yml` / Railway — `celery beat` process.

## Alternatives Considered

1. **Switch `parse_cv` Sonnet → Haiku instead (recommended to evaluate first).**
   One-line model change, **no latency change**, ~67% off the parse call
   (vs ~50% off two calls for batch) with a fraction of the engineering. The
   original architecture doc even specced Haiku for parsing. Requires a quality
   A/B on real CVs. *Not mutually exclusive* — could combine later.
2. **Redis-only batch store.** Simpler, no migration, but loses transactional
   durability for in-flight requests across worker crashes. Rejected for primary
   design; viable if durability is deemed unnecessary.
3. **Single combined batch for parse + authenticity.** Impossible: authenticity
   depends on parse output, forcing sequential batches.
4. **Batch `hard_filter` (Opus) too.** Rejected — it is interactive/HR-triggered;
   deferring it breaks the "click → see score" UX. (Opus is also the priciest
   call, so this is tempting at scale — revisit only if HR tolerates async
   scoring.)
5. **Do nothing.** Given ~1.6¢/CV, entirely defensible until volume is high; the
   logging changes already shipped make the real cost/hit-rate measurable first.

## Open Questions

1. **Latency tolerance (blocking):** Is deferred parse/authenticity/auto-reject
   (minutes typical, up to ~48h worst case across two cycles) acceptable to HR?
   If candidates must be triageable "within minutes of upload," batch is the
   wrong tool.
2. **Actual volume:** What is sustained CVs/month? Needed to confirm breakeven.
   (Pull from the now-instrumented `claude.api_call` logs once in prod.)
3. **Measured cache hit-rate:** Once logging is live, what fraction of
   parse/authenticity calls already hit cache? High hit-rate further shrinks the
   batchable population (and the savings).
4. Should the flush window prioritize cost (large batches, more latency) or
   latency (frequent small batches, less discount benefit)? Suggest making both
   knobs env-tunable and tuning in staging.
5. Is a dedicated `celery beat` process acceptable on Railway (new service), or
   should polling be a self-rescheduling task instead?

## Rollout Plan

1. **Phase 0 — measure (already enabled):** ship the logging changes; collect
   real per-`prompt_version` token sizes and cache hit-rate in production.
   Decide go/no-go against breakeven before building.
2. **Phase 1 — schema + plumbing (flag off):** migration `0013`, enums, models,
   `claude_batch.py`, cache-helper refactor. No behavior change in prod
   (`cv_pipeline_use_batch_api=False`).
3. **Phase 2 — tasks + beat (flag off):** enqueue/flush/poll tasks + beat
   schedule, fully unit/integration tested, still off in prod.
4. **Phase 3 — staging enable:** flip the flag in staging; verify cost drop,
   latency, fallback, idempotency under load.
5. **Phase 4 — prod canary:** enable for a subset (e.g. one job/tenant or a
   percentage) if feasible, monitor `batch.*` logs and stuck-guard rate.
6. **Phase 5 — full enable** or **rollback** (flip the flag — instant, no deploy).

## Testing Strategy

**Unit**
- Cache-key parity: batch path and `call_claude` produce identical keys for the
  same inputs (guards the cache short-circuit).
- Flush logic: triggers on size threshold and on time window; never
  double-submits.
- Poll logic: `ended` writes results + cache + advances stage; `errored` and
  over-max-age route to sync fallback.
- Flag off → existing synchronous chain path is taken unchanged (regression
  guard on `_dispatch_chain`).
- Idempotency: re-running flush/poll on already-`done` requests is a no-op.

**Integration** (needs dev Postgres + Redis; mock the Anthropic Batches client)
- Full flag-on flow: upload → park → flush → poll(parse) → park(auth) →
  poll(auth) → downstream chain → `pipeline_status` all `ok`.
- Sync fallback end-to-end: simulated batch failure still completes the CV.
- Cache hit short-circuit: pre-warmed cache bypasses the batch entirely.

**End-to-end / load (staging)**
- N concurrent uploads → verify batching, cost (via token logs), and worst-case
  latency stay within configured windows; verify no stuck requests after the
  max-age guard fires.

**Non-regression**
- With the flag off, the full existing `tests/integration` CV-pipeline suite
  passes unchanged.
