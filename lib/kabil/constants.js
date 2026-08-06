/**
 * Closed sets from the backend, as JS for dropdowns / labels / badges.
 * Mirrors docs/ENUMS.md. Keep values in sync with the backend.
 */

export const EMPLOYMENT_TYPES = ["permanent", "contract", "temporary"];
export const WORK_MODES = ["onsite", "hybrid", "remote"];
export const NOTICE_PERIODS = ["any", "immediate", "30d", "60d", "90d"];
export const VISA_REQUIREMENTS = ["any", "citizen_or_resident", "sponsorship_offered"];

/* ── Realtime events (SSE) ───────────────────────────────────────────────
 * The `event:` field on each Server-Sent Event from `/events/stream`. Mirrors
 * the backend `AppEventType` enum; `state` (on score events) is "ok"/"failed".
 * See docs/REALTIME.md. */
export const APP_EVENT_TYPES = [
  "hard_filter",
  "authenticity",
  "similarity",
  "whatsapp_message",
];

export const JOB_STATUSES = ["draft", "open", "closed"];

/* ── Dashboard ───────────────────────────────────────────────────────────── */

/** Dashboard Performance-table health verdict (computed server-side). */
export const JOB_HEALTHS = ["healthy", "at_risk", "unhealthy"];

/**
 * The four Candidate Pipeline funnel columns, in funnel order, with a
 * recruiter-facing label and the accent color drawn on each bar. The backend
 * collapses the five `ApplicationStage` values into these (sourcing =
 * vector_screen + hard_filter, screening = whatsapp, interview, final_shortlist
 * = done) and returns `by_bucket` zero-filled across every key.
 */
export const PIPELINE_BUCKETS = [
  { bucket: "sourcing", label: "Sourcing", accent: "#EF9F27" },
  { bucket: "screening", label: "Screening", accent: "#0F6E56" },
  { bucket: "interview", label: "Interview", accent: "#37a07d" },
  { bucket: "final_shortlist", label: "Final Shortlist", accent: "#C87F14" },
];

/** Default count of upcoming interviews the dashboard card shows ("top 3"). */
export const DASHBOARD_UPCOMING_PREVIEW = 3;
/** Hard cap the "View all" interviews dialog requests (mirrors the backend). */
export const DASHBOARD_UPCOMING_MAX = 200;

/** Job health → recruiter-facing label + MUI palette color (Chip `color`). */
export const jobHealthChip = (health) => {
  switch (health) {
    case "unhealthy":
      return { label: "Unhealthy", color: "error" };
    case "at_risk":
      return { label: "At Risk", color: "warning" };
    case "healthy":
      return { label: "Healthy", color: "success" };
    default:
      return { label: humanize(health), color: "default" };
  }
};
export const APPLICATION_STAGES = [
  "vector_screen",
  "hard_filter",
  "whatsapp",
  "interview",
  "done",
];
export const APPLICATION_STATUSES = ["active", "rejected", "accepted", "archived"];

export const APPLICATION_LIST_ORDERS = [
  "-created_at",
  "created_at",
  "-similarity_score",
  "similarity_score",
  "-hard_filter_score",
  "hard_filter_score",
  "-stage_updated_at",
];

/** Forward-only stage transitions (source of truth: backend stages enum). */
export const NEXT_STAGE = {
  vector_screen: "hard_filter",
  hard_filter: "whatsapp",
  whatsapp: "interview",
  interview: "done",
  done: null,
};

/**
 * Allowed status transitions via `PATCH /applications/{id}/status`. `archived`
 * is intentionally absent as both a source and a target: it's set only by
 * move-to-pool (never a manual status change) and is terminal for the pipeline,
 * so there are no transitions to offer for an archived stint.
 */
export const NEXT_STATUSES = {
  active: ["rejected", "accepted"],
  rejected: ["active"],
  accepted: [],
  archived: [],
};

export const BULK_REJECTION_LABELS = {
  not_pdf: "Not a valid PDF",
  too_large: "Exceeds 10 MB limit",
  duplicate_in_batch: "Duplicate file in this upload",
  parse_failed_no_contact: "Couldn't extract contact info",
  over_limit: "Exceeds the per-upload file limit",
};

/* ── CV Inbox ──────────────────────────────────────────────────────────── */

/** Where the recruiter sourced the CVs (UI metadata for the upload form). */
export const CV_SOURCES = [
  { value: "linkedin", label: "LinkedIn", emoji: "💼" },
  { value: "bayt", label: "Bayt", emoji: "🌐" },
  { value: "naukrigulf", label: "Naukrigulf", emoji: "🌐" },
  { value: "indeed", label: "Indeed", emoji: "🔎" },
  { value: "referral", label: "Referral", emoji: "🤝" },
  { value: "email", label: "Email", emoji: "✉️" },
  { value: "career_page", label: "Career page", emoji: "🏢" },
  { value: "other", label: "Other", emoji: "📎" },
];

/** The three-stage flow shown in the CV Inbox banner. */
export const CV_INBOX_STEPS = [
  { n: 1, label: "Upload", color: "#1C4A3E" },
  { n: 2, label: "AI Parses", color: "#EF9F27" },
  { n: 3, label: "Job Pipeline", color: "#0F6E56" },
];

export const CV_INBOX_HINT =
  "Sorting through data, chasing approvals, and tracking performance used to eat up your day. With QABIL, it all happens in seconds.";

/** `accept` attribute for the file picker — the backend only ingests PDFs. */
export const CV_ACCEPT = ".pdf,application/pdf";

export const PDPL_CONSENT_TEXT =
  "I confirm these candidates consented to their data being processed for recruitment purposes including AI-assisted screening with human review in line with applicable data protection laws.";

/* ── Pipeline board ──────────────────────────────────────────────────────── */

/**
 * Kanban columns: backend `ApplicationStage` → recruiter-facing label, a short
 * tag for the summary strip, and the accent color drawn along the column's top
 * edge. Order matches the forward-only pipeline.
 */
export const PIPELINE_COLUMNS = [
  { stage: "vector_screen", label: "Applied", short: "Applied", accent: "#0F6E56" },
  { stage: "hard_filter", label: "Screening", short: "Screening", accent: " #EF9F27" },
  { stage: "whatsapp", label: "Assessment", short: "Assessment", accent: "#2f7fd1" },
  { stage: "interview", label: "Interviewed", short: "Interviewed", accent: "#8155c9" },
  { stage: "done", label: "Shortlist", short: "Shortlist", accent: "#0F6E56" },
];

/**
 * Backend `ApplicationStage` → the exact recruiter-facing label shown on the
 * kanban board column. Falls back to `humanize` for stages without a column
 * (e.g. archived). Use this everywhere a stage is named so it always matches
 * the board.
 */
export const stageLabel = (stage) =>
  PIPELINE_COLUMNS.find((c) => c.stage === stage)?.label ?? humanize(stage);

/** Status filter chips above the board. `value: null` = show every candidate. */
export const PIPELINE_STATUS_FILTERS = [
  { value: "accepted", label: "Accepted", color: "success" },
  { value: "rejected", label: "Rejected", color: "error" },
];

/** Authenticity band → MUI palette color (for Chip/Alert `color` props). */
export const bandColor = (band) => {
  switch (band) {
    case "authentic":
      return "success";
    case "review":
      return "warning";
    case "fabricated":
      return "error";
    default:
      return "default";
  }
};

/** Job status → MUI palette color. */
export const jobStatusColor = (status) => {
  switch (status) {
    case "open":
      return "success";
    case "draft":
      return "default";
    case "inactive":
      return "error";
    case "archived":
    case "closed":
      return "default";
    default:
      return "default";
  }
};

/** Application status → MUI palette color. */
export const statusColor = (status) => {
  switch (status) {
    case "active":
      return "info";
    case "rejected":
      return "error";
    case "accepted":
      return "success";
    case "archived":
      // Moved back to the pool — a neutral, history-only state.
      return "default";
    default:
      return "default";
  }
};

/** Application status → recruiter-facing label. */
export const statusLabel = (status) => {
  switch (status) {
    case "archived":
      return "Moved to pool";
    default:
      return humanize(status);
  }
};

/**
 * Recruiter recommendation derived from a 0–100 score. Frontend heuristic that
 * reuses the documented authenticity-band thresholds (≥75 / ≥50 / <50).
 */
/**
 * Coerce a raw score from the API into a finite number, or null.
 * Tolerates strings with a trailing "%" (e.g. "85%") and non-numeric values.
 */
export const toScore = (raw) => {
  if (raw == null) return null;
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : null;
};

export const scoreBand = (raw) => {
  const score = toScore(raw);
  if (score == null) return { label: "Pending", color: "default" };
  if (score >= 75) return { label: "Advance", color: "success" };
  if (score >= 50) return { label: "Review", color: "warning" };
  return { label: "Hold", color: "error" };
};

/** scoreBand() palette name → the concrete brand hex used for every score
 *  progress bar / disc (green · gold · red · grey). Single source of truth so
 *  the pipeline card and the candidate dialog always render the same colour. */
export const bandHex = (band) =>
  ({ success: "#0F6E56", warning: "#EF9F27", error: "#d24a39" })[band] || "#9aa3a0";

/** Authenticity band → recruiter-facing label. */
export const authenticityLabel = (band) => {
  switch (band) {
    case "authentic":
      return "Authentic";
    case "review":
      return "Needs review";
    case "fabricated":
      return "Flagged";
    default:
      return "Unscored";
  }
};

/** Compact relative time, e.g. "11 days ago". */
export const timeAgo = (iso) => {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return "just now";
  const units = [
    ["year", 31536000000],
    ["month", 2592000000],
    ["week", 604800000],
    ["day", 86400000],
    ["hour", 3600000],
    ["minute", 60000],
  ];
  for (const [unit, ms] of units) {
    const n = Math.floor(diff / ms);
    if (n >= 1) return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  }
  return "just now";
};

/**
 * Friendly absolute date-time for a scheduled slot, e.g. "Mon 19 Jun, 10:00 AM".
 * Renders in the viewer's local zone. Returns "—" for a missing value.
 */
export const formatDateTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  }).format(d);
};

/** Human label for an enum-ish value: "vector_screen" -> "Vector screen". */
export const humanize = (value) => {
  if (!value) return "";
  return String(value)
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
};

export const PAGE_SIZE = 20;
export const MAX_PAGE_SIZE = 100;
export const BULK_MAX_FILES = 50;
export const CV_MAX_MB = 10;

/* ── WhatsApp screening (GET /applications/{id}/whatsapp) ────────────────── */

/** Conversation lifecycle. awaiting_interest → asking_questions → completed,
 *  or → declined on a "No". completed / declined are terminal. */
export const WHATSAPP_CONVERSATION_STATES = [
  "awaiting_interest",
  "asking_questions",
  "completed",
  "declined",
];

/** Who sent a transcript message. */
export const WHATSAPP_DIRECTIONS = ["outbound", "inbound"];

/** Transcript message payload kinds (open-ended on the backend). */
export const WHATSAPP_MESSAGE_TYPES = ["text", "interactive_buttons", "button_reply"];

/** Interest quick-reply button ids (appear in a button_reply's `button_id`). */
export const WHATSAPP_BUTTON_IDS = { YES: "interest_yes", NO: "interest_no" };

/** Conversation state → recruiter-facing label + MUI palette color. */
export const whatsappStateLabel = (state) => {
  switch (state) {
    case "awaiting_interest":
      return { label: "Awaiting interest", color: "info" };
    case "asking_questions":
      return { label: "Answering questions", color: "warning" };
    case "completed":
      return { label: "Completed", color: "success" };
    case "declined":
      return { label: "Declined", color: "error" };
    default:
      return { label: "Not started", color: "default" };
  }
};

/** Commitment verdict for the Eligibility Questions "Commitment" row — derived
 *  deterministically from how far the candidate engaged with screening:
 *  confirmed interest + finished -> Most Likely; still answering -> Possible;
 *  declined -> Unlikely; not yet started -> null (row shows "—"). */
export const commitmentVerdict = (state) => {
  switch (state) {
    case "completed":
      return "Most Likely";
    case "asking_questions":
      return "Possible";
    case "declined":
      return "Unlikely";
    default:
      return null;
  }
};

/** Notice-period days -> a glanceable human label ("Immediate", "3 Weeks"). */
export const formatNoticePeriod = (days) => {
  if (days == null || days < 0) return null;
  if (days === 0) return "Immediate";
  if (days % 30 === 0) {
    const months = days / 30;
    return `${months} Month${months > 1 ? "s" : ""}`;
  }
  if (days % 7 === 0) {
    const weeks = days / 7;
    return `${weeks} Week${weeks > 1 ? "s" : ""}`;
  }
  return `${days} Day${days > 1 ? "s" : ""}`;
};

/* ── Talent pool (/talent-pool/*) ────────────────────────────────────────── */

export const TALENT_POOL_SEARCH_MIN_LENGTH = 1;
export const TALENT_POOL_SEARCH_MAX_LENGTH = 1000;
export const TALENT_POOL_SEARCH_DEFAULT_LIMIT = 10;
export const TALENT_POOL_SEARCH_MAX_LIMIT = 50;

/**
 * Talent-pool search similarity is a plain 0–100 number (cosine, higher = closer)
 * — NOT a percentage string like application scores. Map it to a palette color
 * for the "match" chip.
 */
export const poolMatchColor = (score) => {
  const n = toScore(score);
  if (n == null) return "default";
  if (n >= 75) return "success";
  if (n >= 50) return "warning";
  return "default";
};

// Score-band thresholds for the talent-pool stat cards + Scores filter.
export const POOL_HIGH_SCORE = 80; // "High Score (80+)" card
export const POOL_READY_SCORE = 60; // "Ready to Match" floor (active & decent match)

/** "All scores" dropdown options (client-side filter over the 0–100 match). */
export const POOL_SCORE_FILTERS = [
  { value: "", label: "All scores" },
  { value: "high", label: "High (80+)" },
  { value: "medium", label: "Medium (60–79)" },
  { value: "low", label: "Low (<60)" },
];

/** True if `score` falls in the selected band ("" = no filter). */
export const inPoolScoreBand = (score, band) => {
  if (!band) return true;
  const n = toScore(score);
  if (n == null) return false;
  if (band === "high") return n >= POOL_HIGH_SCORE;
  if (band === "medium") return n >= POOL_READY_SCORE && n < POOL_HIGH_SCORE;
  if (band === "low") return n < POOL_READY_SCORE;
  return true;
};

/** Candidate-level authenticity band → Status chip (label + palette color). */
export const authenticityBandChip = (band) => {
  switch (band) {
    case "authentic":
      return { label: "Authentic", color: "success" };
    case "review":
      return { label: "Review", color: "warning" };
    case "fabricated":
      return { label: "Fabricated", color: "error" };
    default:
      return { label: "Unscored", color: "default" };
  }
};
