/**
 * TanStack Query hooks, one per backend endpoint we use. All requests go
 * through the axios instance in lib/api/client.js (token + error interceptors).
 *
 * Polling: for async pipelines we set `refetchInterval` and stop once the
 * target field/state lands (see docs/ASYNC_AND_POLLING.md). When the realtime
 * SSE stream is live, the backend pushes a nudge the moment a result lands and
 * the RealtimeProvider invalidates the affected query — so polling is gated on
 * `useRealtimeOffline()` and only runs as a fallback while the stream is down.
 * See docs/REALTIME.md.
 */
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiJson, apiUpload, apiPublic } from "@/lib/api/client";
import { useRealtimeOffline } from "@/lib/realtime/RealtimeContext";
import {
  TALENT_POOL_SEARCH_MIN_LENGTH,
  TALENT_POOL_SEARCH_DEFAULT_LIMIT,
  DASHBOARD_UPCOMING_PREVIEW,
} from "@/lib/kabil/constants";

/**
 * Mark every dashboard home-screen panel stale in one call. All five dashboard
 * queries (summary, performance, pipeline, upcoming-interviews, pending-feedback)
 * share the `["dashboard"]` key prefix, so this invalidates the lot. Call it
 * from any mutation that shifts counts, stages, statuses, jobs, or the talent
 * pool, so the dashboard reflects the action the moment it lands.
 */
const invalidateDashboard = (qc) => qc.invalidateQueries({ queryKey: ["dashboard"] });

/* ── Dashboard ─────────────────────────────────────────────────────────── */

/**
 * Workspace-wide aggregate counts for the HR home screen (jobs by status,
 * applications by stage/status, candidate + active talent-pool totals).
 * Read-only and cheap; refetched on window focus. There's no realtime nudge
 * for these rollups, so `refetchInterval` lets a caller opt into light polling
 * (pass `false` to disable). The `by_status` / `by_stage` maps are always
 * zero-filled across every enum value — render them directly.
 */
export const useDashboard = ({ refetchInterval = false } = {}) =>
  useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiJson.get("/dashboard"),
    refetchOnWindowFocus: true,
    refetchInterval,
  });

/**
 * Per-job Performance table (one row per non-draft job: candidates/shortlisted
 * counts, days_open, and a JobHealth verdict). Keyed under `["dashboard", …]`
 * so a broad `["dashboard"]` invalidation refreshes every home-screen panel.
 */
export const usePerformance = () =>
  useQuery({
    queryKey: ["dashboard", "performance"],
    queryFn: () => apiJson.get("/dashboard/performance"),
    refetchOnWindowFocus: true,
  });

/**
 * Candidate Pipeline funnel + Applied→Offer conversion. Pass a `jobId` to scope
 * to one job; omit (or pass null) for the workspace-wide "All Jobs" view.
 */
export const useCandidatePipeline = (jobId, { enabled = true } = {}) =>
  useQuery({
    queryKey: ["dashboard", "pipeline", jobId ?? "all"],
    queryFn: () =>
      apiJson.get(`/dashboard/pipeline${jobId ? `?job_id=${jobId}` : ""}`),
    enabled,
  });

/**
 * Nearest booked, future interviews (soonest first). `limit` defaults to the
 * dashboard card preview; the "View all" dialog requests a larger page. The
 * response carries `total` so the card knows whether more exist than shown.
 */
export const useUpcomingInterviews = ({
  limit = DASHBOARD_UPCOMING_PREVIEW,
  enabled = true,
} = {}) =>
  useQuery({
    queryKey: ["dashboard", "upcoming-interviews", limit],
    queryFn: () => apiJson.get(`/dashboard/upcoming-interviews?limit=${limit}`),
    enabled,
  });

/** Applications stalled at the interview stage past the feedback SLA (oldest first). */
export const usePendingFeedback = () =>
  useQuery({
    queryKey: ["dashboard", "pending-feedback"],
    queryFn: () => apiJson.get("/dashboard/pending-feedback"),
    refetchOnWindowFocus: true,
  });

/* ── Jobs ──────────────────────────────────────────────────────────────── */

/**
 * Paginated jobs list. `order` picks the backend sort: omit for the default
 * (`updated_at` desc — most recently touched first, what the Jobs list page
 * wants) or pass `"title"` for the canonical alphabetical order shared by every
 * job-selector dropdown so they all list jobs identically.
 */
export const useJobs = ({ status, search, order, page = 1, pageSize = 20 } = {}) => {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (search) params.set("search", search);
  if (order) params.set("order", order);
  params.set("page", page);
  params.set("page_size", pageSize);
  return useQuery({
    queryKey: ["jobs", { status, search, order, page, pageSize }],
    queryFn: () => apiJson.get(`/jobs?${params.toString()}`),
  });
};

export const useJob = (jobId, { poll = false } = {}) =>
  useQuery({
    queryKey: ["job", jobId],
    queryFn: () => apiJson.get(`/jobs/${jobId}`),
    enabled: !!jobId,
    // While the job pipeline runs, poll until it's ready for applications.
    refetchInterval: (q) =>
      poll && q.state.data && !q.state.data.ready_for_applications ? 2500 : false,
  });

export const useCreateJob = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) => apiJson.post("/jobs", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      invalidateDashboard(qc);
    },
  });
};

/**
 * AI JD Builder: draft a job description from the Role-Basics fields. Stateless
 * — no job is created — so there's nothing to invalidate. Pass `regenerate: true`
 * to bypass the backend's Claude cache for a fresh draft on a repeat click.
 */
export const useGenerateJobDescription = () =>
  useMutation({
    mutationFn: ({ spec, regenerate = false }) =>
      apiJson.post(
        `/jobs/generate-description${regenerate ? "?regenerate=true" : ""}`,
        spec,
      ),
  });

export const useUpdateJobStatus = (jobId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (status) => apiJson.patch(`/jobs/${jobId}/status`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["job", jobId] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      invalidateDashboard(qc);
    },
  });
};

/**
 * Open a job by id (`draft → open`). This enqueues the job pipeline — embed the
 * JD and AI-generate the WhatsApp screening questions — so it's the trigger the
 * create wizard fires before polling `useJob` for the generated questions.
 */
export const useOpenJob = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId) => apiJson.patch(`/jobs/${jobId}/status`, { status: "open" }),
    onSuccess: (_data, jobId) => {
      qc.invalidateQueries({ queryKey: ["job", jobId] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      invalidateDashboard(qc);
    },
  });
};

/** Replace a job's full ordered WhatsApp question list (1..10, unique id/order). */
export const useSaveWhatsAppQuestions = (jobId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (questions) =>
      apiJson.patch(`/jobs/${jobId}/whatsapp-questions`, { questions }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job", jobId] }),
  });
};

/**
 * A job's ordered WhatsApp question list (`{ questions: [...] }`). Read-only —
 * used to recover each screening answer's `source_field` (commitment/salary/…)
 * by joining the conversation's `answers[].question_id` back to the question
 * that produced it. Questions don't change once a job is open, so this is a
 * plain cached read; gate with `enabled` so it only fires once a candidate has
 * actually reached the screening (WhatsApp) stage.
 */
export const useWhatsAppQuestions = (jobId, { enabled = true } = {}) =>
  useQuery({
    queryKey: ["whatsapp-questions", jobId],
    queryFn: () => apiJson.get(`/jobs/${jobId}/whatsapp-questions`),
    enabled: !!jobId && enabled,
  });

/* ── Applications ──────────────────────────────────────────────────────── */

export const useApplications = (
  jobId,
  { stage, status, order = "-created_at", page = 1, pageSize = 20 } = {},
) => {
  const realtimeOffline = useRealtimeOffline();
  const params = new URLSearchParams();
  if (stage) params.set("stage", stage);
  if (status) params.set("status", status);
  params.set("order", order);
  params.set("page", page);
  params.set("page_size", pageSize);
  return useQuery({
    queryKey: ["applications", jobId, { stage, status, order, page, pageSize }],
    queryFn: () => apiJson.get(`/jobs/${jobId}/applications?${params.toString()}`),
    enabled: !!jobId,
    refetchInterval: (q) => {
      // We only ever poll while some active card is still awaiting the score for
      // its current stage. The list item carries no `pipeline_status`, so we key
      // off the denormalized score columns: a card sits in
      // `hard_filter`/`vector_screen` with a null score until the async scoring
      // lands.
      const items = q.state.data?.items ?? [];
      const awaitingScore = items.some(
        (a) =>
          a.status === "active" &&
          ((a.stage === "hard_filter" && a.hard_filter_score == null) ||
            (a.stage === "vector_screen" && a.similarity_score == null)),
      );
      if (!awaitingScore) return false;
      // When the stream is offline, polling is the only way the score lands, so
      // poll briskly. When it's live, SSE invalidation refreshes the list the
      // moment a score is pushed — but a dropped nudge (an event that fires
      // during the connect/reconnect window is lost while the stream still
      // reports "open") would otherwise strand a card on "Pending" forever,
      // since the detail endpoint refreshes on dialog-open but the list does
      // not. A slow safety poll guarantees the board converges either way.
      return realtimeOffline ? 3000 : 10000;
    },
  });
};

/**
 * Application detail. Pass `poll: true` to keep refetching while a pipeline /
 * scoring run is in flight (similarity not yet "ok").
 */
export const useApplication = (appId, { poll = false } = {}) => {
  const realtimeOffline = useRealtimeOffline();
  return useQuery({
    queryKey: ["application", appId],
    queryFn: () => apiJson.get(`/applications/${appId}`),
    enabled: !!appId,
    refetchInterval: (q) => {
      // When the live stream is up, SSE invalidation drives refetches — skip
      // the timer. Poll only as a fallback while realtime is offline.
      if (!poll || !realtimeOffline) return false;
      const a = q.state.data;
      if (!a) return 2500;
      const ps = a.pipeline_status || {};
      // CV pipeline (vector screen) still running?
      const cvPending = ps.similarity !== "ok" && ps.similarity !== "failed";
      // Hard-filter scoring still running — entered L1 but the score hasn't
      // landed yet. Done signal: hard_filter_score set, or pipeline_status
      // .hard_filter terminal. Without this the timer stops the moment the CV
      // pipeline finishes and never waits for the L1 score.
      const hardFilterPending =
        a.stage === "hard_filter" &&
        a.hard_filter_score == null &&
        ps.hard_filter !== "ok" &&
        ps.hard_filter !== "failed";
      return cvPending || hardFilterPending ? 2500 : false;
    },
  });
};

export const useAuditLog = (appId) =>
  useQuery({
    queryKey: ["audit-log", appId],
    queryFn: () => apiJson.get(`/applications/${appId}/audit-log`),
    enabled: !!appId,
  });

/**
 * A candidate's cross-job activity feed — every audit-log entry across all
 * their applications (stage/status moves, move-to-pool, sourcing, interview
 * marks, contact edits) plus the candidate-level talent-pool rows, newest-first
 * and enriched with the job each entry belongs to. Powers the "Recent Activity"
 * section on the candidate dialog's Candidate Profile tab.
 */
export const useCandidateActivity = (candidateId, { enabled = true } = {}) =>
  useQuery({
    queryKey: ["candidate-activity", candidateId],
    queryFn: () => apiJson.get(`/talent-pool/candidates/${candidateId}/activity`),
    enabled: !!candidateId && enabled,
  });

/**
 * HR-facing WhatsApp screening transcript for one application. The backend 404s
 * with `whatsapp_conversation_not_found` until the application has entered the
 * `whatsapp` stage and the invite has opened a conversation — we treat that as
 * "no conversation yet" (resolve to `null`) rather than an error. While the
 * conversation is live (not completed/declined) we poll so new replies appear.
 */
export const useWhatsAppConversation = (appId, { enabled = true, poll = false } = {}) => {
  const realtimeOffline = useRealtimeOffline();
  return useQuery({
    queryKey: ["whatsapp", appId],
    queryFn: async () => {
      try {
        return await apiJson.get(`/applications/${appId}/whatsapp`);
      } catch (err) {
        if (err?.status === 404) return null;
        throw err;
      }
    },
    enabled: !!appId && enabled,
    refetchInterval: (q) => {
      // A `whatsapp_message` SSE event refetches this query on each inbound
      // reply; only poll as a fallback while the stream is offline.
      if (!poll || !realtimeOffline) return false;
      const c = q.state.data;
      // No conversation yet: keep checking so it appears once the invite opens.
      if (!c) return 4000;
      const terminal = c.state === "completed" || c.state === "declined";
      return terminal ? false : 4000;
    },
  });
};

export const useUpdateStage = (appId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ stage, reason }) =>
      apiJson.patch(`/applications/${appId}/stage`, { stage, reason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["candidate-activity"] });
      invalidateDashboard(qc);
    },
  });
};

export const useUpdateStatus = (appId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ status, reason }) =>
      apiJson.patch(`/applications/${appId}/status`, { status, reason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["candidate-activity"] });
      invalidateDashboard(qc);
    },
  });
};

/**
 * Edit the candidate's contact details (name / email / phone) through an
 * application. The backend only accepts this while the application is in the
 * `vector_screen` / `hard_filter` stage — from `whatsapp` onward it 422s with
 * `candidate_contact_locked`, since the screening flow owns the contact channel.
 * The candidate row is shared, so the edit propagates to every application that
 * person holds — hence we invalidate the broad `applications` list (name/email
 * are shown there) alongside this application's detail.
 */
export const useUpdateCandidateContact = (appId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ full_name, email, phone }) =>
      apiJson.patch(`/applications/${appId}/candidate`, { full_name, email, phone }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["candidate-history"] });
      qc.invalidateQueries({ queryKey: ["candidate-activity"] });
      // Candidate name shows on the interview / pending-feedback panels.
      invalidateDashboard(qc);
    },
  });
};

/**
 * Record HR's post-interview evaluation (a 0–100 mark + optional comment).
 * The backend only accepts this while the application is at the `interview`
 * stage (422 `interview_feedback_wrong_stage` otherwise). Re-submitting
 * overwrites the prior value. Invalidates this application's detail (the score
 * is shown there) plus the list + dashboard (the candidate surfaces on the
 * pending-feedback panel).
 */
export const useSubmitInterviewFeedback = (appId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ score, comment }) =>
      apiJson.patch(`/applications/${appId}/interview-feedback`, { score, comment }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["candidate-activity"] });
      invalidateDashboard(qc);
    },
  });
};

/**
 * Board-level stage move: the application id is passed per call (not bound to a
 * hook instance), so a single mutation can serve every draggable card on the
 * pipeline board.
 */
export const useMoveStage = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ appId, stage, reason }) =>
      apiJson.patch(`/applications/${appId}/stage`, { stage, reason }),
    onSuccess: (_data, { appId }) => {
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      invalidateDashboard(qc);
    },
  });
};

/** Board-level status change (appId per call) — used to reactivate rejects. */
export const useSetStatus = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ appId, status, reason }) =>
      apiJson.patch(`/applications/${appId}/status`, { status, reason }),
    onSuccess: (_data, { appId }) => {
      qc.invalidateQueries({ queryKey: ["application", appId] });
      qc.invalidateQueries({ queryKey: ["applications"] });
      invalidateDashboard(qc);
    },
  });
};

/* ── Public apply (anonymous candidate) ────────────────────────────────────── */

/**
 * Public job details by slug for the candidate-facing apply page. Anonymous —
 * no token. The backend returns `410` for an inactive/unknown slug; we don't
 * retry that (a closed posting won't reopen on a refresh).
 */
export const usePublicJob = (slug) =>
  useQuery({
    queryKey: ["public-job", slug],
    queryFn: () => apiPublic.get(`/public/apply/${slug}`),
    enabled: !!slug,
    retry: (count, err) => err?.status !== 410 && count < 1,
    staleTime: 60_000,
  });

/**
 * Submit a candidate application (multipart). Privacy by design: the backend
 * returns an identical `{ reference_number }` for new, duplicate and honeypot
 * submissions, so the UI must NOT branch on "already applied".
 */
export const usePublicApply = (slug) =>
  useMutation({
    mutationFn: ({ pdf, full_name, email, phone, consent, honeypot }) => {
      const fd = new FormData();
      fd.append("pdf", pdf);
      fd.append("full_name", full_name);
      fd.append("email", email);
      fd.append("phone", phone);
      fd.append("consent", consent ? "true" : "false");
      fd.append("honeypot", honeypot ?? "");
      return apiPublic.upload(`/public/apply/${slug}/upload`, fd);
    },
  });

export const useRescore = (appId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (type) => apiJson.post(`/applications/${appId}/rescore?type=${type}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["application", appId] }),
  });
};

export const useBulkUpload = (jobId) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (files) => {
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      return apiUpload(`/jobs/${jobId}/applications/bulk-upload`, fd);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["applications", jobId] });
      invalidateDashboard(qc);
    },
  });
};

/* ── Talent pool ───────────────────────────────────────────────────────── */

/** Browse the pool: paginated, newest-first. `activeOnly` hides expired entries. */
export const useTalentPool = ({
  activeOnly = true,
  page = 1,
  pageSize = 20,
  enabled = true,
} = {}) => {
  const params = new URLSearchParams();
  params.set("active_only", activeOnly);
  params.set("page", page);
  params.set("page_size", pageSize);
  return useQuery({
    queryKey: ["talent-pool", { activeOnly, page, pageSize }],
    queryFn: () => apiJson.get(`/talent-pool?${params.toString()}`),
    enabled,
  });
};

/**
 * Semantic search over the pool. Disabled until `q` clears the min length so we
 * don't fire on an empty box. Keeps the previous page of hits on screen while a
 * new query is in flight (placeholderData) to avoid a flash of empty results.
 */
export const useTalentPoolSearch = (
  q,
  { jobId = null, limit = TALENT_POOL_SEARCH_DEFAULT_LIMIT, activeOnly = true, enabled = true } = {},
) => {
  const query = (q ?? "").trim();
  const byJob = Boolean(jobId);
  const params = new URLSearchParams();
  // job_id ranks the pool by that job's JD embedding ("relevant to this job");
  // otherwise the free-text query is embedded server-side.
  if (byJob) params.set("job_id", jobId);
  else params.set("q", query);
  params.set("limit", limit);
  params.set("active_only", activeOnly);
  return useQuery({
    queryKey: ["talent-pool-search", { q: byJob ? "" : query, jobId, limit, activeOnly }],
    queryFn: () => apiJson.get(`/talent-pool/search?${params.toString()}`),
    enabled: enabled && (byJob || query.length >= TALENT_POOL_SEARCH_MIN_LENGTH),
    placeholderData: (prev) => prev,
  });
};

/**
 * Add an existing candidate to the pool (idempotent on the candidate). Pass the
 * application's job as `sourceJobId` for provenance. Used from the pipeline's
 * candidate dialog to collect shortlisted people.
 */
export const useAddToPool = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ candidateId, sourceJobId }) =>
      apiJson.post("/talent-pool/entries", {
        candidate_id: candidateId,
        source_job_id: sourceJobId ?? null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["talent-pool"] });
      qc.invalidateQueries({ queryKey: ["talent-pool-search"] });
      // Overview's active talent-pool count.
      invalidateDashboard(qc);
    },
  });
};

/** Upload a single CV (PDF ≤10 MB) straight into the pool. */
export const useTalentPoolUpload = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file) => {
      const fd = new FormData();
      fd.append("file", file);
      return apiUpload("/talent-pool/upload", fd);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["talent-pool"] });
      qc.invalidateQueries({ queryKey: ["talent-pool-search"] });
      // Overview's candidate + active talent-pool counts.
      invalidateDashboard(qc);
    },
  });
};

/**
 * Source a pooled candidate onto a job — creates a fresh `vector_screen`
 * application (or returns the existing one via `already_existed`). Sourcing is
 * a *move*: the backend deactivates the candidate's pool entry, so we refetch
 * the pool too (the candidate drops out of it).
 */
export const useSourceToJob = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ candidateId, jobId }) =>
      apiJson.post("/talent-pool/source", { candidate_id: candidateId, job_id: jobId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["talent-pool"] });
      qc.invalidateQueries({ queryKey: ["talent-pool-search"] });
      invalidateDashboard(qc);
    },
  });
};

/**
 * Move a candidate out of a job's pipeline back into the talent pool. The
 * backend **soft-archives** the application (`status=archived`, retaining its
 * scores + WhatsApp transcript as cross-job history) and (re-)adds the
 * candidate to the pool — so we refetch the board's applications (the archived
 * one drops out of the default list), the pool, and the candidate's history
 * (a new archived stint now exists). The candidate can be sourced back onto the
 * same job afterwards with no "already applied" conflict; the archived stint
 * survives as history.
 */
export const useMoveToPool = () => {
  const qc = useQueryClient();
  return useMutation({
    // `reason` is optional HR justification, saved against this job stint and
    // shown later in the candidate's history. Omit it (or pass blank) to move
    // with no reason — the backend body is optional.
    mutationFn: ({ appId, reason }) =>
      apiJson.post(`/applications/${appId}/move-to-pool`, {
        reason: reason?.trim() || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["talent-pool"] });
      qc.invalidateQueries({ queryKey: ["talent-pool-search"] });
      qc.invalidateQueries({ queryKey: ["candidate-history"] });
      qc.invalidateQueries({ queryKey: ["candidate-activity"] });
      invalidateDashboard(qc);
    },
  });
};

/**
 * A candidate's full cross-job journey: one scored stint per application they
 * ever had (live, rejected, accepted, or archived), each with its
 * similarity/hard-filter scores + WhatsApp screening digest, plus
 * candidate-level authenticity and whether they're currently `in_pool`. This is
 * the view behind the talent pool's "History" action — it stitches together
 * every job a candidate passed through, including stints that were archived
 * back to the pool. Backend 404s (`candidate_not_found`) for an unknown id.
 */
export const useCandidateHistory = (candidateId, { enabled = true } = {}) =>
  useQuery({
    queryKey: ["candidate-history", candidateId],
    queryFn: () => apiJson.get(`/talent-pool/candidates/${candidateId}/history`),
    enabled: !!candidateId && enabled,
  });
