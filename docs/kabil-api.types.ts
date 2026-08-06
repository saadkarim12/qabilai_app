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
/**
 * A 0–100 score rendered as a trimmed percentage string, e.g. "82%", "67.5%",
 * "35%". Application-level scores (similarity / hard_filter / authenticity) and
 * the `score` / `weight` leaves inside a breakdown use this shape — NOT the
 * talent-pool search `similarity_score`, which is a plain number (or `null` for
 * lexical hits). Parse with `parseFloat(value)` when you need to compare or sort
 * numerically.
 */
export type PercentString = string;

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

/** Lifecycle flag, orthogonal to stage. */
export type ApplicationStatus = "active" | "rejected" | "accepted";

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
 *  unhealthy (open >20 working days) → at_risk (open >=18) → healthy
 *  (younger, or any non-open job). */
export type JobHealth = "healthy" | "at_risk" | "unhealthy";

/** The four Candidate Pipeline funnel columns. sourcing = vector_screen +
 *  hard_filter, screening = whatsapp, interview = interview, final_shortlist =
 *  done. */
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

/** The three buckets a WhatsApp screening question can use. The fixed
 *  canonical questions and any HR-added custom questions use `commitment`,
 *  `salary`, or `background_validation`; the AI-authored skill-verification
 *  questions (required + preferred skills only) are always
 *  `background_validation`. The specific topic of a question lives in its
 *  `subcategory`. */
export type WhatsAppQuestionCategory =
  | "commitment"
  | "salary"
  | "background_validation";

/** Lifecycle of one application's WhatsApp screening conversation.
 *  awaiting_interest → (Yes) asking_questions → completed, or → declined on No.
 *  completed / declined are terminal. */
export type WhatsAppConversationState =
  | "awaiting_interest"
  | "asking_questions"
  | "completed"
  | "declined";

/** Who sent a transcript message. */
export type WhatsAppDirection = "outbound" | "inbound";

/** What kind of payload a transcript message holds. Open-ended on the backend. */
export type WhatsAppMessageType =
  | "text"
  | "interactive_buttons"
  | "button_reply"
  | (string & {});

/** Stable ids of the interest quick-reply buttons (appear in `button_id`). */
export type WhatsAppButtonId = "interest_yes" | "interest_no";

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
  /** 1-based display order, 1..15 (6 fixed + ≤3 AI background_validation +
   *  custom). */
  order: number;
  category: WhatsAppQuestionCategory;
  subcategory: string;
  question_en: string;
  question_ar: string;
  reasoning: string;
  /** `true` only for AI-authored background-validation questions; `false` for
   *  the 6 fixed canonical questions and for HR-added custom questions. */
  is_ai_generated: boolean;
  /** For a fixed canonical question, the topic key it covers (e.g. `"salary"`,
   *  `"visa"`); `null` for AI-authored and HR-added custom questions. */
  source_field: string | null;
  /** Gates AI scoring of the candidate's reply. When `true` the answer is
   *  scored (relevance + ai_likelihood); when `false` (default) the answer is
   *  stored but not scored. In practice only the AI `background_validation`
   *  questions have this set to `true`. */
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
  /** Percentage string, e.g. "82%" or "67.5%". Parse with parseFloat for math. */
  value: PercentString;
  /** Per-signal detail; shape varies by score_type (see ASYNC_AND_POLLING.md).
   *  Numeric `score` / `weight` leaves are themselves percentage strings. */
  breakdown: Record<string, unknown>;
  prompt_version: string;
  model_used: ScoreModel;
  computed_at: ISODateTime;
}

export interface CandidateNested {
  id: UUID;
  email: string | null;
  phone_e164: string | null;
  full_name: string;
  /** Percentage string ("82%") or null until the authenticity step runs. */
  authenticity_score: PercentString | null;
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
  /** Percentage string ("82%") or null until scored. */
  similarity_score: PercentString | null;
  hard_filter_score: PercentString | null;
  /** HR's interview mark ("76%"), set only at the interview stage; null otherwise. */
  interview_score: PercentString | null;
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
  /** Percentage string ("82%") or null until scored. */
  similarity_score: PercentString | null;
  hard_filter_score: PercentString | null;
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

/**
 * PATCH /applications/{id}/candidate — edit the candidate's contact details.
 * Accepted only while the application is in `vector_screen` / `hard_filter`
 * (locked from `whatsapp` on). `full_name` required; at least one of
 * `email`/`phone` must remain. `phone` is normalised to E.164 server-side.
 * The candidate row is shared, so the edit affects all their applications.
 */
export interface CandidateContactUpdateRequest {
  full_name: string;
  email?: string | null;
  phone?: string | null;
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

/** GET /public/apply/{slug} — candidate-facing job view. Trimmed projection of
 *  the job; no HR/internal fields. Render before the upload form. */
export interface PublicJobResponse {
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
  /** Kept so a stale link can render a "this role is closed" state. */
  status: JobStatus;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Talent Pool (HR)
 * ────────────────────────────────────────────────────────────────────────── */

/** Minimal candidate snapshot shown in pool listings / search hits. */
export interface TalentPoolCandidateNested {
  id: UUID;
  full_name: string;
  email: string | null;
  phone_e164: string | null;
  /** Most-recent work-history title from the parsed CV (null until parsed). */
  role: string | null;
  /** Top parsed skills (empty until the CV is parsed). */
  skills: string[];
  /** Candidate-level CV-trust verdict (null until scored). */
  authenticity_score: number | null;
  authenticity_band: string | null;
}

export interface TalentPoolEntry {
  id: UUID;
  candidate_id: UUID;
  source_job_id: UUID | null;
  /** Title of the job the candidate was sourced from; null = direct upload. */
  source_job_title: string | null;
  added_by: UUID | null;
  added_at: ISODateTime;
  expires_at: ISODateTime;
  is_active: boolean;
  candidate: TalentPoolCandidateNested;
}

/** POST /talent-pool/entries body. */
export interface TalentPoolAddRequest {
  candidate_id: UUID;
  source_job_id?: UUID | null;
}

/** POST /talent-pool/upload (201) — multipart `file` (PDF, ≤10 MB). */
export interface TalentPoolUploadResponse {
  entry: TalentPoolEntry;
  candidate_created: boolean;
  cv_created: boolean;
  /** True if the parse/authenticity/embed pipeline was dispatched (not yet
   *  searchable until it finishes). */
  enqueued: boolean;
}

/** GET /talent-pool (paginated, newest-first). */
export interface TalentPoolListResponse {
  items: TalentPoolEntry[];
  total: number;
}

export interface TalentPoolSearchResultItem {
  candidate_id: UUID;
  entry_id: UUID;
  cv_document_id: UUID;
  /**
   * Job (semantic) hits: cosine similarity of the job's JD vs. the candidate's
   * CV, 0–100 (higher = closer). Free-text (lexical) hits: `null` — text search
   * matches on role/skills/name, not vectors, so there is no score.
   */
  similarity_score: number | null;
  /** Title of the job the candidate was sourced from; null = direct upload. */
  source_job_title: string | null;
  added_at: ISODateTime;
  expires_at: ISODateTime;
  is_active: boolean;
  candidate: TalentPoolCandidateNested;
}

/** GET /talent-pool/search?q=…|job_id=…&limit=…&active_only=… (one of q/job_id required). */
export interface TalentPoolSearchResponse {
  query: string;
  items: TalentPoolSearchResultItem[];
  total: number;
}

/** POST /talent-pool/source body. */
export interface TalentPoolSourceRequest {
  candidate_id: UUID;
  job_id: UUID;
}

/** POST /talent-pool/source (202). */
export interface TalentPoolSourceResponse {
  application_id: UUID;
  candidate_id: UUID;
  job_id: UUID;
  sourced_from_talent_pool: boolean;
  /** True if an application for this candidate+job already existed (nothing
   *  new was created or enqueued). */
  already_existed: boolean;
  enqueued: boolean;
}

/* ──────────────────────────────────────────────────────────────────────────
 * WhatsApp screening transcript (HR)
 * GET /applications/{id}/whatsapp
 * ────────────────────────────────────────────────────────────────────────── */

/** One message in the transcript (oldest-first in the parent list). */
export interface WhatsAppMessageResponse {
  id: UUID;
  direction: WhatsAppDirection;
  message_type: WhatsAppMessageType;
  body: string | null;
  button_id: string | null;
  button_title: string | null;
  /** Which screening question this message relates to, if any. */
  question_index: number | null;
  wa_message_id: string | null;
  /** Scoring of an inbound answer message (null on outbound / unscored). */
  answer_relevance_score: number | null; // 0–10, higher = better addresses the question
  answer_ai_score: number | null; // 0–10, higher = more likely AI-generated
  answer_score_rationale: string | null;
  created_at: ISODateTime;
}

/** One scored question/answer pair, aggregated on the conversation. */
export interface WhatsAppAnswer {
  question_id: string;
  question: string;
  answer: string;
  rationale: string;
  relevance_score: number; // 0–10
  ai_likelihood_score: number; // 0–10
}

export interface WhatsAppConversationResponse {
  id: UUID;
  application_id: UUID;
  candidate_id: UUID;
  job_id: UUID;
  state: WhatsAppConversationState;
  current_question_index: number | null;
  /** Structured, scored Q&A captured during screening. */
  answers: WhatsAppAnswer[];
  phone_e164: string;
  created_at: ISODateTime;
  updated_at: ISODateTime;
  closed_at: ISODateTime | null;
  messages: WhatsAppMessageResponse[];
}

/* ──────────────────────────────────────────────────────────────────────────
 * Dashboard
 * GET /dashboard — workspace-wide aggregate counts for the HR home screen.
 * The `by_status` / `by_stage` maps always contain *every* enum value as a
 * key (zero-filled), so you never have to guard a missing key.
 * ────────────────────────────────────────────────────────────────────────── */

export interface DashboardJobsSummary {
  total: number;
  /** Keyed by every JobStatus value; zero-filled. */
  by_status: Record<JobStatus, number>;
}

export interface DashboardApplicationsSummary {
  total: number;
  /** Keyed by every ApplicationStage value; zero-filled. */
  by_stage: Record<ApplicationStage, number>;
  /** Keyed by every ApplicationStatus value; zero-filled. */
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

/* Dashboard — Performance / Pipeline / Interviews / Pending feedback.
 *   GET /dashboard/performance        → JobPerformanceResponse
 *   GET /dashboard/pipeline?job_id=…  → CandidatePipelineResponse
 *   GET /dashboard/upcoming-interviews?limit=… → UpcomingInterviewsResponse
 *   GET /dashboard/pending-feedback   → PendingFeedbackResponse */

/** One row of the Performance table. `candidates`/`shortlisted` exclude archived
 *  stints; `shortlisted` = apps at the `done` stage. `days_open` =
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

/** GET /dashboard/pipeline — `job_id` null for the All-Jobs view. `by_bucket`
 *  counts *active* applications (every PipelineBucket key present, zero-filled).
 *  `by_stage` is the same active counts at raw ApplicationStage granularity
 *  (every stage key present, zero-filled) so the FE can mirror the kanban
 *  board's five columns. Conversion: `applied` = non-archived apps,
 *  `offers` = accepted, `conversion_rate` = offers/applied as a 0–100 percentage. */
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

/** GET /dashboard/upcoming-interviews — soonest first. `total` is the full count
 *  of future booked interviews. `limit` defaults to 3, max 200. */
export interface UpcomingInterviewsResponse {
  interviews: UpcomingInterview[];
  total: number;
}

/** An application stalled at the `interview` stage past the feedback SLA
 *  (active, stage==interview, last moved > 3 days ago). `days_waiting` is whole
 *  days since `stage_updated_at`. */
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
 * Health
 * ────────────────────────────────────────────────────────────────────────── */

export interface HealthResponse {
  status: "ok";
  version: string;
}
