/**
 * Realtime HR event stream (Server-Sent Events) — the browser side of the
 * backend's `events:hr` channel. See docs/REALTIME.md.
 *
 * The backend pushes tiny *pointer* envelopes (`{ event, application_id,
 * job_id?, state? }`) — never scores or PII — whenever a slow async result
 * lands (hard-filter / authenticity / similarity score, or an inbound WhatsApp
 * message). The UI reacts by refetching just the affected resource through the
 * normal authed REST endpoints (TanStack Query invalidation). This module is
 * the transport only; the event→refetch mapping lives in
 * lib/realtime/RealtimeContext.js.
 *
 * Why a ticket and not the Bearer token: the browser `EventSource` API can't
 * send an `Authorization` header, so we trade the token for a short-lived,
 * single-use ticket (`POST /events/ticket`) and open the stream with it in the
 * query string (`GET /events/stream?ticket=…`). Because the ticket is consumed
 * on connect, the native `EventSource` auto-reconnect (which reuses the same
 * URL) would fail — so we disable it: on any error we close, mint a *fresh*
 * ticket, and reconnect ourselves with capped exponential backoff.
 */

import { BASE_URL, apiJson } from "./client";
import { APP_EVENT_TYPES } from "@/lib/kabil/constants";

/**
 * Master switch. Realtime is on unless explicitly disabled, so a missing env
 * var (the common case) keeps the live stream working. Set
 * `NEXT_PUBLIC_KABIL_REALTIME=false` to force the polling-only fallback.
 */
export const REALTIME_ENABLED =
  process.env.NEXT_PUBLIC_KABIL_REALTIME !== "false";

/** Connection-state values surfaced to the UI via `onStatus`. */
export const STREAM_STATUS = {
  CONNECTING: "connecting",
  OPEN: "open",
  OFFLINE: "offline", // dropped; a reconnect is scheduled
  DISABLED: "disabled", // off by config, or the backend has SSE turned off
};

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 1_000;

/**
 * Open the realtime stream. Returns a handle with `close()` — call it on
 * unmount. Safe to call only in the browser (guards `window`/`EventSource`).
 *
 * @param {object} opts
 * @param {(event: object) => void} opts.onEvent  - parsed envelope per message
 * @param {(status: string) => void} [opts.onStatus] - connection-state changes
 * @param {() => void} [opts.onOpen] - fired each time the stream (re)connects,
 *   so the consumer can catch up on events missed while it was disconnected
 * @returns {{ close: () => void }}
 */
export const openEventStream = ({ onEvent, onStatus, onOpen }) => {
  const setStatus = (s) => onStatus?.(s);

  if (
    !REALTIME_ENABLED ||
    typeof window === "undefined" ||
    typeof EventSource === "undefined"
  ) {
    setStatus(STREAM_STATUS.DISABLED);
    return { close: () => {} };
  }

  let es = null;
  let closed = false;
  let attempt = 0;
  let reconnectTimer = null;

  const scheduleReconnect = () => {
    if (closed) return;
    attempt += 1;
    // 1s, 2s, 4s … capped at 30s. Heartbeats keep a healthy stream open, so a
    // reconnect only fires on a genuine drop.
    const delay = Math.min(
      MAX_BACKOFF_MS,
      BASE_BACKOFF_MS * 2 ** Math.min(attempt - 1, 5),
    );
    reconnectTimer = setTimeout(connect, delay);
  };

  const handleMessage = (e) => {
    try {
      onEvent?.(JSON.parse(e.data));
    } catch {
      // A malformed payload must not kill the stream; just drop it.
    }
  };

  async function connect() {
    if (closed) return;
    setStatus(attempt === 0 ? STREAM_STATUS.CONNECTING : STREAM_STATUS.OFFLINE);

    let ticket;
    try {
      ({ ticket } = await apiJson.post("/events/ticket"));
    } catch (err) {
      // 404 → the backend has realtime disabled (sse_enabled=False). Stop
      // trying and let the polling fallback take over for the whole session.
      if (err?.status === 404) {
        setStatus(STREAM_STATUS.DISABLED);
        return;
      }
      // 401 is handled by the axios interceptor (clears session, redirects).
      // Anything else (network blip) → back off and retry.
      scheduleReconnect();
      return;
    }
    if (closed || !ticket) return;

    es = new EventSource(
      `${BASE_URL}/events/stream?ticket=${encodeURIComponent(ticket)}`,
    );

    es.onopen = () => {
      attempt = 0;
      setStatus(STREAM_STATUS.OPEN);
      // The stream only nudges; events that fired before this (re)connect were
      // never delivered. Catch up by refetching the affected views now — this
      // covers the gap between the initial REST seed and the first connect, and
      // every reconnect after a drop.
      onOpen?.();
    };

    // The backend sets the SSE `event:` field to the envelope's type, so we
    // listen per kind; `onmessage` is a safety net for any default-typed event.
    for (const type of APP_EVENT_TYPES) es.addEventListener(type, handleMessage);
    es.onmessage = handleMessage;

    es.onerror = () => {
      // Our ticket is already spent, so the native reconnect can't succeed —
      // tear down and reconnect with a fresh ticket on a backoff.
      if (es) {
        es.close();
        es = null;
      }
      if (!closed) scheduleReconnect();
    };
  }

  connect();

  return {
    close: () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (es) {
        es.close();
        es = null;
      }
      setStatus(STREAM_STATUS.DISABLED);
    },
  };
};
