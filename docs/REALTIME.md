# Realtime Updates (Server-Sent Events)

Some Kabil.ai results land **after** the request that triggered them returns —
a hard-filter score computed when HR moves a candidate to L1, an authenticity
or similarity score from the CV pipeline, an inbound WhatsApp reply. Instead of
polling the GET endpoint until the value appears (see
[ASYNC_AND_POLLING.md](./ASYNC_AND_POLLING.md)), the backend **pushes a nudge**
over a **Server-Sent Events (SSE)** stream and the UI refetches just the
affected resource.

Polling still works and is the fallback — realtime is a latency optimization,
not a new source of truth.

---

## The contract

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/events/ticket` | Bearer | Mint a short-lived, **single-use** ticket for opening the stream. |
| `GET` | `/events/stream?ticket=…` | ticket | The SSE stream. Relays every event on the shared HR channel. |

Both return **`404`** when realtime is disabled on the backend
(`sse_enabled=False`) — treat that as "realtime off, keep polling".

**Why a ticket?** The browser `EventSource` API can't send an `Authorization`
header, and putting the Bearer token in the URL would leak it into proxy/access
logs. So an authed call trades the token for an opaque ticket
(`{ ticket, stream_url }`), and the stream is opened with the ticket in the
query string. The ticket is consumed on connect (single-use, ~60s TTL).

### Event shape — *pointer, not payload*

Every message is a compact envelope. **It never contains a score, a breakdown,
or message text** — only enough to tell the UI *what to refetch*:

```json
{ "event": "hard_filter", "application_id": "uuid", "job_id": "uuid", "state": "ok" }
```

| Field | Notes |
|---|---|
| `event` | One of `hard_filter`, `authenticity`, `similarity`, `whatsapp_message`. Also the SSE `event:` field, so you can `addEventListener` per kind. |
| `application_id` | Which application to refresh. Always present. |
| `job_id` | The owning job (lets a board-scoped view cheaply ignore others). |
| `state` | `"ok"` / `"failed"` for score events; absent for `whatsapp_message`. |

On any event, **refetch the named resource through the normal authed REST
endpoint** — the envelope is a pointer, the REST response is the truth.

### What does *not* come over SSE

- **Job pipeline** completion (`draft → open` → `ready_for_applications`). There
  is no job event; keep polling `GET /jobs/{id}` (`useJob({ poll: true })`).
- **Talent-pool** embed/search readiness. Keep the search-poll recipe.

---

## How the frontend consumes it

```
lib/api/events.js              ← transport: ticket → EventSource → reconnect
lib/realtime/RealtimeContext.js ← provider: event → React Query invalidation + status
app/(protected)/layout.js       ← mounts <RealtimeProvider> once, past the auth gate
lib/kabil/queries.js            ← polling gated on useRealtimeOffline()
```

1. **One stream per tab.** `RealtimeProvider` is mounted inside the authed HR
   shell, so a Bearer token always exists when it mints the first ticket. It
   opens exactly one stream for the whole console.
2. **Event → invalidation.** Each envelope invalidates the affected query keys:
   `["application", id]` and `["applications"]` (list badges read denormalized
   score columns); a `whatsapp_message` also invalidates `["whatsapp", id]`.
   Invalidation refetches only the **active** queries — so just what's on screen
   hits the network.
3. **Reconnection.** Because the ticket is single-use, the native `EventSource`
   auto-reconnect (same URL, spent ticket) can't work. On any error the client
   closes, mints a **fresh ticket**, and reconnects with capped exponential
   backoff (1s → 30s). A `404` on the ticket means realtime is off → it stops
   and the polling fallback carries the session.
4. **Polling fallback.** `useApplication` and `useWhatsAppConversation` poll
   **only while the stream is offline** (`useRealtimeOffline()`). When the
   stream is `open`, the timer is off and SSE invalidation drives refetches;
   if the stream drops, polling resumes automatically until it recovers.
5. **Connection indicator.** The TopBar shows a small dot — green (pulsing) when
   live, amber while connecting/reconnecting, hidden when realtime is disabled.

### Config

| Env var | Default | Effect |
|---|---|---|
| `NEXT_PUBLIC_KABIL_REALTIME` | _(unset = on)_ | Set to `false` to force polling-only (never opens a stream). |
| `NEXT_PUBLIC_KABIL_API` | `http://localhost:8000` | Backend origin; the stream is opened at `${API}/events/stream`. |

The frontend origin must be in the backend's CORS allow-list (same requirement
as every other call).

---

## Building your own client

If you're not using this repo's hooks, the minimal flow is:

```ts
// 1. Mint a single-use ticket (Bearer-authed).
const { ticket } = await api<{ ticket: string }>("/events/ticket", {
  method: "POST",
  token,
});

// 2. Open the stream with the ticket (no header — EventSource can't send one).
const es = new EventSource(`${API}/events/stream?ticket=${encodeURIComponent(ticket)}`);

// 3. React to each pointer envelope by refetching the named application.
for (const type of ["hard_filter", "authenticity", "similarity", "whatsapp_message"]) {
  es.addEventListener(type, (e) => {
    const { application_id } = JSON.parse(e.data);
    refetchApplication(application_id); // your REST call
  });
}

// 4. The ticket is spent on connect — on error, close, mint a NEW ticket, and
//    reconnect with backoff. Don't rely on the native auto-reconnect.
es.onerror = () => { es.close(); reconnectWithFreshTicket(); };
```

**Guidance**
- Open the stream *after* an initial REST fetch seeds the view; the stream only
  tells you *when to refetch*, not the current state.
- Keep the polling code — fall back to it on disconnect/timeout, and for the
  job pipeline and talent-pool readiness which have no events.
- Never expect a value on the stream. If you find yourself reading `state` to
  *display* something, refetch instead — the envelope is intentionally thin.
