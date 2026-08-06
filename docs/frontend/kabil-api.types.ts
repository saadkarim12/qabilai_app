/**
 * Kabil.ai Backend — TypeScript API types
 * ----------------------------------------
 * Hand-maintained mirror of the backend Pydantic schemas and enums.
 * Drop this into your Next.js app (e.g. `lib/kabil/types.ts`) and import
 * the types you need. Keep it in sync when the backend contract changes.
 *
 * Source of truth on the backend:
 *   - src/schemas/*.py        (request/response shapes)
 *   - src/enums/*.py          (closed sets / literals)
 *   - src/api/exception_handlers.py  (error envelope)
 *
 * Conventions
 *   - All ids are UUID strings.
 *   - All timestamps are ISO-8601 strings (UTC, e.g. "2026-06-08T12:34:56.789Z").
 *   - Money amounts (salary) are integers in the job's `currency`.
 *   - `null` means "not set / unknown"; absence of a key (rare) is noted inline.
 */

/* ──────────────────────────────────────────────────────────────────────────
 * Primitives
 * ────────────────────────────────────────────────────────────────────────── */

/** UUID v4 string, e.g. "3f8b...". */
export type UUID = string;
/** ISO-8601 datetime string in UTC. */
export type ISODateTime = string;

/* ──────────────────────────────────────────────────────────────────────────
 * Enums / closed sets
 * ────────────────────────────────────────────────────────────────────────── */

export type UserRole = "admin" | "hiring_manager";

export type JobStatus = "draft" | "open" | "inactive" | "archived" | "closed";
/** Accepted by PATCH /jobs/{id}/status (`draft` is server-set only). */
export type JobStatusPatch = "open" | "inactive" | "archived" | "closed";

export type EmploymentType = "permanent" | "contract" | "temporary";
export type WorkMode = "onsite" | "hybrid" | "remote";
export type NoticePeriod = "any" | "immediate" | "30d" | "60d" | "90d";
export type VisaRequirement = "any" | "citizen_or_resident" | "sponsorship_offered";

/** Pipeline stage an application sits in. Forward-only (see WORKFLOWS.md). */
export type ApplicationStage =
  | "vector_screen"
  | "hard_filter"
  | "whatsapp"
  | "interview"
  | "done";

/** Lifecycle flag, orthogonal to stage.
 *  `archived` is set only by move-to-pool (a history-preserving alternative to
 *  deletion): the application leaves the active pipeline but its scores +
 *  WhatsApp transcript are retained and surface in the candidate-history view.
 *  You cannot set it via PATCH /status; it's excluded from the job's active
 *  application list by default (pass `status=archived` to see archived stints). */
export type ApplicationStatus = "active" | "rejected" | "accepted" | "archived";

/** Score families recorded against an application. */
export type ScoreType = "similarity" | "hard_filter" | "authenticity";

/** Model / mechanism that produced a score (`model_used`). Open-ended on the
 *  backend (varchar); these are the values emitted today. */
export type ScoreModel =
  | "claude-opus"
  | "claude-sonnet"
  | "claude-haiku"
  | "openai-text-embedding-3-small"
  | "deterministic"
  | "blended"
  | (string & {}); // future-proof: backend may emit new values

/** Authenticity band derived from the score. authentic ≥75 / review ≥50 / fabricated <50. */
export type AuthenticityBand = "authentic" | "review" | "fabricated";

/** Dashboard Performance-table health verdict (computed, never persisted).
 *  Age-based on OPEN jobs, in working days (Mon-Fri). Precedence:
 *  unhealthy (open >20 working days) → at_risk (open >=18 working days) →
 *  healthy (younger open jobs, and any non-open job). */
export type JobHealth = "healthy" | "at_risk" | "unhealthy";

/** The four Candidate Pipeline funnel columns. The five raw ApplicationStage
 *  values collapse into these: sourcing = vector_screen + hard_filter,
 *  screening = whatsapp, interview = interview, final_shortlist = done. */
export type PipelineBucket =
  | "sourcing"
  | "screening"
  | "interview"
  | "final_shortlist";

/** order= query value for the applications list. `-` prefix = descending. */
export type ApplicationListOrder =
  | "-created_at"
  | "created_at"
  | "-similarity_score"
  | "similarity_score"
  | "-hard_filter_score"
  | "hard_filter_score"
  | "-stage_updated_at";

/** type= query value for POST /applications/{id}/rescore. */
export type RescoreType = "similarity" | "hard_filter";

/** Per-file rejection reason in a bulk upload. */
export type BulkUploadRejectionReason =
  | "not_pdf"
  | "too_large"
  | "duplicate_in_batch"
  | "parse_failed_no_contact";

/** The three buckets a WhatsApp screening question can use.
 *  `commitment` / `salary` tag the deterministic fixed questions (notice
 *  period, employment type, work mode, reason-for-move → `commitment`; salary
 *  expectation → `salary`); `background_validation` covers the fixed
 *  visa/residency question plus the AI-authored required + preferred skill
 *  verification questions (skills only). The specific topic lives in
 *  `subcategory`. */
export type WhatsAppQuestionCategory =
  | "commitment"
  | "salary"
  | "background_validation";

/* ──────────────────────────────────────────────────────────────────────────
 * Error envelope (every non-2xx response)
 * ────────────────────────────────────────────────────────────────────────── */

/** Machine-readable error codes the backend emits in `error`. */
export type ErrorCode =
  | "bad_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "method_not_allowed"
  | "conflict"
  | "unsupported_media_type"
  | "validation_error"
  | "rate_limited"
  | "http_error"
  | "internal_server_error"
  | (string & {}); // domain errors (KabilError) carry their own codes

export interface ApiError {
  error: ErrorCode;
  message: string;
  /** Present on 422 validation errors and some domain errors. */
  details?: unknown;
  /** Always present. Matches the X-Correlation-ID response header. */
  correlation_id: string;
  /** Dev/test only — never present in production. */
  exception?: string;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Pagination envelope
 * ────────────────────────────────────────────────────────────────────────── */

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Auth
 * ────────────────────────────────────────────────────────────────────────── */

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  /** Token expiry (8 hours after login). Use this to schedule re-auth. */
  expires_at: ISODateTime;
}

export interface MeResponse {
  id: UUID;
  email: string;
  full_name: string;
  role: UserRole;
  created_at: ISODateTime;
  last_login_at: ISODateTime | null;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Jobs
 * ────────────────────────────────────────────────────────────────────────── */

export interface JobCreateRequest {
  title: string;
  hiring_company: string;
  /** ISO-3166 alpha-2, uppercased server-side (e.g. "AE"). */
  country: string;
  city: string;
  employment_type: EmploymentType;
  work_mode: WorkMode;
  /** ISO-4217, 3 letters (e.g. "AED"). */
  currency: string;
  min_salary?: number | null;
  max_salary?: number | null;
  notice_period?: NoticePeriod | null;
  min_experience_years: number;
  required_skills?: string[];
  preferred_skills?: string[];
  visa_requirement?: VisaRequirement | null;
  nationality_preference?: string[];
  languages_required?: string[];
  job_description: string;
}

export interface JobCreateResponse {
  id: UUID;
}

/** Body of `POST /jobs/generate-description` (AI JD Builder). The Role-Basics
 *  fields only — same shape as `JobCreateRequest` minus `job_description`.
 *  Sent before any job exists to draft a description. */
export type JobDescriptionGenerateRequest = Omit<
  JobCreateRequest,
  "job_description"
>;

export interface JobDescriptionGenerateResponse {
  /** The Claude-drafted job description prose. */
  job_description: string;
}

/** Per-step async pipeline state. Keys appear as steps run; values are the
 *  status. On failure a companion `{step}_error` string key is present. */
export type PipelineStatus = Record<string, "pending" | "ok" | "failed" | string>;

export interface JobListItem {
  id: UUID;
  title: string;
  hiring_company: string;
  status: JobStatus;
  country: string;
  city: string;
  employment_type: EmploymentType;
  work_mode: WorkMode;
  public_slug: string;
  pipeline_status: PipelineStatus;
  ready_for_applications: boolean;
  /** Per-job pipeline funnel: count of this job's applications at each stage.
   *  Always fully populated — every ApplicationStage key present, zero-filled.
   *  Counts all applications regardless of status (rejected/accepted keep the
   *  stage they stopped at). */
  applications_by_stage: Record<ApplicationStage, number>;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export type JobListResponse = Paginated<JobListItem>;

export interface JobDetail {
  id: UUID;
  created_by: UUID;
  title: string;
  hiring_company: string;
  country: string;
  city: string;
  employment_type: EmploymentType;
  work_mode: WorkMode;
  currency: string;
  min_salary: number | null;
  max_salary: number | null;
  notice_period: NoticePeriod | null;
  min_experience_years: number;
  required_skills: string[];
  preferred_skills: string[];
  visa_requirement: VisaRequirement | null;
  nationality_preference: string[];
  languages_required: string[];
  job_description: string;
  whatsapp_questions: WhatsAppQuestion[];
  status: JobStatus;
  public_slug: string;
  pipeline_status: PipelineStatus;
  ready_for_applications: boolean;
  closed_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface JobStatusUpdateRequest {
  status: JobStatusPatch;
}

/* ──────────────────────────────────────────────────────────────────────────
 * WhatsApp screening questions
 * ────────────────────────────────────────────────────────────────────────── */

export interface WhatsAppQuestion {
  /** "q_" + 8 url-safe chars, e.g. "q_Ab12Cd34". */
  id: string;
  /** 1-based display order, 1..15 (6 fixed + ≤3 AI + custom, with headroom). */
  order: number;
  category: WhatsAppQuestionCategory;
  subcategory: string;
  question_en: string;
  question_ar: string;
  reasoning: string;
  /** `true` only for the AI-authored `background_validation` questions; `false`
   *  for the 6 fixed canonical questions and for any custom HR-added question. */
  is_ai_generated: boolean;
  /** The canonical key a fixed question was generated from (`"commitment"`,
   *  `"salary"`, `"notice_period"`, `"visa"`, `"employment_type"`,
   *  `"work_mode"`); `null` for AI-authored and custom questions. */
  source_field: string | null;
  /** Gates answer scoring: when `true` the candidate's WhatsApp reply is scored
   *  by Claude (relevance + ai_likelihood + rationale). Only the AI
   *  `background_validation` questions set this; fixed and custom answers are
   *  stored verbatim and never AI-scored. Defaults to `false`. */
  ai_verifies_response: boolean;
}

export interface WhatsAppQuestionsResponse {
  questions: WhatsAppQuestion[];
}

export interface WhatsAppQuestionsUpdateRequest {
  /** Full ordered list (1..15). `order` values must be unique; `id` unique. */
  questions: WhatsAppQuestion[];
}

/* ──────────────────────────────────────────────────────────────────────────
 * Applications
 * ────────────────────────────────────────────────────────────────────────── */

export interface ApplicationScore {
  /** null for the synthesized `authenticity` entry (no backing row). */
  id: UUID | null;
  score_type: ScoreType;
  value: number;
  /** Per-signal detail; shape varies by score_type (see ASYNC_AND_POLLING.md). */
  breakdown: Record<string, unknown>;
  prompt_version: string;
  model_used: ScoreModel;
  computed_at: ISODateTime;
}

export interface CandidateNested {
  id: UUID;
  /** Either email or phone_e164 may be null (but not both) — a bulk-uploaded
   *  CV that yields only one contact field still creates a candidate. */
  email: string | null;
  phone_e164: string | null;
  full_name: string;
  authenticity_score: number | null;
  authenticity_band: AuthenticityBand | null;
  authenticity_computed_at: ISODateTime | null;
  /** Structured CV (skills, work_history, education, languages,
   *  total_experience_years). `{}` until the parse step runs. */
  parsed_profile: Record<string, unknown>;
}

export interface CvDocumentNested {
  id: UUID;
  blob_url: string;
  blob_sha256: string;
  language: string | null;
  uploaded_at: ISODateTime;
}

export interface ApplicationListItem {
  id: UUID;
  candidate_id: UUID;
  candidate_full_name: string;
  candidate_email: string | null;
  job_id: UUID;
  stage: ApplicationStage;
  status: ApplicationStatus;
  similarity_score: number | null;
  hard_filter_score: number | null;
  stage_updated_at: ISODateTime;
  created_at: ISODateTime;
}

export type ApplicationListResponse = Paginated<ApplicationListItem>;

export interface ApplicationDetail {
  id: UUID;
  job_id: UUID;
  candidate: CandidateNested;
  cv_document: CvDocumentNested;
  stage: ApplicationStage;
  status: ApplicationStatus;
  similarity_score: number | null;
  hard_filter_score: number | null;
  /** Human-readable rejection summary; null unless rejected (and only for
   *  rows with a populated similarity breakdown). */
  rejection_reason: string | null;
  pipeline_status: PipelineStatus;
  consent_context: Record<string, unknown>;
  consented_at: ISODateTime;
  /** Chronological score history + the synthesized authenticity entry. */
  scores: ApplicationScore[];
  stage_updated_at: ISODateTime;
  created_at: ISODateTime;
  updated_at: ISODateTime;
}

export interface ApplicationStageUpdateRequest {
  stage: ApplicationStage;
  /** Optional HR justification, ≤500 chars; lands in the audit log. */
  reason?: string | null;
}

export interface ApplicationStatusUpdateRequest {
  status: ApplicationStatus;
  reason?: string | null;
}

export interface RescoreResponse {
  application_id: UUID;
  type: RescoreType;
  enqueued: boolean; // always true
}

/* ──────────────────────────────────────────────────────────────────────────
 * Audit log
 * ────────────────────────────────────────────────────────────────────────── */

export interface AuditLogEntry {
  id: UUID;
  user_id: UUID | null;
  entity_type: string;
  entity_id: UUID;
  action: string;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown>;
  ip_address: string | null;
  created_at: ISODateTime;
}

export interface AuditLogResponse {
  /** Newest-first. */
  items: AuditLogEntry[];
  total: number;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Bulk upload (HR)
 * ────────────────────────────────────────────────────────────────────────── */

export interface BulkUploadCreatedApplication {
  application_id: UUID;
  candidate_id: UUID;
  filename: string;
}

export interface BulkUploadAlreadyAppliedItem {
  application_id: UUID;
  candidate_id: UUID;
  filename: string;
}

export interface BulkUploadRejectedItem {
  filename: string;
  reason: BulkUploadRejectionReason;
}

export interface BulkUploadResponse {
  batch_id: UUID;
  accepted_count: number;
  rejected_count: number;
  applications: BulkUploadCreatedApplication[];
  already_applied: BulkUploadAlreadyAppliedItem[];
  rejected: BulkUploadRejectedItem[];
}

/* ──────────────────────────────────────────────────────────────────────────
 * Public apply (anonymous candidate)
 * ────────────────────────────────────────────────────────────────────────── */

/** Multipart form fields for POST /public/apply/{slug}/upload. */
export interface PublicApplyForm {
  pdf: File;
  email: string;
  phone: string;
  full_name: string;
  consent: boolean;
  /** Hidden honeypot field — leave empty for real users. */
  honeypot?: string;
}

export interface PublicApplyResponse {
  /** 32-char hex application id, shown to the candidate. */
  reference_number: string;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Dashboard
 *
 * `GET /dashboard` — workspace-wide aggregate counts for the HR home screen.
 * The `by_status` / `by_stage` maps always contain *every* enum value as a
 * key (zero-filled), so you never have to guard a missing key.
 * ────────────────────────────────────────────────────────────────────────── */

export interface DashboardJobsSummary {
  total: number;
  /** Keyed by every `JobStatus` value; zero-filled. */
  by_status: Record<JobStatus, number>;
}

export interface DashboardApplicationsSummary {
  total: number;
  /** Keyed by every `ApplicationStage` value; zero-filled. */
  by_stage: Record<ApplicationStage, number>;
  /** Keyed by every `ApplicationStatus` value; zero-filled. */
  by_status: Record<ApplicationStatus, number>;
}

export interface DashboardCandidatesSummary {
  total: number;
}

export interface DashboardTalentPoolSummary {
  /** Active (non-deactivated) talent-pool entries. */
  active: number;
}

export interface DashboardSummaryResponse {
  jobs: DashboardJobsSummary;
  applications: DashboardApplicationsSummary;
  candidates: DashboardCandidatesSummary;
  talent_pool: DashboardTalentPoolSummary;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Dashboard — Performance / Pipeline / Interviews / Pending feedback
 *   GET /dashboard/performance        → JobPerformanceResponse
 *   GET /dashboard/pipeline?job_id=…  → CandidatePipelineResponse
 *   GET /dashboard/upcoming-interviews?limit=… → UpcomingInterviewsResponse
 *   GET /dashboard/pending-feedback   → PendingFeedbackResponse
 * ────────────────────────────────────────────────────────────────────────── */

/** One row of the Performance table — a single non-draft job's health.
 *  `candidates`/`shortlisted` exclude archived (moved-to-pool) stints;
 *  `shortlisted` counts apps at the `done` stage. `days_open` is
 *  `(closed_at ?? now) − created_at` in whole days. */
export interface JobPerformanceRow {
  job_id: string;
  title: string;
  status: JobStatus;
  candidates: number;
  shortlisted: number;
  days_open: number;
  health: JobHealth;
}

/** GET /dashboard/performance — rows sorted at-risk first, then days_open desc. */
export interface JobPerformanceResponse {
  rows: JobPerformanceRow[];
}

/** GET /dashboard/pipeline — `job_id` null for the All-Jobs view.
 *  `by_bucket` counts *active* applications, always fully populated
 *  (every PipelineBucket key present, zero-filled). `by_stage` is the same
 *  active counts at raw ApplicationStage granularity (every stage key present,
 *  zero-filled) so the FE can mirror the kanban board's five columns.
 *  conversion: `applied` = non-archived apps, `offers` = status==accepted,
 *  `conversion_rate` = offers/applied as a 0–100 percentage (0 when none
 *  applied). */
export interface CandidatePipelineResponse {
  job_id: string | null;
  by_bucket: Record<PipelineBucket, number>;
  by_stage: Record<ApplicationStage, number>;
  applied: number;
  offers: number;
  conversion_rate: number;
}

/** One booked, future interview slot. Show `join_url` for virtual meetings,
 *  else `location_text` (physical / phone). */
export interface UpcomingInterview {
  application_id: string;
  job_id: string;
  candidate_name: string;
  job_title: string;
  scheduled_start_at: string; // ISO-8601
  scheduled_end_at: string | null;
  location_type: string | null;
  join_url: string | null;
  location_text: string | null;
  invitee_timezone: string | null;
}

/** GET /dashboard/upcoming-interviews — soonest first. `total` is the full
 *  count of future booked interviews (so you know if "show all" has more than
 *  the returned page). `limit` defaults to 3, max 200. */
export interface UpcomingInterviewsResponse {
  interviews: UpcomingInterview[];
  total: number;
}

/** An application stalled at the `interview` stage past the feedback SLA
 *  (active, stage==interview, last moved > 3 days ago). `days_waiting` is
 *  whole days since `stage_updated_at`. */
export interface PendingFeedbackItem {
  application_id: string;
  job_id: string;
  candidate_name: string;
  job_title: string;
  stage: ApplicationStage;
  stage_updated_at: string; // ISO-8601
  days_waiting: number;
}

/** GET /dashboard/pending-feedback — oldest first. */
export interface PendingFeedbackResponse {
  items: PendingFeedbackItem[];
}

/* ──────────────────────────────────────────────────────────────────────────
 * Talent pool — candidate cross-job history
 *   GET /talent-pool/candidates/{candidate_id}/history
 * ────────────────────────────────────────────────────────────────────────── */

/** Lifecycle of one stint's WhatsApp screening conversation. */
export type WhatsAppConversationState =
  | "awaiting_interest"
  | "asking_questions"
  | "completed"
  | "declined";

/** One scored screening answer in a stint's WhatsApp digest. Per-answer scores
 *  are null until the answer-scoring task runs (or for non-answer turns). */
export interface CandidateHistoryScreeningAnswer {
  question_index: number | null;
  question: string | null;
  answer: string | null;
  relevance_score: number | null;
  ai_score: number | null;
  rationale: string | null;
}

/** Compact WhatsApp screening digest for one stint. The authoritative
 *  transcript stays at GET /applications/{id}/whatsapp. */
export interface CandidateHistoryScreening {
  conversation_id: UUID;
  state: WhatsAppConversationState;
  answers: CandidateHistoryScreeningAnswer[];
  created_at: ISODateTime;
  closed_at: ISODateTime | null;
}

/** One application the candidate had on one job — any status, including
 *  `archived` (moved back to the pool). Scores + screening are this stint's
 *  own, computed against that job. Authenticity is candidate-level and lives
 *  once on `CandidateHistoryResponse.candidate`, not here. */
export interface CandidateHistoryStint {
  application_id: UUID;
  job_id: UUID;
  job_title: string;
  stage: ApplicationStage;
  status: ApplicationStatus;
  sourced_from_talent_pool: boolean;
  similarity_score: number | null;
  hard_filter_score: number | null;
  hard_filter_breakdown: Record<string, unknown> | null;
  /** The append-only `application_scores` rows for this stint (similarity +
   *  hard_filter), newest-first. Authenticity is not duplicated here. */
  scores: ApplicationScore[];
  screening: CandidateHistoryScreening | null;
  created_at: ISODateTime;
  stage_updated_at: ISODateTime;
  /** When this stint was archived (moved to the pool); null while live. */
  archived_at: ISODateTime | null;
}

export interface CandidateHistoryResponse {
  candidate: CandidateNested;
  /** Whether the candidate currently sits in the active talent pool. */
  in_pool: boolean;
  /** Newest-first by creation; one entry per job stint the candidate had. */
  stints: CandidateHistoryStint[];
  total_stints: number;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Health
 * ────────────────────────────────────────────────────────────────────────── */

export interface HealthResponse {
  status: "ok";
  version: string;
}
