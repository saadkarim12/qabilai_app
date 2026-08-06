# Async Pipelines & Polling Guide

Several Kabil.ai actions return **`202 Accepted`** and finish the real work in
background workers. The frontend must treat the affected data as *eventually
consistent*: the response confirms the work was **enqueued**, not **done**.
This guide tells you what runs async, how to detect completion, and what the
in-progress states look like.

Completion is observed by **polling** the relevant GET endpoint and reading the
`pipeline_status` map + score fields — the resilient baseline every integration
should implement.

> **Realtime shortcut:** the backend also pushes a **Server-Sent Events** nudge
> the moment a score or WhatsApp reply lands, so the UI can refetch immediately
> instead of waiting for the next poll. Polling stays as the fallback. See
> **[REALTIME.md](./REALTIME.md)**. Note SSE covers score + WhatsApp events
> only — the **job pipeline** and **talent-pool** readiness below are still
> poll-only.

---

## What's synchronous vs. asynchronous

| Action | Response | Result is… |
|---|---|---|
| Login / logout / me | `200` / `204` | immediate |
| Create job | `202` | row exists immediately; status `draft` |
| List / get job, list / get application, audit log | `200` | immediate |
| Get/patch WhatsApp questions | `200` | immediate |
| Patch application status | `200` | immediate |
| Patch application **stage** | `200` | stage immediate; **scoring async** (when entering `hard_filter`) |
| **Open job** (`status → open`) | `200` | **job pipeline async** |
| **Public apply** | `202` | **CV pipeline async** |
| **Bulk upload** | `202` | rows created; **CV pipeline async per file** |
| **Rescore** | `202` | **scoring async** |
| Patch application **stage** → `whatsapp` | `200` | stage immediate; **WhatsApp invite async** (conversation appears later) |
| **Talent-pool upload** | `201` | entry exists; **CV embed pipeline async** (`enqueued: true` → not searchable yet) |
| **Talent-pool source** | `202` | application created; **CV scoring pipeline async** |

---

## The two pipelines

### Job pipeline — runs on `draft → open`

Two parallel tasks; tracked in `JobDetail.pipeline_status`:

| Key | Produces | Done when |
|---|---|---|
| `embedding` | JD embedding (internal) | `"ok"` |
| `questions` | `whatsapp_questions` | `"ok"` |

`ready_for_applications` flips to `true` **only when both are `"ok"`**. That's
the single flag to gate "job is live" UI on.

```json
// JobDetail.pipeline_status while running
{ "embedding": "ok", "questions": "pending" }
// on failure
{ "embedding": "ok", "questions": "failed", "questions_error": "..." }
```

### CV pipeline — runs on each CV (public apply + bulk upload)

A linear chain; tracked in `ApplicationDetail.pipeline_status`:

| Order | Key | Produces (visible to FE) |
|---|---|---|
| 1 | `extract_text` | — |
| 2 | `parse_cv` | `candidate.parsed_profile` |
| 3 | `authenticity` | `candidate.authenticity_score` / `_band` + `scores[]` authenticity entry |
| 4 | `embed_cv` | — |
| 5 | `similarity` | `similarity_score` + `scores[]` similarity entry |
| 6 | `auto_reject` | may set `status: "rejected"` + `rejection_reason` |

```json
// ApplicationDetail.pipeline_status, fully processed, not auto-rejected
{ "extract_text": "ok", "parse_cv": "ok", "authenticity": "ok",
  "embed_cv": "ok", "similarity": "ok", "auto_reject": "skipped" }
```

### Hard-filter scoring — runs on entering `hard_filter` stage (or rescore)

Not part of the chain above. Tracked under the `hard_filter` key in the
application's `pipeline_status`; produces `hard_filter_score` + a `hard_filter`
entry in `scores[]`.

---

## Step states (`pipeline_status` values)

| State | Meaning | UI |
|---|---|---|
| _(key absent)_ | never ran yet | "queued" / skeleton |
| `pending` | retry-eligible task in flight | spinner |
| `ok` | finished, target fields populated | show the data |
| `failed` | non-transient error; message in `{key}_error` | error badge + the `_error` text |
| `skipped` | step ran but no action needed (e.g. `auto_reject` when above threshold) | treat as success |

A `{key}_error` string companion appears next to any `failed` key.

---

## Detecting completion (what to poll for)

| After… | Poll | Done signal |
|---|---|---|
| Open job | `GET /jobs/{id}` | `ready_for_applications === true` (or `pipeline_status.questions/embedding === "ok"`) |
| Public apply / bulk upload | `GET /applications/{id}` | `pipeline_status.similarity === "ok"`; `similarity_score !== null` |
| Authenticity specifically | `GET /applications/{id}` | `pipeline_status.authenticity === "ok"`; `candidate.authenticity_score !== null` |
| Enter `hard_filter` stage | `GET /applications/{id}` | `hard_filter_score !== null` (or `pipeline_status.hard_filter === "ok"`) |
| Rescore | `GET /applications/{id}` | matching `scores[]` entry has a **newer `computed_at`**, or the score field becomes non-null again |
| Enter `whatsapp` stage | `GET /applications/{id}/whatsapp` | stops returning `404` (invite opened the conversation); then poll for new `messages[]` while `state ∈ {awaiting_interest, asking_questions}` |
| Talent-pool upload (`enqueued: true`) | `GET /talent-pool/search?q=…` | the candidate starts appearing in results (their CV finished embedding) |
| Talent-pool source | `GET /applications/{application_id}` | same as public apply — `pipeline_status.similarity === "ok"` |

> For lists, the denormalized `similarity_score` / `hard_filter_score` columns
> on `ApplicationListItem` let you poll the **list** endpoint and refresh row
> badges without fetching each detail.

---

## Polling recipe (Next.js)

Keep it simple: short interval, bounded attempts, stop on a terminal condition.

```ts
import type { ApplicationDetail } from "@/lib/kabil/types";
import { api } from "@/lib/kabil/client";

/** Poll until `done(detail)` is true, or attempts run out. */
async function pollApplication(
  id: string,
  token: string,
  done: (a: ApplicationDetail) => boolean,
  { intervalMs = 2000, maxAttempts = 30 } = {},
): Promise<ApplicationDetail> {
  for (let i = 0; i < maxAttempts; i++) {
    const a = await api<ApplicationDetail>(`/applications/${id}`, { token });
    if (done(a)) return a;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error("timed out waiting for pipeline");
}

// CV pipeline finished (similarity landed):
await pollApplication(id, token, (a) => a.pipeline_status.similarity === "ok");

// Hard-filter score landed:
await pollApplication(id, token, (a) => a.hard_filter_score !== null);
```

**With TanStack Query**, the same idea via `refetchInterval`:

```ts
useQuery({
  queryKey: ["application", id],
  queryFn: () => api<ApplicationDetail>(`/applications/${id}`, { token }),
  refetchInterval: (q) =>
    q.state.data?.pipeline_status.similarity === "ok" ? false : 2000,
});
```

**Guidance**
- Interval ~1.5–3s; back off after the first ~30s. CV processing is typically a
  few seconds but depends on provider latency.
- Always render an explicit "processing" state — `parsed_profile` is `{}`,
  scores are `null`, and `pipeline_status` keys may be absent until steps run.
- Surface `failed` steps with their `{key}_error` so HR knows a CV needs a
  manual look (e.g. a corrupt PDF), and offer a rescore where applicable.
- Don't block the whole UI on polling — let HR keep working and update badges in
  place.
