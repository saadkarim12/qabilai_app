# Kabil.ai — Frontend Integration Docs

Everything a frontend (Next.js + TypeScript) needs to build against the
Kabil.ai backend. Start here, then dive into the specific guide.

## What is Kabil.ai?

An AI-driven hiring-automation platform. HR posts jobs; candidates apply with a
CV via a public link; the backend parses, embeds, scores, and screens each CV
through async pipelines, surfacing an explainable verdict (relevancy + CV fit +
authenticity) so HR can triage. See the
[backend architecture](../KABIL_AI_ARCHITECTURE.md) for the full design.

## The docs

| Doc | What's in it |
|---|---|
| **[API_REFERENCE.md](./API_REFERENCE.md)** | Base URL, auth, error envelope, conventions, every endpoint with examples (incl. Talent Pool + WhatsApp transcript). |
| **[ENUMS.md](./ENUMS.md)** | Every closed set (stages, statuses, job descriptors, bands, WhatsApp states) + limits cheat sheet. |
| **[WORKFLOWS.md](./WORKFLOWS.md)** | End-to-end journeys with sequence diagrams (incl. WhatsApp screening + talent-pool sourcing) + the application state machine. |
| **[ASYNC_AND_POLLING.md](./ASYNC_AND_POLLING.md)** | What runs async, the pipelines, and how to poll for completion. |
| **[REALTIME.md](./REALTIME.md)** | The Server-Sent Events stream: ticket auth, the pointer envelopes, and how the FE turns events into refetches (with polling as fallback). |
| **[kabil-api.types.ts](./kabil-api.types.ts)** | Drop-in TypeScript types for every request/response shape. |

## Recently added (what's new for FE)

- **Public job view** — `GET /public/apply/{slug}` returns the candidate-facing
  job details so the apply page can render the role before upload.
- **WhatsApp screening** — moving an application to the `whatsapp` stage starts
  an automated interest-check + Q&A conversation; HR reads it via
  `GET /applications/{id}/whatsapp`. See WORKFLOWS §8.
- **Talent Pool** — `/talent-pool/*`: add/upload candidates, semantic search,
  and **source** a pooled candidate onto a job. See WORKFLOWS §9.
- **Realtime (SSE)** — `/events/ticket` + `/events/stream` push a nudge when a
  score or WhatsApp reply lands, so the UI refetches instantly instead of
  polling. See REALTIME.md.

## Also available (not in this folder)

- **Live OpenAPI docs** — `GET /docs` (Swagger UI) and `GET /openapi.json` on a
  running backend. Byte-exact source of truth; great for trying calls.
- **Postman collection** — [`../postman/`](../postman/). Import
  `kabil-backend.postman_collection.json` + `kabil-backend.postman_environment.json`.
  Covers every route incl. error cases, with environment variables wired up
  (set `base_url`, run **Login**, the token auto-populates).

## 60-second mental model

1. **Auth** — `POST /auth/login` → Bearer token (8h). Attach to every HR call.
2. **Job lifecycle** — create (`draft`) → open (async job pipeline) →
   `ready_for_applications` → share `public_slug`.
3. **Applications arrive** — candidate via `POST /public/apply/{slug}/upload`,
   or HR via bulk upload. Each CV runs an async pipeline.
4. **Triage** — list applications (sortable by score), open detail for the
   explainable `scores[]`, walk through stages, accept/reject.
5. **Screen on WhatsApp** — advance an application to the `whatsapp` stage to
   auto-send the candidate an interest check + screening questions; read the
   transcript at `GET /applications/{id}/whatsapp`.
6. **Talent pool** — keep & semantically search strong candidates, then source
   one onto a job to create a fresh, fully-scored application.
7. **Async everywhere** — `202`/`201` can mean *enqueued*; poll the GET endpoint
   and read `pipeline_status` + score fields (ASYNC_AND_POLLING.md), or subscribe
   to the SSE stream for an instant nudge when results land (REALTIME.md).

## Conventions in one glance

- Bearer auth; `Authorization: Bearer <token>`.
- JSON for most; `multipart/form-data` for uploads (don't set `Content-Type`).
- UUID strings, ISO-8601 UTC timestamps, `null` = unknown.
- Lists are paginated: `{ items, total, page, page_size }`.
- Errors are uniform: `{ error, message, details?, correlation_id }`.
- Every response has an `X-Correlation-ID` header — log it.

## Suggested setup in your Next.js app

```
lib/kabil/
  types.ts     ← copy of kabil-api.types.ts
  client.ts    ← typed fetch wrapper (see WORKFLOWS.md, Appendix A)
```

Set `NEXT_PUBLIC_KABIL_API` (e.g. `http://localhost:8000`). Make sure your
frontend origin is in the backend's CORS allow-list for local dev.
