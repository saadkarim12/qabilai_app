/**
 * Realtime context — bridges the SSE stream (lib/api/events.js) to TanStack
 * Query. Mounted once inside the authed HR shell (app/(protected)/layout.js),
 * it opens a single stream per tab and, on each pointer envelope, invalidates
 * exactly the affected query keys so the open view refetches through the normal
 * authed REST endpoints. No score/PII ever rides the stream — see docs/REALTIME.md.
 *
 * The context also publishes the connection `status` so data hooks can keep
 * polling as a fallback while the stream is down and stop once it's live
 * (`useRealtimeOffline`).
 */
"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { openEventStream, STREAM_STATUS } from "@/lib/api/events";

// Default to "disabled" so a consumer rendered outside the provider (or before
// it connects) behaves exactly like today: polling stays on.
const RealtimeContext = createContext({ status: STREAM_STATUS.DISABLED });

/**
 * Translate one pointer envelope into query invalidations.
 *
 * Every event names an `application_id`, so we always refresh that application's
 * detail and any application *list* (board badges read denormalized
 * similarity/hard_filter columns). A `whatsapp_message` additionally refreshes
 * the transcript query. Invalidation marks the queries stale and refetches the
 * active ones — so only what's on screen actually hits the network.
 */
const applyEvent = (queryClient, evt) => {
  const appId = evt?.application_id;
  if (!appId) return;

  queryClient.invalidateQueries({ queryKey: ["application", appId] });
  queryClient.invalidateQueries({ queryKey: ["applications"] });

  if (evt.event === "whatsapp_message") {
    queryClient.invalidateQueries({ queryKey: ["whatsapp", appId] });
  }
};

export const RealtimeProvider = ({ children }) => {
  // The QueryClient is a stable singleton (created once in app/providers.js),
  // so closing over it here never re-opens the long-lived stream.
  const queryClient = useQueryClient();
  const [status, setStatus] = useState(STREAM_STATUS.CONNECTING);

  useEffect(() => {
    const handle = openEventStream({
      onEvent: (evt) => applyEvent(queryClient, evt),
      onStatus: setStatus,
      // On every (re)connect, refetch the score-bearing views so anything that
      // landed while we were disconnected — before the first connect, or during
      // a reconnect — is picked up immediately instead of waiting for the next
      // poll. These keys cover application detail + board list badges and the
      // WhatsApp transcript; invalidation only refetches what's on screen.
      onOpen: () => {
        queryClient.invalidateQueries({ queryKey: ["application"] });
        queryClient.invalidateQueries({ queryKey: ["applications"] });
        queryClient.invalidateQueries({ queryKey: ["whatsapp"] });
      },
    });
    return () => handle.close();
  }, [queryClient]);

  return (
    <RealtimeContext.Provider value={{ status }}>
      {children}
    </RealtimeContext.Provider>
  );
};

/** Current realtime connection status (see STREAM_STATUS). */
export const useRealtime = () => useContext(RealtimeContext);

/**
 * True when the live stream is NOT delivering events (connecting, dropped, or
 * disabled) — the signal data hooks use to decide whether to keep polling.
 * While the stream is `open`, returns false so polling backs off and SSE
 * invalidation drives refetches instead.
 */
export const useRealtimeOffline = () =>
  useContext(RealtimeContext).status !== STREAM_STATUS.OPEN;
