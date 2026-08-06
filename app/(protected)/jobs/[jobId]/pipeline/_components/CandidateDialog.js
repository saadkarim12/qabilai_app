"use client";

import { useEffect, useMemo, useState } from "react";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import DialogActions from "@mui/material/DialogActions";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Divider from "@mui/material/Divider";
import Chip from "@mui/material/Chip";
import Avatar from "@mui/material/Avatar";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import TextField from "@mui/material/TextField";
import Tabs from "@mui/material/Tabs";
import Tab from "@mui/material/Tab";
import Alert from "@mui/material/Alert";
import Skeleton from "@mui/material/Skeleton";
import CloseIcon from "@mui/icons-material/Close";
import EmailOutlinedIcon from "@mui/icons-material/EmailOutlined";
import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import BlockIcon from "@mui/icons-material/Block";
import ReplayIcon from "@mui/icons-material/Replay";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import WhatsAppIcon from "@mui/icons-material/WhatsApp";
import PersonAddAltOutlinedIcon from "@mui/icons-material/PersonAddAltOutlined";
import VideocamOutlinedIcon from "@mui/icons-material/VideocamOutlined";
import PlaceOutlinedIcon from "@mui/icons-material/PlaceOutlined";
import WorkOutlineIcon from "@mui/icons-material/WorkOutlineOutlined";
import SchoolOutlinedIcon from "@mui/icons-material/SchoolOutlined";
import TranslateOutlinedIcon from "@mui/icons-material/TranslateOutlined";
import EventAvailableOutlinedIcon from "@mui/icons-material/EventAvailableOutlined";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined";
import CheckIcon from "@mui/icons-material/Check";
import ErrorAlert from "@/components/ErrorAlert";
import WhatsAppDialog from "./WhatsAppDialog";
import CvScoreCard from "./CvScoreCard";
import {
  useApplication,
  useUpdateStage,
  useUpdateStatus,
  useMoveToPool,
  useUpdateCandidateContact,
  useWhatsAppConversation,
  useWhatsAppQuestions,
  useCandidateActivity,
} from "@/lib/kabil/queries";
import {
  APPLICATION_STAGES,
  NEXT_STAGE,
  commitmentVerdict,
  formatNoticePeriod,
  humanize,
  stageLabel,
  statusLabel,
  timeAgo,
} from "@/lib/kabil/constants";
import { COLORS } from "@/lib/theme";

/** Requested brand green for the candidate dialog chrome. */
const GREEN = "#0F6E56";
const GREEN_DARK = "#0c5a46";
/** Shared cream card surface + hairline — mirrors the talent-pool history card. */
const CARD_BORDER = "#ECE5D6";
const activityCardSx = {
  bgcolor: "#F4F0E84D",
  border: `1px solid ${CARD_BORDER}`,
  borderRadius: "14px",
  overflow: "hidden",
};
const activitySectionTitleSx = {
  fontFamily: "var(--font-jakarta), system-ui, sans-serif",
  fontWeight: 700,
  fontSize: "13px",
  lineHeight: "16px",
  letterSpacing: "0.2px",
  color: GREEN,
  mb: 1,
};

const initials = (name) =>
  (name || "?")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");

const asArray = (v) => (Array.isArray(v) ? v : []);

/**
 * The six fixed eligibility questions every candidate answers over WhatsApp,
 * in the order the recruiter reads them. `key` is the backend `source_field`
 * stamped on each fixed question; `label` is recruiter-facing.
 */
const ELIGIBILITY_FIELDS = [
  { key: "commitment", label: "Commitment" },
  { key: "salary", label: "Salary Expectation" },
  { key: "notice_period", label: "Notice Period" },
  { key: "visa", label: "Visa" },
  { key: "employment_type", label: "Employment Type" },
  { key: "work_mode", label: "Work Mode" },
];

/** Format one fixed answer's backend `extracted` payload into the recruiter-
 *  facing display string for its Eligibility Questions row, per source_field.
 *  Returns null when the value wasn't extracted (row shows "—"). */
const formatEligibility = (key, extracted, state) => {
  // Commitment is not extracted from text — it's a verdict on how far the
  // candidate engaged with screening, derived from the conversation state.
  if (key === "commitment") return commitmentVerdict(state);
  if (!extracted || typeof extracted !== "object") return null;
  switch (key) {
    case "salary": {
      const { amount, currency } = extracted;
      if (amount == null) return null;
      return `${amount.toLocaleString()}${currency ? ` ${currency}` : ""}`;
    }
    case "notice_period":
      return formatNoticePeriod(extracted.days);
    case "visa":
      return extracted.valid == null ? null : extracted.valid ? "Yes" : "No";
    case "employment_type":
    case "work_mode": {
      // Shown as the job's own setting once accepted; flagged if declined.
      if (extracted.accepted == null) return null;
      return extracted.accepted ? humanize(extracted.value) || "Yes" : "Not open";
    }
    default:
      return null;
  }
};

/**
 * Fold the WhatsApp screening conversation + the job's question list into the
 * view-model the score card needs:
 *  - `eligibility`: each fixed question normalized to a glanceable value
 *    ("15,800 AED", "3 Weeks", "Yes", "Remote", "Most Likely"), joining
 *    `answers[].question_id` back to the question's `source_field` (the answer
 *    copy carries no source_field of its own). The salary / notice / visa /
 *    employment / work-mode values come from the backend's per-answer
 *    `extracted` payload; Commitment is derived from the conversation state.
 *  - `bgValidation`: a 0–100 "background validation" score derived from the
 *    AI-scored answers' relevance (0–10, only the AI questions are scored) —
 *    the backend computes no such aggregate, so we mean-then-scale here. Null
 *    until at least one AI answer has been scored.
 *  - `bgValidationBreakdown`: the per-question rows behind that score — one
 *    entry per scored answer to an AI-authored `background_validation` question
 *    (question text, the candidate's answer, and the 0–100 relevance). Only the
 *    background-validation questions are included; the fixed canonical
 *    questions (commitment/salary/notice/…) surface in Eligibility instead.
 */
const deriveScreening = (convo, questions) => {
  const answers = asArray(convo?.answers);
  const qList = asArray(questions);
  const sourceById = new Map(qList.map((q) => [q.id, q.source_field]));
  // AI-authored background-validation questions — the only ones scored. Fixed
  // canonical questions also carry the `background_validation` category, so we
  // additionally require `is_ai_generated` (equivalently a null source_field).
  const bgQuestionIds = new Set(
    qList
      .filter(
        (q) =>
          q?.category === "background_validation" &&
          (q?.is_ai_generated || q?.ai_verifies_response || q?.source_field == null),
      )
      .map((q) => q.id),
  );

  const extractedBySource = new Map();
  for (const a of answers) {
    const sf = sourceById.get(a?.question_id);
    if (!sf || extractedBySource.has(sf)) continue;
    extractedBySource.set(sf, a?.extracted ?? null);
  }
  const state = convo?.state ?? null;
  const eligibility = ELIGIBILITY_FIELDS.map((f) => ({
    label: f.label,
    value: formatEligibility(f.key, extractedBySource.get(f.key), state),
  }));

  // Scored answers to background-validation questions. When the question list
  // hasn't loaded yet (empty set) fall back to every scored answer, since in
  // practice only AI background-validation questions are ever scored.
  const scored = answers.filter(
    (a) => a?.relevance_score != null && (bgQuestionIds.size === 0 || bgQuestionIds.has(a?.question_id)),
  );
  const bgValidation = scored.length
    ? Math.round((scored.reduce((s, a) => s + a.relevance_score, 0) / scored.length) * 10)
    : null;
  const bgValidationBreakdown = scored.map((a) => ({
    question: a.question,
    answer: a.answer,
    score: Math.round(a.relevance_score * 10),
  }));

  return {
    eligibility,
    bgValidation,
    bgValidationBreakdown,
    computedAt: convo?.closed_at || convo?.updated_at || null,
  };
};

/** One labelled value row. */
const Field = ({ label, children }) => (
  <Box>
    <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, display: "block" }}>
      {label}
    </Typography>
    <Typography variant="body2" sx={{ wordBreak: "break-word" }}>
      {children ?? "—"}
    </Typography>
  </Box>
);

/** A titled block inside the dialog. */
const Section = ({ title, action, children, titleSx }) => (
  <Box>
    <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between", mb: 1 }}>
      <Typography variant="overline" color="text.secondary" sx={{ fontWeight: 700, ...titleSx }}>
        {title}
      </Typography>
      {action}
    </Stack>
    {children}
  </Box>
);

/** Cream surface shared by the candidate-profile cards (matches the score cards). */
const PROFILE_BG = "#F4F0E84D";
const PROFILE_BORDER = "#E2DDCE";
const SKILL_LIMIT = 21;

const skillChipSx = {
  bgcolor: "#fff",
  border: "1px solid #dfe5e0",
  borderRadius: 999,
  color: "#2b3530",
  fontWeight: 500,
  fontSize: "0.655rem",
  height: 28,
  "& .MuiChip-label": { px: 1.5 },
};

// Eyebrow label ("card key") + value ("key") type for the Experience/Languages cards.
const STAT_LABEL_SX = {
  fontFamily: "var(--font-jakarta), system-ui, sans-serif",
  fontWeight: 700,
  fontSize: "10px",
  lineHeight: "15px",
  letterSpacing: "0.5px",
  textTransform: "uppercase",
  color: "#0F6E56",
};
const STAT_VALUE_SX = {
  fontFamily: "var(--font-jakarta), system-ui, sans-serif",
  fontWeight: 600,
  fontSize: "14px",
  lineHeight: "20px",
  letterSpacing: 0,
  color: "#1C4A3E",
};
// Secondary (sub-line) value type for the Education / Work History cards.
const INFO_VALUE_SX = {
  fontFamily: "var(--font-jakarta), system-ui, sans-serif",
  fontWeight: 400,
  fontSize: "12px",
  lineHeight: "16px",
  letterSpacing: 0,
  color: "#6B7280",
};

/** Eyebrow stat card (Experience / Languages). */
const StatCard = ({ icon, label, children }) => (
  <Box sx={{ border: `1px solid ${PROFILE_BORDER}`, bgcolor: PROFILE_BG, borderRadius: "14px", px: 2.5, py: 1.75 }}>
    <Stack direction="row" spacing={0.85} sx={{ alignItems: "center", mb: 1 }}>
      {icon}
      <Typography sx={STAT_LABEL_SX}>{label}</Typography>
    </Stack>
    {children}
  </Box>
);

/** Cream info card used for education + work-history rows. */
const InfoCard = ({ children }) => (
  <Box sx={{ border: `1px solid ${PROFILE_BORDER}`, bgcolor: PROFILE_BG, borderRadius: "14px", px: 2.25, py: 1.85 }}>
    {children}
  </Box>
);

/** Green-icon column heading (Education / Work History). */
const ProfileHeading = ({ icon: Icon, title }) => (
  <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
    <Icon sx={{ fontSize: 18, color: GREEN }} />
    <Typography sx={{ fontWeight: 700, fontSize: 11 }}>{title}</Typography>
  </Stack>
);

/** "13th January 2026" — day (with ordinal) + full month + year. */
const activityOrdinal = (n) => {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
};
const activityDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const month = new Intl.DateTimeFormat(undefined, { month: "long" }).format(d);
  return `${activityOrdinal(d.getDate())} ${month} ${d.getFullYear()}`;
};

/** One audit entry → the recruiter-facing sentence. Job context comes from the
 *  backend-enriched `job_title` (null for candidate-level rows). */
const activitySentence = (e) => {
  const before = e.before_state || {};
  const after = e.after_state || {};
  const job = e.job_title || "a job";
  switch (e.action) {
    case "stage_changed":
      return `Moved from ${stageLabel(before.stage)} to ${stageLabel(after.stage)} for ${job}`;
    case "application_moved_to_pool":
      return `Moved from ${job} to Talent Pool at ${stageLabel(before.stage)}.`;
    case "status_changed":
      return `Status changed from ${statusLabel(before.status)} to ${statusLabel(after.status)} for ${job}`;
    case "manually_accepted":
      return `Accepted for ${job}`;
    case "manually_rejected":
      return `Rejected for ${job}`;
    case "whatsapp_declined":
      return `Declined screening on WhatsApp for ${job}`;
    case "rescore_requested":
      return `Re-scoring requested for ${job}`;
    case "candidate_details_updated":
      return `Contact details updated for ${job}`;
    case "interview_scored":
      return `Interview scored for ${job}`;
    case "talent_pool_added":
      return "Added to Talent Pool";
    case "talent_pool_sourced":
      return `Sourced from Talent Pool to ${job}`;
    case "bulk_upload":
      return `Added via bulk upload to ${job}`;
    default:
      return e.job_title ? `${humanize(e.action)} for ${job}` : humanize(e.action);
  }
};

/** One row in the Recent Activity card: gold dot + sentence + date. */
const ActivityRow = ({ entry, isLast }) => (
  <Box sx={{ px: 2, py: 1.5, borderBottom: isLast ? "none" : `1px solid ${CARD_BORDER}` }}>
    <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
      <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: COLORS.gold, flexShrink: 0 }} />
      <Typography variant="body2" sx={{ color: "#2C2C2A", fontWeight: 500 }}>
        {activitySentence(entry)}
      </Typography>
    </Stack>
    <Typography variant="caption" sx={{ display: "block", ml: "20px", color: "text.secondary" }}>
      {activityDate(entry.created_at)}
    </Typography>
  </Box>
);

/**
 * The candidate's cross-job activity feed — every audit-log entry across all
 * their applications plus the candidate-level talent-pool rows, newest-first.
 * Reuses the talent-pool history "Recent Activity" card styling. Rendered on
 * the Candidate Profile tab; hides itself when there's nothing to show.
 */
const RecentActivity = ({ candidateId }) => {
  const { data, isLoading } = useCandidateActivity(candidateId, { enabled: !!candidateId });
  const items = asArray(data?.items);

  if (!candidateId) return null;

  if (isLoading) {
    return (
      <Box>
        <Typography sx={activitySectionTitleSx}>Recent Activity</Typography>
        <Box sx={activityCardSx}>
          {[0, 1].map((i) => (
            <Box
              key={i}
              sx={{ px: 2, py: 1.5, borderBottom: i === 0 ? `1px solid ${CARD_BORDER}` : "none" }}
            >
              <Skeleton width="70%" height={18} />
              <Skeleton width="30%" height={14} />
            </Box>
          ))}
        </Box>
      </Box>
    );
  }

  if (!items.length) return null;

  return (
    <Box>
      <Typography sx={activitySectionTitleSx}>Recent Activity</Typography>
      <Box sx={activityCardSx}>
        {items.map((entry, i) => (
          <ActivityRow key={entry.id} entry={entry} isLast={i === items.length - 1} />
        ))}
      </Box>
    </Box>
  );
};

/** Structured CV from `candidate.parsed_profile` (shape is loose — render defensively). */
const ParsedProfile = ({ profile }) => {
  const p = profile || {};
  const skills = asArray(p.skills).map(String).filter(Boolean);
  const work = asArray(p.work_history);
  const education = asArray(p.education);
  const languages = asArray(p.languages)
    .map((l) => (typeof l === "object" ? l?.name || "" : l))
    .filter(Boolean);
  const years = p.total_experience_years;

  const [showAllSkills, setShowAllSkills] = useState(false);

  if (!Object.keys(p).length) {
    return (
      <Typography variant="body2" color="text.secondary">
        CV is still being parsed — details will appear once processing completes.
      </Typography>
    );
  }

  const stats = [];
  if (years != null) {
    stats.push(
      <StatCard key="exp" icon={<WorkOutlineIcon sx={{ fontSize: 18, color: GREEN }} />} label="EXPERIENCE">
        <Stack direction="row" spacing={0.75} sx={{ alignItems: "baseline" }}>
          <Typography sx={STAT_VALUE_SX}>{years}</Typography>
          <Typography sx={{ ...STAT_VALUE_SX, color: "text.secondary" }}>{years === 1 ? "yr" : "yrs"}</Typography>
        </Stack>
      </StatCard>,
    );
  }
  if (languages.length > 0) {
    stats.push(
      <StatCard key="lang" icon={<TranslateOutlinedIcon sx={{ fontSize: 18, color: GREEN }} />} label="LANGUAGES">
        <Typography sx={STAT_VALUE_SX}>{languages.join(", ")}</Typography>
      </StatCard>,
    );
  }

  const visibleSkills = showAllSkills ? skills : skills.slice(0, SKILL_LIMIT);
  const hiddenCount = skills.length - visibleSkills.length;

  return (
    <Stack spacing={2.5}>
      {stats.length > 0 && (
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, gap: 2 }}>{stats}</Box>
      )}

      {skills.length > 0 && (
        <Box>
          <Typography sx={{ fontWeight: 700, fontSize: 11, mb: 1.25 }}>Skills</Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
            {visibleSkills.map((s, i) => (
              <Chip key={`${s}-${i}`} label={s} sx={skillChipSx} />
            ))}
            {hiddenCount > 0 && (
              <Chip
                label={`+${hiddenCount} more...`}
                onClick={() => setShowAllSkills(true)}
                sx={{ ...skillChipSx, color: GREEN, cursor: "pointer" }}
              />
            )}
            {showAllSkills && skills.length > SKILL_LIMIT && (
              <Chip
                label="Show less"
                onClick={() => setShowAllSkills(false)}
                sx={{ ...skillChipSx, color: GREEN, cursor: "pointer" }}
              />
            )}
          </Box>
        </Box>
      )}

      {(education.length > 0 || work.length > 0) && (
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, gap: 3 }}>
          {education.length > 0 && (
            <Box>
              <ProfileHeading icon={SchoolOutlinedIcon} title="Education" />
              <Stack spacing={1.5} sx={{ mt: 1.5 }}>
                {education.map((e, i) => {
                  const obj = typeof e === "object" && e ? e : null;
                  const degree = obj ? obj.degree || obj.title || "—" : String(e);
                  const inst = obj ? obj.institution || obj.school || "" : "";
                  const year = obj ? obj.year : "";
                  return (
                    <InfoCard key={i}>
                      <Typography sx={STAT_VALUE_SX}>{degree}</Typography>
                      {(inst || year) && (
                        <Typography sx={INFO_VALUE_SX}>
                          {[inst, year].filter(Boolean).join(" · ")}
                        </Typography>
                      )}
                    </InfoCard>
                  );
                })}
              </Stack>
            </Box>
          )}

          {work.length > 0 && (
            <Box>
              <ProfileHeading icon={WorkOutlineIcon} title="Work History" />
              <Stack spacing={1.5} sx={{ mt: 1.5 }}>
                {work.map((w, i) => {
                  const dates = [w.start_date || w.start, w.end_date || w.end].filter(Boolean).join(" – ");
                  const sub = [w.company, dates].filter(Boolean).join(" · ");
                  return (
                    <InfoCard key={i}>
                      <Stack direction="row" spacing={1.25} sx={{ alignItems: "center", minWidth: 0 }}>
                        <Box
                          sx={{
                            width: 9,
                            height: 9,
                            borderRadius: "50%",
                            bgcolor: i === 0 ? COLORS.gold : GREEN,
                            flexShrink: 0,
                          }}
                        />
                        <Typography noWrap sx={STAT_VALUE_SX}>
                          {w.title || w.role || "Role"}
                        </Typography>
                      </Stack>
                      {sub && <Typography sx={{ ...INFO_VALUE_SX, pl: "19px" }}>{sub}</Typography>}
                      {w.summary && (
                        <Typography sx={{ ...INFO_VALUE_SX, pl: "19px", display: "block", mt: 0.5 }}>
                          {w.summary}
                        </Typography>
                      )}
                    </InfoCard>
                  );
                })}
              </Stack>
            </Box>
          )}
        </Box>
      )}
    </Stack>
  );
};

/** Centered "Open CV" button matching the mock. */
const OpenCvButton = ({ doc }) => (
  <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 0.75 }}>
    <Button
      variant="contained"
      startIcon={<OpenInNewIcon />}
      component="a"
      href={doc.blob_url}
      target="_blank"
      rel="noopener noreferrer"
      sx={{ bgcolor: GREEN, px: 4, py: 1.25, borderRadius: "5px", "&:hover": { bgcolor: GREEN_DARK } }}
    >
      Open CV
    </Button>
  </Box>
);

/** Absolute date + time, optionally rendered in the invitee's IANA timezone. */
const formatDateTime = (iso, tz) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      ...(tz ? { timeZone: tz } : {}),
    }).format(d);
  } catch {
    return d.toLocaleString();
  }
};

/** Time-only, for the trailing end of a slot range. */
const formatTime = (iso, tz) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeStyle: "short",
      ...(tz ? { timeZone: tz } : {}),
    }).format(d);
  } catch {
    return "";
  }
};

const INTERVIEW_STATE_META = {
  invited: { label: "Invite sent", color: "info" },
  booked: { label: "Booked", color: "success" },
  canceled: { label: "Canceled", color: "error" },
};
const interviewStateMeta = (state) =>
  INTERVIEW_STATE_META[state] || { label: humanize(state), color: "default" };

/** Calendly location discriminator → recruiter-facing label. */
const LOCATION_LABELS = {
  zoom: "Zoom",
  google_conference: "Google Meet",
  microsoft_teams_conference: "Microsoft Teams",
  gotomeeting: "GoTo Meeting",
  webex_conference: "Webex",
  physical: "In person",
  outbound_call: "Phone — we call the candidate",
  inbound_call: "Phone — candidate calls in",
  custom: "Custom",
  ask_invitee: "Candidate's choice",
};
const locationLabel = (type) => (type ? LOCATION_LABELS[type] || humanize(type) : "—");

/**
 * The interview booking lifecycle (Calendly), surfaced from `app.interview`.
 * Renders the booked slot + meeting details once a candidate picks a time,
 * reschedule history, and the cancellation reason — falling back to an
 * "awaiting booking" note while the invite is still outstanding.
 */
const InterviewSection = ({ interview }) => {
  const meta = interviewStateMeta(interview.state);
  const booked = interview.state === "booked";
  const canceled = interview.state === "canceled";
  const rescheduled = (interview.reschedule_count || 0) > 0;
  const tz = interview.invitee_timezone || undefined;

  return (
    <Section
      title="Interview"
      action={
        <Chip size="small" label={meta.label} color={meta.color} variant={booked ? "filled" : "outlined"} />
      }
    >
      <Stack spacing={2}>
        {booked && (
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, gap: 2 }}>
            <Field label="Scheduled slot">
              {formatDateTime(interview.scheduled_start_at, tz)}
              {interview.scheduled_end_at ? ` – ${formatTime(interview.scheduled_end_at, tz)}` : ""}
            </Field>
            <Field label="Meeting type">{locationLabel(interview.location_type)}</Field>
            <Field label="Booked by">{interview.invitee_email}</Field>
            <Field label="Candidate timezone">{tz || "—"}</Field>
          </Box>
        )}

        {booked && interview.join_url && (
          <Box>
            <Button
              variant="outlined"
              size="small"
              startIcon={<VideocamOutlinedIcon />}
              component="a"
              href={interview.join_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              Join meeting
            </Button>
          </Box>
        )}
        {booked && !interview.join_url && interview.location_text && (
          <Field label="Location">
            <Stack direction="row" spacing={0.75} sx={{ alignItems: "flex-start" }}>
              <PlaceOutlinedIcon sx={{ fontSize: 18, color: "text.secondary", mt: 0.25 }} />
              <span>{interview.location_text}</span>
            </Stack>
          </Field>
        )}

        {rescheduled && (
          <Box sx={{ bgcolor: "#fff8e8", border: "1px solid #f0e2bd", borderRadius: 1.5, p: 1.25 }}>
            <Typography variant="caption" sx={{ fontWeight: 700, display: "block" }}>
              Rescheduled {interview.reschedule_count}×
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {interview.previous_start_at ? `Previously ${formatDateTime(interview.previous_start_at, tz)}` : ""}
              {interview.rescheduled_at ? ` · changed ${timeAgo(interview.rescheduled_at)}` : ""}
            </Typography>
          </Box>
        )}

        {canceled && (
          <Box sx={{ bgcolor: "#fdecec", border: "1px solid #f4c7c7", borderRadius: 1.5, p: 1.25 }}>
            <Typography variant="caption" sx={{ fontWeight: 700, display: "block" }}>
              Canceled{interview.canceled_by ? ` by ${humanize(interview.canceled_by)}` : ""}
              {interview.canceled_at ? ` · ${timeAgo(interview.canceled_at)}` : ""}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "pre-wrap" }}>
              {interview.cancel_reason || "No reason provided."}
            </Typography>
          </Box>
        )}

        {!booked && !canceled && (
          <Typography variant="body2" color="text.secondary">
            Invite sent {timeAgo(interview.created_at)}. Awaiting the candidate to pick a slot.
            {interview.reminder_sent_at ? ` Reminder sent ${timeAgo(interview.reminder_sent_at)}.` : ""}
          </Typography>
        )}

        {interview.scheduling_url && (
          <Box>
            <Button
              variant="text"
              size="small"
              startIcon={<EventAvailableOutlinedIcon />}
              component="a"
              href={interview.scheduling_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {booked ? "View booking page" : "Open booking link"}
            </Button>
          </Box>
        )}

        {interview.last_error && (
          <Typography variant="caption" color="error.main">
            Last issue: {humanize(interview.last_error)}
          </Typography>
        )}
      </Stack>
    </Section>
  );
};

/** Static field caption sitting above each contact input (mock style). */
const FieldLabel = ({ children, required }) => (
  <Typography component="label" sx={{ display: "block", fontWeight: 600, fontSize: 11, color: "#1c2522", mb: 0.85 }}>
    {children}
    {required && (
      <Box component="span" sx={{ color: "#d24a39", ml: 0.5 }}>
        *
      </Box>
    )}
  </Typography>
);

/** Warm-gray filled input matching the contact-details mock. */
const contactFieldSx = {
  "& .MuiOutlinedInput-root": {
    bgcolor: "#F4F0E84D",
    borderRadius: "5px",
    height: 39,
    "& fieldset": { borderColor: "#E0DBD0" },
    "&:hover fieldset": { borderColor: "#E0DBD0" },
    "&.Mui-focused fieldset": { borderColor: GREEN, borderWidth: 1 },
  },
  "& .MuiOutlinedInput-input": { py: 0 },
};

/**
 * Editable contact details (name / email / phone).
 *
 * The backend only accepts edits while the application is in the
 * `vector_screen` / `hard_filter` stage; from `whatsapp` onward the automated
 * screening (and the interview invite) reach the candidate on these details,
 * so the form locks and explains why. The candidate row is shared across all
 * of that person's applications, so a save propagates everywhere.
 */
const DetailsTab = ({ appId, candidate, editable }) => {
  const update = useUpdateCandidateContact(appId);
  // Seed lazily from the candidate. The parent mounts this with `key={appId}`,
  // so switching candidates remounts the form fresh — no effect needed, and a
  // background refetch (the detail query polls while a score is pending) can't
  // clobber an in-progress edit.
  const [form, setForm] = useState(() => ({
    full_name: candidate?.full_name || "",
    email: candidate?.email || "",
    phone: candidate?.phone_e164 || "",
  }));
  const [saved, setSaved] = useState(false);

  const set = (key) => (e) => {
    setForm((f) => ({ ...f, [key]: e.target.value }));
    setSaved(false);
  };

  const name = form.full_name.trim();
  const email = form.email.trim();
  const phone = form.phone.trim();
  const hasContact = !!email || !!phone;
  const changed =
    name !== (candidate?.full_name || "") ||
    email !== (candidate?.email || "") ||
    phone !== (candidate?.phone_e164 || "");
  const canSave = editable && !!name && hasContact && changed && !update.isPending;

  const submit = (e) => {
    e.preventDefault();
    if (!canSave) return;
    update.mutate(
      { full_name: name, email: email || null, phone: phone || null },
      {
        onSuccess: (data) => {
          setSaved(true);
          // Reflect server normalisation (email lower-cased, phone → E.164).
          if (data?.candidate) {
            setForm({
              full_name: data.candidate.full_name || "",
              email: data.candidate.email || "",
              phone: data.candidate.phone_e164 || "",
            });
          }
        },
      },
    );
  };

  const disabled = !editable || update.isPending;

  return (
    <Box component="form" onSubmit={submit} sx={{ maxWidth: 720, mx: "auto" }}>
      {!editable && (
        <Alert severity="info" icon={<LockOutlinedIcon fontSize="inherit" />} sx={{ mb: 3 }}>
          Contact details are locked once WhatsApp screening begins. They can only be edited while the
          candidate is in the Applied or L1 (CV screened) stage.
        </Alert>
      )}

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, columnGap: 3, rowGap: 0 }}>
        <Box>
          <FieldLabel required>Full Name</FieldLabel>
          <TextField
            fullWidth
            value={form.full_name}
            onChange={set("full_name")}
            disabled={disabled}
            placeholder="Ali Saqib"
            inputProps={{ maxLength: 255 }}
            sx={contactFieldSx}
          />
        </Box>
        <Box>
          <FieldLabel>Email</FieldLabel>
          <TextField
            fullWidth
            type="email"
            value={form.email}
            onChange={set("email")}
            disabled={disabled}
            placeholder="aliSaqib@gmail.com"
            inputProps={{ maxLength: 254 }}
            error={!hasContact}
            helperText={!hasContact ? "Provide at least an email or a phone number." : " "}
            sx={contactFieldSx}
          />
        </Box>
        <Box>
          <FieldLabel>Phone</FieldLabel>
          <TextField
            fullWidth
            value={form.phone}
            onChange={set("phone")}
            disabled={disabled}
            placeholder="+923409297758"
            inputProps={{ maxLength: 40 }}
            helperText="Include the country code, e.g. +971501234567."
            sx={contactFieldSx}
          />
        </Box>
      </Box>

      {update.isError && (
        <Box sx={{ mt: 2.5 }}>
          <ErrorAlert error={update.error} />
        </Box>
      )}
      {saved && !update.isError && (
        <Alert severity="success" sx={{ mt: 2.5 }}>
          Contact details updated.
        </Alert>
      )}

      {editable && (
        <Box sx={{ display: "flex", justifyContent: "center", mt: 4 }}>
          <Button
            type="submit"
            variant="contained"
            endIcon={<CheckIcon />}
            disabled={!canSave}
            sx={{ bgcolor: GREEN, px: 4, py: 1.25, borderRadius: "5px", "&:hover": { bgcolor: GREEN_DARK } }}
          >
            {update.isPending ? "Saving…" : "Save Changes"}
          </Button>
        </Box>
      )}
    </Box>
  );
};

const DialogSkeleton = () => (
  <Stack spacing={2}>
    <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
      <Skeleton variant="circular" width={48} height={48} />
      <Box sx={{ flexGrow: 1 }}>
        <Skeleton variant="text" width="60%" height={26} />
        <Skeleton variant="text" width="40%" />
      </Box>
    </Stack>
    <Skeleton variant="rounded" height={80} />
    <Skeleton variant="rounded" height={120} />
    <Skeleton variant="rounded" height={160} />
  </Stack>
);

/** Pill-style tab strip matching the mock (rounded track, filled-green active). */
const pillTabsSx = {
  minHeight: 0,
  bgcolor: "#F4F0E84D",
  borderRadius: "5px",
  p: 0.5,
  "& .MuiTabs-indicator": { display: "none" },
  "& .MuiTabs-flexContainer": { gap: 0.5 },
  "& .MuiTab-root": {
    fontFamily: "var(--font-jakarta), system-ui, sans-serif",
    textTransform: "none",
    fontWeight: 600,
    fontSize: "14px",
    minHeight: 0,
    py: 1,
    px: { xs: 1.5, sm: 3 },
    borderRadius: "5px",
    color: "text.secondary",
    transition: "background-color .15s, color .15s",
  },
  "& .MuiTab-root.Mui-selected": { bgcolor: "#0F6E56", color: "#fff" },
};

/**
 * Modal with the full application + candidate record and pipeline actions.
 * Three tabs — Scoring (scores, interview, move controls), Contact Details
 * (editable contact), Candidate Profile (parsed CV, CV document, activity).
 * Pass `readOnly` (e.g. when viewing a pooled candidate) to drop the pipeline
 * controls and tabs, leaving a clean combined profile view.
 */
const CandidateDialog = ({ appId, open, onClose, readOnly = false }) => {
  const [reason, setReason] = useState("");
  const [waOpen, setWaOpen] = useState(false);
  // Move-to-pool confirmation dialog + its optional reason field.
  const [poolDialogOpen, setPoolDialogOpen] = useState(false);
  const [poolReason, setPoolReason] = useState("");
  const [tab, setTab] = useState(0); // 0 = Scoring, 1 = Contact Details, 2 = Candidate Profile

  // Reset to the Scoring tab whenever the dialog switches candidates. This is
  // the "adjust state during render" pattern (no effect) — React applies the
  // setState before painting, and it avoids a setState-in-effect.
  const [prevAppId, setPrevAppId] = useState(appId);
  if (appId !== prevAppId) {
    setPrevAppId(appId);
    setTab(0);
    // Also drop any half-filled move-to-pool dialog from the previous candidate.
    setPoolDialogOpen(false);
    setPoolReason("");
  }
  const { data: app, isLoading, isError, error } = useApplication(appId, { poll: true });
  const updateStage = useUpdateStage(appId);
  const updateStatus = useUpdateStatus(appId);
  const moveToPool = useMoveToPool();
  const resetMoveToPool = moveToPool.reset;

  // Clear any prior move error when the dialog switches candidates, since this
  // component stays mounted across openings. `reset` is stable in RQ.
  useEffect(() => {
    resetMoveToPool();
  }, [appId, resetMoveToPool]);

  const busy = updateStage.isPending || updateStatus.isPending;
  const nextStage = app ? NEXT_STAGE[app.stage] : null;
  const isActive = app?.status === "active";
  // The WhatsApp screening transcript exists once a candidate reaches L2
  // (the `whatsapp` stage) and stays relevant through later stages.
  const reachedWhatsApp =
    !!app && APPLICATION_STAGES.indexOf(app.stage) >= APPLICATION_STAGES.indexOf("whatsapp");

  // Screening results (eligibility answers + derived background-validation
  // score) only exist once the candidate reaches the WhatsApp stage, so gate
  // both fetches on that. The conversation carries the answers; the job's
  // question list lets us map each answer back to its `source_field`.
  const { data: convo } = useWhatsAppConversation(appId, { enabled: reachedWhatsApp, poll: true });
  const { data: waQuestions } = useWhatsAppQuestions(app?.job_id, { enabled: reachedWhatsApp });
  const screening = useMemo(
    () => (reachedWhatsApp ? deriveScreening(convo, waQuestions?.questions) : null),
    [reachedWhatsApp, convo, waQuestions],
  );

  // HR's manual interview evaluation (mark + comment), editable only while the
  // application is actively sitting at the interview stage — mirrors the
  // backend's stage guard.
  const interviewFeedback = app
    ? { score: app.interview_score, comment: app.interview_comment, scoredAt: app.interview_scored_at }
    : null;
  const interviewEditable = app?.stage === "interview" && app?.status === "active";
  // Once HR has recorded an interview mark, the booking-status Interview section
  // (invite sent / awaiting slot / booked details) is no longer useful — the
  // manual score lives in the scoring card — so we drop it.
  const hasInterviewScore = app?.interview_score != null;
  // Contact details are editable only through the hard_filter stage; from
  // whatsapp onward the screening flow owns the contact channel (mirrors the
  // backend's CONTACT_EDITABLE_STAGES gate).
  const contactEditable =
    !!app && APPLICATION_STAGES.indexOf(app.stage) <= APPLICATION_STAGES.indexOf("hard_filter");
  const showTabs = !readOnly;
  const eyebrow = readOnly
    ? "CANDIDATE PROFILE"
    : ["CANDIDATE SCORING", "CANDIDATE CONTACT DETAILS", "CANDIDATE PROFILE"][tab];

  const advance = () => {
    if (!nextStage) return;
    updateStage.mutate({ stage: nextStage, reason: reason.trim() || undefined });
    setReason("");
  };
  const setStatus = (status) => {
    updateStatus.mutate({ status, reason: reason.trim() || undefined });
    setReason("");
  };

  const candidate = app?.candidate;
  // Moving to the pool soft-archives this application (it leaves the active
  // board but survives as cross-job history), so once it succeeds there's
  // nothing left to show here — close and let the board refetch it out of view.
  const moveToPoolNow = () => {
    if (!appId) return;
    moveToPool.mutate(
      { appId, reason: poolReason },
      {
        onSuccess: () => {
          setPoolDialogOpen(false);
          onClose();
        },
      },
    );
  };

  // The stage/reject controls (Scoring tab only).
  const moveCandidate = app && (
    <Section
      title="Move candidate"
      titleSx={{
        fontFamily: "var(--font-jakarta)",
        fontWeight: 700,
        fontSize: "12px",
        lineHeight: "15px",
        letterSpacing: "1px",
        textTransform: "capitalize",
        color: "#0F6E56",
      }}
    >
      {app.rejection_reason && (
        <Typography variant="body2" color="error.main" sx={{ mb: 1.5 }}>
          {app.rejection_reason}
        </Typography>
      )}
      <TextField
        fullWidth
        size="small"
        multiline
        minRows={2}
        label="Reason (optional, recorded in audit log)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        inputProps={{ maxLength: 500 }}
        disabled={busy}
        sx={{ mb: 1.5, "& .MuiOutlinedInput-root": { borderRadius: "14px" } }}
      />
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, justifyContent: "center" }}>
        {isActive && nextStage && (
          <Button
            variant="contained"
            size="small"
            endIcon={<ArrowForwardIcon />}
            onClick={advance}
            disabled={busy}
            sx={{ py: 1, bgcolor: GREEN, "&:hover": { bgcolor: GREEN_DARK } }}
          >
            Advance to {stageLabel(nextStage)}
          </Button>
        )}
        {isActive && (
          <Button
            variant="outlined"
            size="small"
            color="error"
            startIcon={<BlockIcon />}
            onClick={() => setStatus("rejected")}
            disabled={busy}
            sx={{ py: 1 }}
          >
            Reject
          </Button>
        )}
        {app.status === "rejected" && (
          <Button
            variant="outlined"
            size="small"
            startIcon={<ReplayIcon />}
            onClick={() => setStatus("active")}
            disabled={busy}
          >
            Reactivate
          </Button>
        )}
      </Box>
      {(updateStage.isError || updateStatus.isError) && (
        <ErrorAlert error={updateStage.error || updateStatus.error} sx={{ mt: 1.5 }} />
      )}
    </Section>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="md"
      scroll="paper"
      slotProps={{ paper: { sx: { borderRadius: "5px" } } }}
    >
      <IconButton
        onClick={onClose}
        aria-label="Close"
        sx={{ position: "absolute", top: 10, right: 10, zIndex: 2, color: "#fff" }}
      >
        <CloseIcon />
      </IconButton>

      <DialogContent
        sx={{
          p: 0,
          // Uniform shrink of the dialog's secondary text + controls.
          "& .MuiTypography-body2": { fontSize: "0.72rem" },
          "& .MuiTypography-caption": { fontSize: "0.66rem" },
          "& .MuiTypography-overline": { fontSize: "0.6rem" },
          "& .MuiTypography-subtitle2": { fontSize: "0.75rem" },
          "& .MuiButton-root": { fontSize: "0.72rem" },
          "& .MuiChip-root": { fontSize: "0.66rem" },
          "& .MuiOutlinedInput-input": { fontSize: "0.655rem" },
          "& .MuiFormLabel-root": { fontSize: "0.655rem" },
          "& .MuiFormHelperText-root": { fontSize: "0.64rem" },
          "& .MuiAlert-message": { fontSize: "0.72rem" },
        }}
      >
        {isError ? (
          <Box sx={{ p: 3 }}>
            <ErrorAlert error={error} />
          </Box>
        ) : isLoading || !app ? (
          <Box sx={{ p: 3 }}>
            <DialogSkeleton />
          </Box>
        ) : (
          <Box>
            {/* Header banner */}
            <Box sx={{ bgcolor: GREEN, color: "#fff", px: { xs: 2, sm: 3 }, py: 2 }}>
              <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", pr: 4 }}>
                <Avatar sx={{ width: 42, height: 42, bgcolor: COLORS.gold, color: "#fff", fontWeight: 700, fontSize: 11 }}>
                  {initials(candidate?.full_name)}
                </Avatar>
                <Box sx={{ minWidth: 0 }}>
                  <Typography
                    sx={{
                      fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                      fontWeight: 700,
                      fontSize: "18px",
                      lineHeight: "22.5px",
                      letterSpacing: 0,
                      color: "#FFFFFF",
                    }}
                  >
                    {candidate?.full_name || "Candidate"}
                  </Typography>
                  <Typography
                    sx={{
                      fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                      fontWeight: 400,
                      fontSize: "10px",
                      lineHeight: "15px",
                      letterSpacing: "1px",
                      textTransform: "capitalize",
                      color: "#FFFFFF",
                    }}
                  >
                    Applied {timeAgo(app.created_at)}
                  </Typography>
                </Box>
              </Stack>
            </Box>

            {/* Body */}
            <Box sx={{ px: { xs: 2, sm: 3 }, py: 1.75 }}>
              {/* Eyebrow + email + actions */}
              <Stack
                direction="row"
                sx={{ justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 1.5 }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography
                    sx={{
                      fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                      fontWeight: 700,
                      fontSize: "12px",
                      lineHeight: "15px",
                      letterSpacing: "1px",
                      textTransform: "uppercase",
                      color: "#0F6E56",
                    }}
                  >
                    {eyebrow}
                  </Typography>
                  {candidate?.email && (
                    <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", mt: 0.5 }}>
                      <EmailOutlinedIcon sx={{ fontSize: 16, color: GREEN }} />
                      <Typography variant="body2" sx={{ color: GREEN, wordBreak: "break-all" }}>
                        {candidate.email}
                      </Typography>
                    </Stack>
                  )}
                </Box>
                {!readOnly && (
                  <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", rowGap: 1 }}>
                    {reachedWhatsApp && (
                      <Button
                        variant="outlined"
                        size="small"
                        color="success"
                        startIcon={<WhatsAppIcon />}
                        onClick={() => setWaOpen(true)}
                      >
                        WhatsApp chat
                      </Button>
                    )}
                    <Button
                      variant="contained"
                      disableElevation
                      endIcon={<PersonAddAltOutlinedIcon />}
                      onClick={() => setPoolDialogOpen(true)}
                      disabled={moveToPool.isPending || !appId}
                      sx={{
                        bgcolor: "#0F6E56",
                        color: "#fff",
                        textTransform: "none",
                        fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                        fontWeight: 700,
                        fontSize: "18px",
                        lineHeight: 1.2,
                        borderRadius: "5px",
                        px: 3,
                        py: 1.25,
                        "& .MuiButton-endIcon": { ml: 1.25 },
                        "&:hover": { bgcolor: GREEN_DARK },
                      }}
                    >
                      {moveToPool.isPending ? "Moving…" : "Move to Talent Pool"}
                    </Button>
                  </Stack>
                )}
              </Stack>

              {/* Tabs */}
              {showTabs && (
                <Box sx={{ display: "flex", justifyContent: "center", mt: 2 }}>
                  <Tabs value={tab} onChange={(_e, v) => setTab(v)} sx={pillTabsSx}>
                    <Tab label="Scoring" />
                    <Tab label="Contact Details" />
                    <Tab label="Candidate Profile" />
                  </Tabs>
                </Box>
              )}

              {/* Content */}
              {readOnly ? (
                <Stack spacing={2} sx={{ mt: 2 }} divider={<Divider flexItem />}>
                  <CvScoreCard
                    scores={app.scores}
                    stage={app.stage}
                    screening={screening}
                    appId={appId}
                    interviewFeedback={interviewFeedback}
                    interviewEditable={false}
                  />
                  {app.interview && !hasInterviewScore && <InterviewSection interview={app.interview} />}
                  <Stack spacing={2.5}>
                    <ParsedProfile profile={candidate?.parsed_profile} />
                    {app.cv_document?.blob_url && <OpenCvButton doc={app.cv_document} />}
                  </Stack>
                </Stack>
              ) : tab === 0 ? (
                <Stack spacing={2} sx={{ mt: 2 }} divider={<Divider flexItem />}>
                  <CvScoreCard
                    scores={app.scores}
                    stage={app.stage}
                    screening={screening}
                    appId={appId}
                    interviewFeedback={interviewFeedback}
                    interviewEditable={interviewEditable}
                  />
                  {app.interview && !hasInterviewScore && <InterviewSection interview={app.interview} />}
                  {moveCandidate}
                </Stack>
              ) : tab === 1 ? (
                <Box sx={{ mt: 2 }}>
                  <DetailsTab key={appId} appId={appId} candidate={candidate} editable={contactEditable} />
                </Box>
              ) : (
                <Stack spacing={2.5} sx={{ mt: 2 }}>
                  <RecentActivity candidateId={candidate?.id} />
                  <ParsedProfile profile={candidate?.parsed_profile} />
                  {app.cv_document?.blob_url && <OpenCvButton doc={app.cv_document} />}
                </Stack>
              )}
            </Box>
          </Box>
        )}
      </DialogContent>

      <WhatsAppDialog appId={appId} candidate={candidate} open={waOpen} onClose={() => setWaOpen(false)} />

      {/* Move-to-pool confirmation: capture an optional reason, saved against
          this job stint and shown later in the candidate's history. */}
      <Dialog
        open={poolDialogOpen}
        onClose={() => (moveToPool.isPending ? null : setPoolDialogOpen(false))}
        maxWidth="xs"
        fullWidth
        PaperProps={{ sx: { borderRadius: "16px" } }}
      >
        <DialogTitle sx={{ fontFamily: "var(--font-jakarta)", fontWeight: 700, color: GREEN }}>
          Move to Talent Pool
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ color: "text.secondary", mb: 2 }}>
            This archives the candidate on this job (their scores and screening are
            kept as history) and adds them to the talent pool. Add an optional note
            on why — it’s saved against this job so you can see it later.
          </Typography>
          <TextField
            fullWidth
            size="small"
            multiline
            minRows={2}
            autoFocus
            label="Reason (optional)"
            placeholder="e.g. Strong profile, but role was put on hold"
            value={poolReason}
            onChange={(e) => setPoolReason(e.target.value)}
            inputProps={{ maxLength: 500 }}
            disabled={moveToPool.isPending}
            sx={{ "& .MuiOutlinedInput-root": { borderRadius: "14px" } }}
          />
          {moveToPool.isError && <ErrorAlert error={moveToPool.error} sx={{ mt: 1.5 }} />}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2.5 }}>
          <Button
            onClick={() => {
              setPoolDialogOpen(false);
              resetMoveToPool();
            }}
            disabled={moveToPool.isPending}
            sx={{ textTransform: "none", color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            disableElevation
            endIcon={<PersonAddAltOutlinedIcon />}
            onClick={moveToPoolNow}
            disabled={moveToPool.isPending || !appId}
            sx={{
              bgcolor: "#0F6E56",
              color: "#fff",
              textTransform: "none",
              fontWeight: 700,
              borderRadius: "8px",
              px: 2.5,
              "&:hover": { bgcolor: GREEN_DARK },
            }}
          >
            {moveToPool.isPending ? "Moving…" : "Move to Pool"}
          </Button>
        </DialogActions>
      </Dialog>
    </Dialog>
  );
};

export default CandidateDialog;
