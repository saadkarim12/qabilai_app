"use client";

import { useState } from "react";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import Divider from "@mui/material/Divider";
import Collapse from "@mui/material/Collapse";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import LinearProgress from "@mui/material/LinearProgress";
import Skeleton from "@mui/material/Skeleton";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import WarningAmberOutlinedIcon from "@mui/icons-material/WarningAmberOutlined";
import AccessTimeOutlinedIcon from "@mui/icons-material/AccessTimeOutlined";
import SmartToyOutlinedIcon from "@mui/icons-material/SmartToyOutlined";
import ManageSearchOutlinedIcon from "@mui/icons-material/ManageSearchOutlined";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutlined";
import WorkOutlineIcon from "@mui/icons-material/WorkOutlineOutlined";
import VerifiedUserOutlinedIcon from "@mui/icons-material/VerifiedUserOutlined";
import FactCheckOutlinedIcon from "@mui/icons-material/FactCheckOutlined";
import { toScore, scoreBand, bandHex, timeAgo, humanize, APPLICATION_STAGES } from "@/lib/kabil/constants";
import { useSubmitInterviewFeedback } from "@/lib/kabil/queries";
import ErrorAlert from "@/components/ErrorAlert";

/** HR interview mark bounds — mirrors the backend `INTERVIEW_SCORE_*` thresholds. */
const INTERVIEW_SCORE_MIN = 0;
const INTERVIEW_SCORE_MAX = 100;

const asArray = (v) => (Array.isArray(v) ? v : []);
const findScore = (scores, type) => asArray(scores).find((s) => s?.score_type === type);

/** The authenticity `breakdown` is now the full aggregator dump:
 *  `{ score, band, breakdown: { <signal>: … }, top_concerns, rationale }`.
 *  Older rows stored the signal map at the top level. Return the signal map
 *  either way so callers can index it by SignalKey. */
const pickSignals = (bd) =>
  (bd && typeof bd.breakdown === "object" && bd.breakdown !== null && !Array.isArray(bd.breakdown)
    ? bd.breakdown
    : bd) || null;

/** One signal's recruiter-facing sentence — the LLM `finding`, falling back to
 *  the legacy `reasons` list. */
const signalText = (sig) => sig?.finding || asArray(sig?.reasons)[0] || "";

/** 0–100 → "65" or "37.8" (one decimal only when it isn't whole). */
const fmtPct = (v) => (v == null ? "—" : Number.isInteger(v) ? `${v}` : v.toFixed(1));

/* ── Shared visual language ────────────────────────────────────────────────── */

const GREEN = "#0F6E56";
const CARD_BG = "#FBF9F2";
const CARD_BORDER = "#ECE5D6";
const TRACK = "#E7E1D2";

// Shared title style for every score row (CV Score, Profile Match, Background
// Validation, Eligibility Questions, Interview Scoring, Shortlist, …) so they
// all read identically — Inter 600 / 14px / 21px in the near-black brand ink.
const ROW_TITLE_SX = {
  fontFamily: "var(--font-sans), system-ui, Arial, sans-serif",
  fontWeight: 600,
  fontSize: "14px",
  lineHeight: "21px",
  letterSpacing: 0,
  color: "#2C2C2A",
};

const cardSx = {
  bgcolor: "#F4F0E84D",
  border: `1px solid ${CARD_BORDER}`,
  borderRadius: "14px",
  overflow: "hidden",
};

/** The five authenticity sub-signals, in the order the recruiter reads them.
 *  Keys are the backend SignalKey values; labels/tips are recruiter-facing. */
const TRUST_SIGNALS = [
  {
    key: "consistency",
    label: "Claims vs Evidence",
    tip: "Does the experience claimed match the actual work history?",
  },
  {
    key: "specificity",
    label: "Detail Quality",
    tip: "Are specific projects, clients, and outcomes mentioned?",
  },
  {
    key: "timeline_coherence",
    label: "Skills Traceability",
    tip: "Can each listed skill be found in the actual work history?",
  },
  {
    key: "linguistic_genericity",
    label: "AI Language Check",
    tip: "Does the CV lean on generic AI / corporate buzzwords?",
  },
  {
    key: "structural_templating",
    label: "AI Formatting Check",
    tip: "Do the bullets / punctuation show AI-generated templating?",
  },
  {
    key: "jd_keyword_mirroring",
    label: "Template Match",
    tip: "Does the CV mirror a generic role template rather than real, specific experience?",
  },
];

/** CV authenticity verdict bands — score → recruiter-facing badge. */
const TRUST_BADGES = [
  { min: 75, label: "Authentic", color: "success" },
  { min: 50, label: "Review", color: "warning" },
  { min: 0, label: "Likely Fabricated", color: "error" },
];
const trustBadge = (value) => TRUST_BADGES.find((b) => value >= b.min) || TRUST_BADGES[2];

/** Solid, band-coloured score disc (white numeral), mirroring the mock. */
export const ScoreBadge = ({ value, size = 44 }) => {
  const has = value != null;
  const color = has ? bandHex(scoreBand(value).color) : "#c4c8c4";
  return (
    <Box
      sx={{
        width: size,
        height: size,
        borderRadius: "50%",
        bgcolor: color,
        color: "#fff",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: 700,
        fontSize: 13,
        flexShrink: 0,
      }}
    >
      {has ? Math.round(value) : "—"}
    </Box>
  );
};

/** Green "Breakdown ⌄" toggle that sits in a card footer. */
export const BreakdownToggle = ({ open, onClick }) => (
  <Button
    onClick={onClick}
    disableRipple
    size="small"
    endIcon={
      <ExpandMoreIcon
        sx={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .2s" }}
      />
    }
    sx={{ color: GREEN, fontWeight: 700, minWidth: 0, p: 0, "&:hover": { bgcolor: "transparent" } }}
  >
    {open ? "Hide Breakdown" : "Breakdown"}
  </Button>
);
/* ── Rows (stacked inside the single Credibility Check card) ─────────────────── */

/** One row inside the unified card; a hairline separates it from the next (the
 *  last row has none). */
const RowShell = ({ children }) => (
  <Box sx={{ px: 2, py: 1.75, "&:not(:last-of-type)": { borderBottom: `1px solid ${CARD_BORDER}` } }}>
    {children}
  </Box>
);

/** A numeric score row (CV Score, Background Validation): title + meter + score
 *  disc, with an optional expandable breakdown drawer + computed footer. */
const MeterRow = ({ label, value, showFooter = true, defaultOpen = false, children }) => {
  const [open, setOpen] = useState(defaultOpen);
  const has = value != null;
  const color = has ? bandHex(scoreBand(value).color) : TRACK;
  const hasBreakdown = !!children;
  return (
    <RowShell>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ ...ROW_TITLE_SX, mb: 1 }}>{label}</Typography>
          <LinearProgress
            variant="determinate"
            value={has ? Math.min(100, Math.max(0, value)) : 0}
            sx={{
              height: 8,
              borderRadius: 4,
              bgcolor: TRACK,
              "& .MuiLinearProgress-bar": { bgcolor: color, borderRadius: 4 },
            }}
          />
        </Box>
        <ScoreBadge value={value} />
      </Stack>

      {hasBreakdown && (
        <Collapse in={open} timeout="auto" unmountOnExit>
          <Box sx={{ pt: 1.75 }}>
            <Stack spacing={2.5}>{children}</Stack>
          </Box>
        </Collapse>
      )}

      {/* Footer sits at the bottom, so expanded detail nests above it. */}
      {showFooter && hasBreakdown && (
        <Stack direction="row" sx={{ justifyContent: "flex-end", alignItems: "center", pt: 1 }}>
          <BreakdownToggle open={open} onClick={() => setOpen((o) => !o)} />
        </Stack>
      )}
    </RowShell>
  );
};

/** CV Authenticity verdict row: title + coloured band label + concern summary. */
const AuthenticityRow = ({ value, summary }) => {
  const badge = trustBadge(value);
  const labelColor = bandHex(badge.color);
  return (
    <RowShell>
      <Stack direction="row" spacing={2} sx={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <Typography sx={ROW_TITLE_SX}>CV Authenticity</Typography>
        <Typography sx={{ fontWeight: 700, color: labelColor, flexShrink: 0, whiteSpace: "nowrap" }}>
          {badge.label}
        </Typography>
      </Stack>
      {summary && (
        <Typography
          sx={{
            mt: 1,
            fontFamily: "var(--font-sans), system-ui, Arial, sans-serif",
            fontWeight: 400,
            fontSize: "12px",
            lineHeight: "18px",
            letterSpacing: 0,
            color: "#9CA3AF",
          }}
        >
          {summary}
        </Typography>
      )}
    </RowShell>
  );
};

/** Eligibility Questions row: the candidate's verbatim answers to the six fixed
 *  WhatsApp screening questions, shown as an open-by-default breakdown. */
const EligibilityRow = ({ items }) => {
  const [open, setOpen] = useState(false); // collapsed until the recruiter opens it
  return (
    <RowShell>
      <Typography sx={ROW_TITLE_SX}>Eligibility Questions</Typography>
      <Collapse in={open} timeout="auto" unmountOnExit>
        <Stack sx={{ mt: 1.25 }}>
          {items.map((it) => (
            <Stack
              key={it.label}
              direction="row"
              spacing={2}
              sx={{
                alignItems: "center",
                justifyContent: "space-between",
                py: 1.75,
                borderBottom: "1px solid",
                borderColor: "action.hover",
                "&:last-of-type": { border: 0 },
              }}
            >
              <Stack direction="row" spacing={1.25} sx={{ alignItems: "center", minWidth: 0 }}>
                <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: GREEN, flexShrink: 0 }} />
                <Typography
                  sx={{
                    fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                    fontWeight: 600,
                    fontSize: "14px",
                    lineHeight: "20px",
                    letterSpacing: 0,
                    color: "#1C4A3E",
                  }}
                >
                  {it.label}
                </Typography>
              </Stack>
              <Typography
                sx={{
                  fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                  fontWeight: 600,
                  fontSize: "11px",
                  lineHeight: "16.5px",
                  letterSpacing: 0,
                  textAlign: "center",
                  color: it.value ? "#0F6E56" : "text.disabled",
                  wordBreak: "break-word",
                }}
              >
                {it.value ?? "—"}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Collapse>
      <Stack direction="row" sx={{ justifyContent: "flex-end", alignItems: "center", pt: 1 }}>
        <BreakdownToggle open={open} onClick={() => setOpen((o) => !o)} />
      </Stack>
    </RowShell>
  );
};

/** Empty placeholder row when a score family hasn't been computed yet. */
const PendingRow = ({ label }) => (
  <RowShell>
    <Typography sx={ROW_TITLE_SX}>{label}</Typography>
    <Typography variant="caption" color="text.secondary">
      Not computed yet.
    </Typography>
  </RowShell>
);

/** A score row whose value for the current stage is still being fetched: the
 *  title stays, but the bar + score disc are skeletons until the score lands. */
const MeterSkeletonRow = ({ label }) => (
  <RowShell>
    <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ ...ROW_TITLE_SX, mb: 1 }}>{label}</Typography>
        <Skeleton variant="rounded" height={8} sx={{ borderRadius: 4 }} />
      </Box>
      <Skeleton variant="circular" width={44} height={44} sx={{ flexShrink: 0 }} />
    </Stack>
  </RowShell>
);

/** Interview Scoring row: HR's manual post-interview mark + comment. The
 *  breakdown stays collapsed by default; the user opens it to see/enter the
 *  mark. Once a mark exists it also shows as a score disc. While the app is at
 *  the interview stage HR can open the form to edit; past that it's read-only
 *  (the comment, if any). */
const InterviewScoringRow = ({ appId, feedback, editable }) => {
  const scoreVal = toScore(feedback?.score);
  const has = scoreVal != null;
  const [open, setOpen] = useState(false); // collapsed until the user opens it
  const [marks, setMarks] = useState(has ? String(Math.round(scoreVal)) : "");
  const [comment, setComment] = useState(feedback?.comment || "");
  const submit = useSubmitInterviewFeedback(appId);

  const trimmed = marks.trim();
  const marksNum = trimmed === "" ? null : Number(trimmed);
  const marksValid =
    marksNum != null &&
    Number.isFinite(marksNum) &&
    marksNum >= INTERVIEW_SCORE_MIN &&
    marksNum <= INTERVIEW_SCORE_MAX;
  const canSave = editable && marksValid && !submit.isPending;

  const save = () => {
    if (!canSave) return;
    submit.mutate(
      { score: marksNum, comment: comment.trim() || null },
      { onSuccess: () => setOpen(false) },
    );
  };

  const hasDrawer = editable || !!feedback?.comment;

  return (
    <RowShell>
      <Stack direction="row" spacing={2} sx={{ alignItems: "center", justifyContent: "space-between" }}>
        <Box sx={{ minWidth: 0 }}>
          <Typography sx={ROW_TITLE_SX}>Interview Scoring</Typography>
          <Typography variant="caption" color="text.secondary">
            {has ? (feedback?.scoredAt ? timeAgo(feedback.scoredAt) : "Scored") : "Not scored yet"}
          </Typography>
        </Box>
        {has && <ScoreBadge value={scoreVal} />}
      </Stack>

      {hasDrawer && (
        <Collapse in={open} timeout="auto" unmountOnExit>
          <Box sx={{ pt: 1.5 }}>
            {editable ? (
              <Stack spacing={1.5}>
                <TextField
                  label="Marks (0–100)"
                  value={marks}
                  onChange={(e) => setMarks(e.target.value)}
                  type="number"
                  size="small"
                  inputProps={{ min: INTERVIEW_SCORE_MIN, max: INTERVIEW_SCORE_MAX }}
                  error={trimmed !== "" && !marksValid}
                  helperText={trimmed !== "" && !marksValid ? "Enter a number from 0 to 100." : " "}
                  sx={{ maxWidth: 200 }}
                />
                <TextField
                  label="Comment (optional)"
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  multiline
                  minRows={2}
                  size="small"
                  inputProps={{ maxLength: 2000 }}
                  fullWidth
                />
                {submit.isError && <ErrorAlert error={submit.error} />}
                <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
                  <Button
                    variant="contained"
                    size="small"
                    onClick={save}
                    disabled={!canSave}
                    sx={{ bgcolor: GREEN, "&:hover": { bgcolor: "#0c5a46" } }}
                  >
                    {submit.isPending ? "Saving…" : has ? "Update marks" : "Save marks"}
                  </Button>
                </Box>
              </Stack>
            ) : (
              feedback?.comment && (
                <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                  {feedback.comment}
                </Typography>
              )
            )}
          </Box>
        </Collapse>
      )}

      {hasDrawer && (
        <Stack direction="row" sx={{ justifyContent: "flex-end", pt: 1 }}>
          <BreakdownToggle open={open} onClick={() => setOpen((o) => !o)} />
        </Stack>
      )}
    </RowShell>
  );
};

/** Final-shortlist row: a plain verdict, no score or breakdown. */
const ShortlistRow = () => (
  <RowShell>
    <Stack direction="row" spacing={2} sx={{ alignItems: "center", justifyContent: "space-between" }}>
      <Typography sx={ROW_TITLE_SX}>Shortlist</Typography>
      <Typography sx={{ fontWeight: 700, color: "#0F6E56", whiteSpace: "nowrap" }}>Shortlisted</Typography>
    </Stack>
  </RowShell>
);

/* ── Breakdown bodies ──────────────────────────────────────────────────────── */

/** One labelled signal inside a detailed breakdown (label + reasons, no score). */
const SignalBar = ({ label, tip, children }) => (
  <Box>
    {tip ? (
      <Tooltip title={tip} arrow placement="top">
        <Typography
          variant="caption"
          sx={{
            fontWeight: 700,
            borderBottom: "1px dotted",
            borderColor: "text.disabled",
            cursor: "help",
            display: "inline-block",
            mb: 0.5,
          }}
        >
          {label}
        </Typography>
      </Tooltip>
    ) : (
      <Typography variant="caption" sx={{ fontWeight: 700, display: "block", mb: 0.5 }}>
        {label}
      </Typography>
    )}
    {children}
  </Box>
);

/** Small wrapping row of coloured skill chips with an optional lead label. */
const ChipRow = ({ lead, items, color, leadingIcon, max }) => {
  const list = asArray(items);
  if (!list.length) return null;
  const shown = max != null ? list.slice(0, max) : list;
  const extra = list.length - shown.length;
  return (
    <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, alignItems: "center", mt: 0.75 }}>
      {lead && (
        <Typography variant="caption" color="text.secondary" sx={{ mr: 0.25 }}>
          {lead}
        </Typography>
      )}
      {shown.map((s, i) => (
        <Chip
          key={`${s}-${i}`}
          size="small"
          color={color}
          variant="outlined"
          icon={leadingIcon}
          label={String(s)}
          sx={{ height: 22, "& .MuiChip-label": { px: 0.75, fontSize: 11 } }}
        />
      ))}
      {extra > 0 && (
        <Chip
          size="small"
          variant="outlined"
          label={`+${extra} more`}
          sx={{ height: 22, "& .MuiChip-label": { px: 0.75, fontSize: 11 } }}
        />
      )}
    </Box>
  );
};


/** Background Validation breakdown: one row per scored background-validation
 *  question — the question, the candidate's answer, and its 0–100 relevance. */
const BgValidationBreakdown = ({ items }) => (
  <Stack spacing={2.5} divider={<Divider sx={{ borderColor: CARD_BORDER }} />}>
    {items.map((it, i) => (
      <Box key={i}>
        <Stack direction="row" spacing={2} sx={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <Stack direction="row" spacing={1.25} sx={{ alignItems: "flex-start", minWidth: 0 }}>
            {/* Center the dot on the first line (question line-height is 20px). */}
            <Box sx={{ height: "20px", display: "flex", alignItems: "center", flexShrink: 0 }}>
              <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: "#1C4A3E" }} />
            </Box>
            <Typography
              sx={{
                fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                fontWeight: 600,
                fontSize: "12px",
                lineHeight: "20px",
                letterSpacing: 0,
                color: "#1C4A3E",
              }}
            >
              {it.question}
            </Typography>
          </Stack>
          {it.score != null && (
            <Typography
              sx={{
                fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                fontWeight: 600,
                fontSize: "11px",
                lineHeight: "16.5px",
                letterSpacing: 0,
                textAlign: "center",
                color: "#0F6E56",
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              {it.score}
            </Typography>
          )}
        </Stack>
        {it.answer && (
          <Typography
            sx={{
              ml: "18px",
              mt: 0.5,
              fontFamily: "var(--font-jakarta), system-ui, sans-serif",
              fontWeight: 400,
              fontSize: "10px",
              lineHeight: "16px",
              letterSpacing: 0,
              color: "#000000B2",
            }}
          >
            {it.answer}
          </Typography>
        )}
      </Box>
    ))}
  </Stack>
);
/* The hard-filter (CV Score) rubric: an open-ended `{ criterion: … }` map where
 * each criterion typically carries a numeric `score` (a percent string per the
 * API) plus a prose reason — but the leaf shape and field names vary. Extract
 * the score + reason defensively, and render any remaining structure generically
 * so nothing is ever silently dropped. Meta leaves (model/version/…) are skipped. */
const RUBRIC_META_KEYS = new Set(["computed_by", "model", "model_used", "version", "id"]);
/** Rubric leaf keys deliberately hidden from the CV Score breakdown UI. */
const RUBRIC_HIDDEN_KEYS = new Set(["gap", "gaps", "evidence"]);
const SCORE_KEYS = ["score", "points", "points_awarded", "awarded", "rating", "mark", "value"];
const WEIGHT_KEYS = ["weight", "max", "max_points", "out_of"];
const REASON_KEYS = [
  "reason", "reasoning", "explanation", "description", "detail", "details",
  "notes", "note", "rationale", "comment", "comments", "justification",
  "summary", "text", "feedback", "remark", "remarks", "analysis",
];
const NUMERIC_RE = /^\s*\d+(\.\d+)?%?\s*$/;
const SMALL_WORDS = new Set(["and", "or", "of", "the", "to", "a", "for", "in", "on", "with"]);

const isObj = (v) => v != null && typeof v === "object" && !Array.isArray(v);
const firstKey = (obj, keys) => keys.find((k) => obj[k] != null && obj[k] !== "");

/** snake_case / loose key → Title Case ("role_and_seniority" → "Role and Seniority"). */
const titleCase = (key) =>
  String(key)
    .replace(/_/g, " ")
    .trim()
    .split(/\s+/)
    .map((w, i) =>
      i > 0 && SMALL_WORDS.has(w.toLowerCase())
        ? w.toLowerCase()
        : w.charAt(0).toUpperCase() + w.slice(1),
    )
    .join(" ");

const padScore = (n) => (n == null ? null : String(Math.round(n)).padStart(2, "0"));

/** The numeric points for a rubric criterion, whatever leaf shape it takes. */
const rubricScore = (v) => {
  if (typeof v === "number") return v;
  if (typeof v === "string") return NUMERIC_RE.test(v) ? toScore(v) : null;
  if (isObj(v)) {
    const k = firstKey(v, SCORE_KEYS);
    return k ? toScore(v[k]) : null;
  }
  return null;
};

/** The reason / explanation prose for a rubric criterion. */
const rubricText = (v) => {
  if (typeof v === "string") return NUMERIC_RE.test(v) ? "" : v;
  if (Array.isArray(v)) return v.every((x) => !isObj(x) && !Array.isArray(x)) ? v.join(", ") : "";
  if (isObj(v)) {
    const k = firstKey(v, REASON_KEYS);
    if (k) return String(v[k]);
    if (Array.isArray(v.reasons)) return v.reasons.join(" ");
    if (Array.isArray(v.evidence)) return v.evidence.join(" ");
  }
  return "";
};

/** A primitive leaf → display string ("" for objects/arrays, handled by Tree). */
const leafString = (v) => {
  if (v == null || v === "") return "—";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return v.every((x) => !isObj(x)) ? v.join(", ") : "";
  if (typeof v === "object") return "";
  return String(v);
};

/** Generic indented renderer for any leftover nested structure. */
const Tree = ({ data }) => {
  const entries = Array.isArray(data) ? data.map((v, i) => [String(i + 1), v]) : Object.entries(data || {});
  return (
    <Stack spacing={0.75} sx={{ pl: 2.25, mt: 0.75 }}>
      {entries.map(([k, v]) =>
        isObj(v) || (Array.isArray(v) && v.some(isObj)) ? (
          <Box key={k}>
            <Typography variant="caption" sx={{ fontWeight: 700 }}>
              {titleCase(k)}
            </Typography>
            <Tree data={v} />
          </Box>
        ) : (
          <Stack key={k} direction="row" spacing={1.5} sx={{ justifyContent: "space-between" }}>
            <Typography variant="caption" color="text.secondary">
              {titleCase(k)}
            </Typography>
            <Typography variant="caption" sx={{ fontWeight: 600, textAlign: "right", wordBreak: "break-word" }}>
              {leafString(v) || "—"}
            </Typography>
          </Stack>
        ),
      )}
    </Stack>
  );
};

/** Keys already surfaced as score / weight / reason — dropped from the leftover Tree. */
const consumedKeys = (v) => {
  const used = new Set();
  if (isObj(v)) {
    const sk = firstKey(v, SCORE_KEYS);
    const wk = firstKey(v, WEIGHT_KEYS);
    const rk = firstKey(v, REASON_KEYS);
    if (sk) used.add(sk);
    if (wk) used.add(wk);
    if (rk) used.add(rk);
  }
  return used;
};

/* The five documented hard-filter signals (ENUMS.md: skills / experience /
 * industry / regional / role-seniority), in weight order, mapped to the
 * recruiter-facing labels from the mock. Keys are matched flexibly so minor
 * backend spelling differences (e.g. `role_seniority` vs `role_and_seniority`)
 * still resolve. */
const HARD_FILTER_SIGNALS = [
  { keys: ["skills", "skill"], label: "Skills" },
  { keys: ["experience"], label: "Experience" },
  { keys: ["industry", "industry_match"], label: "Industry Match" },
  { keys: ["regional", "region", "regional_experience"], label: "Regional Experience" },
  {
    keys: ["role_seniority", "role_and_seniority", "role_and_seniority_alignment", "seniority", "role"],
    label: "Role and Seniority Alignment",
  },
];
const normKey = (k) => String(k).toLowerCase().replace(/[\s-]+/g, "_");

/** Order the breakdown into the documented signals first (with polished labels),
 *  then any leftover non-meta keys (title-cased) so nothing is lost. */
const orderRubric = (data) => {
  // The signals live under a `signals` wrapper; fall back to the root otherwise.
  const source = isObj(data?.signals) ? data.signals : data;
  const entries = Object.entries(source || {}).filter(([k]) => !RUBRIC_META_KEYS.has(k));
  const used = new Set();
  const rows = [];
  for (const sig of HARD_FILTER_SIGNALS) {
    const hit = entries.find(([k]) => !used.has(k) && sig.keys.includes(normKey(k)));
    if (hit) {
      used.add(hit[0]);
      rows.push({ key: hit[0], label: sig.label, value: hit[1] });
    }
  }
  for (const [k, v] of entries) {
    if (!used.has(k)) rows.push({ key: k, label: titleCase(k), value: v });
  }
  return rows;
};

const RubricRow = ({ label, value }) => {
  const raw = rubricScore(value);
  const weight = isObj(value) ? toScore(value.weight ?? value.max ?? value.max_points ?? value.out_of) : null;
  // The mock shows each signal's weighted point contribution (these sum to the
  // overall CV Score). Fall back to the raw score when there's no weight.
  const score = padScore(raw != null && weight != null ? (raw / 100) * weight : raw);
  // The criterion's weightage (max points it can contribute), shown as a percent
  // next to the label and as the smaller denominator in the "score/weight" value.
  const weightPct = weight != null ? Math.round(weight) : null;
  const text = rubricText(value);
  // Whatever the score + reason didn't capture, surface it generically.
  let leftover = null;
  if (isObj(value)) {
    const used = consumedKeys(value);
    const rest = Object.fromEntries(
      Object.entries(value).filter(([k]) => !used.has(k) && !RUBRIC_HIDDEN_KEYS.has(k.toLowerCase())),
    );
    if (Object.keys(rest).length) leftover = rest;
  } else if (Array.isArray(value) && !text) {
    leftover = value;
  }
  const primitiveOnly = !text && !leftover && score == null;
  return (
    <Box>
      <Stack direction="row" spacing={1} sx={{ justifyContent: "space-between", alignItems: "center" }}>
        <Stack direction="row" spacing={1.25} sx={{ alignItems: "center", minWidth: 0 }}>
          <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: GREEN, flexShrink: 0 }} />
          <Typography sx={{ fontWeight: 700, color: GREEN, fontSize: "0.725rem" }}>{label}</Typography>
          {weightPct != null && (
            <Typography sx={{ fontWeight: 600, color: "#9CA3AF", fontSize: "0.7rem", flexShrink: 0 }}>
              {`${weightPct}%`}
            </Typography>
          )}
        </Stack>
        {score != null && (
          <Typography sx={{ fontWeight: 700, color: GREEN, flexShrink: 0 }}>
            {score}
            {weightPct != null && (
              <Box component="span" sx={{ fontSize: "0.75em", fontWeight: 600, color: "#9CA3AF" }}>
                {`/${weightPct}`}
              </Box>
            )}
          </Typography>
        )}
      </Stack>
      {text && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75, ml: 2.25, lineHeight: 1.6 }}>
          {text}
        </Typography>
      )}
      {primitiveOnly && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75, ml: 2.25, lineHeight: 1.6 }}>
          {leafString(value)}
        </Typography>
      )}
      {leftover && (
        <Box sx={{ ml: 2.25 }}>
          <Tree data={leftover} />
        </Box>
      )}
    </Box>
  );
};

export const RubricBreakdown = ({ data }) => {
  const rows = orderRubric(data);
  if (!rows.length)
    return (
      <Typography variant="caption" color="text.disabled">
        No breakdown details.
      </Typography>
    );
  return (
    <Stack spacing={2.5} divider={<Divider sx={{ borderColor: CARD_BORDER }} />}>
      {rows.map((r) => (
        <RubricRow key={r.key} label={r.label} value={r.value} />
      ))}
    </Stack>
  );
};

/* ── Key flags + screening summary (folded into the breakdown drawers) ──────── */

const FlagRow = ({ icon, color, label, tag }) => (
  <Stack
    direction="row"
    spacing={1.5}
    sx={{
      alignItems: "center",
      py: 1,
      borderBottom: "1px solid",
      borderColor: "action.hover",
      "&:last-of-type": { border: 0 },
    }}
  >
    <Box
      sx={{
        width: 30,
        height: 30,
        borderRadius: 1.5,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: `${color}.main`,
        bgcolor: `${color}.light`,
        opacity: 0.95,
      }}
    >
      {icon}
    </Box>
    <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
      {label}
    </Typography>
    <Typography variant="caption" sx={{ fontWeight: 700, color: `${color}.main` }}>
      {tag}
    </Typography>
  </Stack>
);

/** Trim a long LLM `finding` down to a glanceable one-liner for the flag rows. */
const clip = (s, n = 150) => (s && s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

/** Per-signal icon + fallback copy for the "key flags at a glance" rows, in the
 *  order a recruiter reads them. Concern flags are generated for any signal that
 *  falls below the "solid" bar (score < 75). */
const CONCERN_FLAG_META = [
  ["consistency", AccessTimeOutlinedIcon, "Experience claims may exceed documented history"],
  ["specificity", FactCheckOutlinedIcon, "Accomplishments lack concrete metrics or named systems"],
  ["timeline_coherence", ManageSearchOutlinedIcon, "Listed skills can't be traced to the work history"],
  ["linguistic_genericity", SmartToyOutlinedIcon, "Language reads generic / AI-typical"],
  ["structural_templating", SmartToyOutlinedIcon, "Formatting matches AI-template patterns"],
  ["jd_keyword_mirroring", WorkOutlineIcon, "CV mirrors a generic role template"],
];

/** Build the "key flags at a glance" rows from both breakdowns. */
const buildFlags = (jd, trust) => {
  const flags = [];
  const signals = pickSignals(trust);

  const total = jd?.required_skills_total;
  const matched = jd?.required_skills_matched;
  if (total != null && matched != null) {
    const missing = total - matched;
    if (missing > 0) {
      const critical = matched / total < 0.5;
      flags.push({
        icon: <WarningAmberOutlinedIcon fontSize="small" />,
        color: critical ? "error" : "warning",
        label: `${missing} of ${total} required skills missing from CV`,
        tag: critical ? "Critical" : "Warning",
      });
    }
  }

  // One flag per authenticity signal that dipped below the "solid" bar, using
  // the LLM's own evidence (`finding`) as the label.
  for (const [key, Icon, fallback] of CONCERN_FLAG_META) {
    const sig = signals?.[key];
    const score = toScore(sig?.score);
    if (score == null || score >= 75) continue;
    flags.push({
      icon: <Icon fontSize="small" />,
      color: score < 50 ? "error" : "warning",
      label: clip(signalText(sig)) || fallback,
      tag: score < 50 ? "Critical" : "Warning",
    });
  }

  // A positive note when the CV is genuinely specific.
  const specificity = toScore(signals?.specificity?.score);
  if (specificity != null && specificity >= 75) {
    flags.push({
      icon: <CheckCircleOutlineIcon fontSize="small" />,
      color: "success",
      label: clip(signalText(signals?.specificity)) || "Names specific clients, projects and outcomes",
      tag: "Good",
    });
  }

  return flags;
};

const KeyFlags = ({ flags }) => (
  <Box sx={{ border: `1px solid ${CARD_BORDER}`, borderRadius: 2, p: 2, bgcolor: CARD_BG }}>
    <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
      Key flags at a glance
    </Typography>
    {flags.map((f, i) => (
      <FlagRow key={i} {...f} />
    ))}
  </Box>
);

const SummaryRow = ({ icon, label, value, tagColor: tc, tag }) => (
  <Stack
    direction="row"
    spacing={1.5}
    sx={{
      alignItems: "center",
      py: 1,
      borderBottom: "1px solid",
      borderColor: "action.hover",
      "&:last-of-type": { border: 0 },
    }}
  >
    <Box
      sx={{
        width: 28,
        height: 28,
        borderRadius: 1.5,
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "text.secondary",
        bgcolor: "action.hover",
      }}
    >
      {icon}
    </Box>
    <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
      {label}
    </Typography>
    <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
      <Typography variant="caption" sx={{ fontWeight: 600 }}>
        {value}
      </Typography>
      {tag && <Chip size="small" label={tag} color={tc} sx={{ height: 20, "& .MuiChip-label": { px: 0.75, fontSize: 11 } }} />}
    </Stack>
  </Stack>
);

/* ── Data → view-model helpers ─────────────────────────────────────────────── */

/** A rationale is only shown if it's genuine prose. The judge very occasionally
 *  emits it as a serialized JSON object / schema placeholder (e.g.
 *  `{"$comment": "This field must be a string, not an object"}`) — that passes
 *  the backend's `str` check but is garbage to a recruiter, so treat anything
 *  JSON-shaped as absent. */
const proseRationale = (rationale) => {
  if (typeof rationale !== "string") return null;
  const t = rationale.trim();
  if (!t || t[0] === "{" || t[0] === "[" || t.includes("$comment")) return null;
  return rationale;
};

/** The weakest flagged signal's finding — a meaningful fallback when there's no
 *  usable rationale. */
const weakestConcernFinding = (trustBreak) => {
  const signals = pickSignals(trustBreak);
  if (!signals) return "";
  for (const key of asArray(trustBreak?.top_concerns)) {
    const text = signalText(signals[key]);
    if (text) return text;
  }
  return "";
};

/** A recruiter-facing concern summary for the CV Authenticity card face: the
 *  judge's rationale when it's real prose, else the weakest sub-signal's
 *  finding, else a generic "all clear" line. */
const authenticitySummary = (trustBreak) => {
  return (
    proseRationale(trustBreak?.rationale) ||
    weakestConcernFinding(trustBreak) ||
    "All authenticity checks look solid."
  );
};

/**
 * The Credibility Check card — a recruiter-facing read of an application's
 * headline signals, stacked as rows inside one cream card: CV Authenticity (the
 * `authenticity` verdict + its five sub-signals), the CV Score (the
 * `hard_filter` rubric), a derived Background Validation score, and — once the
 * candidate has been through WhatsApp screening — the Eligibility Questions.
 *
 * Stage-gated per the pipeline levels:
 *  - Profile Match (the `similarity` score) is the L1 headline and is dropped
 *    once the candidate advances to hard_filter (L2), where CV Score leads.
 *  - Background Validation + Eligibility Questions appear at whatsapp (L3),
 *    fed by the derived `screening` view-model from the parent.
 *  - Interview Scoring (HR's manual mark + comment) appears at the interview
 *    stage; the Shortlist verdict appears at the final (done) stage.
 */
const CvScoreCard = ({
  scores,
  stage,
  screening,
  appId,
  interviewFeedback,
  interviewEditable = false,
  historyMode = false,
}) => {
  const trust = findScore(scores, "authenticity");
  const jd = findScore(scores, "similarity");
  const others = asArray(scores).filter(
    (s) => s && s.score_type !== "authenticity" && s.score_type !== "similarity",
  );

  const trustBreak = trust?.breakdown || null;

  const trustValue = trust ? toScore(trust.value) : null;
  const jdValue = jd ? toScore(jd.value) : null;

  // History mode (talent-pool candidate history): a candidate's archived stint
  // carries only its numeric score rows (similarity + hard_filter) — authenticity
  // is candidate-level and screening/interview state isn't part of the digest —
  // so we render just Profile Match + CV Score with the very same MeterRow /
  // RubricBreakdown styling as the live board, and nothing that isn't there.
  if (historyMode) {
    const hf = others.find((s) => s.score_type === "hard_filter");
    const hfValue = hf ? toScore(hf.value) : null;
    const hasHfBreakdown = hf?.breakdown && Object.keys(hf.breakdown).length > 0;
    const hasProfileMatch = jd && jdValue != null;
    const hasCvScore = hf && hfValue != null;
    return (
      <Box>
        <Box sx={cardSx}>
          {hasProfileMatch && <MeterRow label="Profile Match" value={jdValue} />}
          {hasCvScore && (
            <MeterRow label="CV Score" value={hfValue}>
              {hasHfBreakdown ? <RubricBreakdown data={hf.breakdown} /> : null}
            </MeterRow>
          )}
          {!hasProfileMatch && !hasCvScore && (
            <RowShell>
              <Typography variant="body2" color="text.secondary">
                No scores were computed for this job.
              </Typography>
            </RowShell>
          )}
        </Box>
      </Box>
    );
  }

  const stageIdx = APPLICATION_STAGES.indexOf(stage);
  const reachedHardFilter = stageIdx >= APPLICATION_STAGES.indexOf("hard_filter");
  const reachedWhatsApp = stageIdx >= APPLICATION_STAGES.indexOf("whatsapp");
  const reachedInterview = stageIdx >= APPLICATION_STAGES.indexOf("interview");
  const reachedDone = stageIdx >= APPLICATION_STAGES.indexOf("done");
  // Profile Match only ever leads at the initial vector-screen level (L1).
  const atVectorScreen = !reachedHardFilter;
  const atWhatsApp = stage === "whatsapp";

  const bgValidation = screening?.bgValidation ?? null;
  const bgBreakdown = asArray(screening?.bgValidationBreakdown);
  const eligibility = asArray(screening?.eligibility);

  if (!trust && !jd && !others.length && !reachedWhatsApp) {
    return (
      <Typography variant="body2" color="text.secondary">
        No scores computed yet.
      </Typography>
    );
  }

  const otherLabel = (t) => (t === "hard_filter" ? "CV Score" : humanize(t));
  const hasHardFilter = others.some((s) => s.score_type === "hard_filter");

  return (
    <Box>
      <Box sx={cardSx}>
        {trust && trustValue != null ? (
          <AuthenticityRow
            value={trustValue}
            summary={authenticitySummary(trustBreak)}
          />
        ) : (
          <PendingRow label="CV Authenticity" />
        )}

        {others.map((s, i) => (
          <MeterRow
            key={s.id || `${s.score_type}-${i}`}
            label={otherLabel(s.score_type)}
            value={toScore(s.value)}
          >
            {s.breakdown && Object.keys(s.breakdown).length > 0 ? <RubricBreakdown data={s.breakdown} /> : null}
          </MeterRow>
        ))}

        {/* Reached hard filter but the CV Score hasn't landed yet — skeleton the
            bar until the async score arrives (Profile Match is already gone). */}
        {reachedHardFilter && !hasHardFilter && <MeterSkeletonRow label="CV Score" />}

        {atVectorScreen &&
          (jd && jdValue != null ? (
            <MeterRow label="Profile Match" value={jdValue} />
          ) : (
            // At L1 but the similarity score is still computing — skeleton the bar.
            <MeterSkeletonRow label="Profile Match" />
          ))}

        {/* Derived client-side from the AI-scored answers. While the candidate is
            in screening but no answer is scored yet, skeleton the bar. */}
        {bgValidation != null ? (
          <MeterRow label="Background Validation" value={bgValidation}>
            {bgBreakdown.length > 0 ? <BgValidationBreakdown items={bgBreakdown} /> : null}
          </MeterRow>
        ) : (
          atWhatsApp && <MeterSkeletonRow label="Background Validation" />
        )}

        {reachedWhatsApp && eligibility.length > 0 && (
          <EligibilityRow items={eligibility} />
        )}

        {/* HR's manual interview mark + comment (keyed by appId so the form
            state resets when the dialog switches candidates). */}
        {reachedInterview && (
          <InterviewScoringRow
            key={appId}
            appId={appId}
            feedback={interviewFeedback}
            editable={interviewEditable}
          />
        )}

        {reachedDone && <ShortlistRow />}
      </Box>
    </Box>
  );
};

export default CvScoreCard;
