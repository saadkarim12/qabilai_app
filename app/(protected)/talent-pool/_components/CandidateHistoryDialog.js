"use client";

import { useState } from "react";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Avatar from "@mui/material/Avatar";
import IconButton from "@mui/material/IconButton";
import Button from "@mui/material/Button";
import Collapse from "@mui/material/Collapse";
import Divider from "@mui/material/Divider";
import Skeleton from "@mui/material/Skeleton";
import LinearProgress from "@mui/material/LinearProgress";
import CloseIcon from "@mui/icons-material/Close";
import EmailOutlinedIcon from "@mui/icons-material/EmailOutlined";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ErrorAlert from "@/components/ErrorAlert";
import {
  ScoreBadge,
  BreakdownToggle,
  RubricBreakdown,
} from "@/app/(protected)/jobs/[jobId]/pipeline/_components/CvScoreCard";
import { useCandidateHistory } from "@/lib/kabil/queries";
import { stageLabel, statusLabel, toScore, scoreBand, bandHex } from "@/lib/kabil/constants";
import { COLORS } from "@/lib/theme";

/** Brand green — mirrors the candidate dialog chrome so both dialogs read identically. */
const GREEN = "#0F6E56";
/** Shared card surface + meter track (identical to CvScoreCard). */
const CARD_BORDER = "#ECE5D6";
const TRACK = "#E7E1D2";
const cardSx = {
  bgcolor: "#F4F0E84D",
  border: `1px solid ${CARD_BORDER}`,
  borderRadius: "14px",
  overflow: "hidden",
};

/** Each score row's title — Inter 600 / 14px, identical to the pipeline board. */
const ROW_TITLE_SX = {
  fontFamily: "var(--font-sans), system-ui, Arial, sans-serif",
  fontWeight: 600,
  fontSize: "14px",
  lineHeight: "21px",
  letterSpacing: 0,
  color: "#2C2C2A",
};

/** How many recent projects (job stints) the history shows. */
const MAX_PROJECTS = 3;

const initials = (name) =>
  (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("") || "?";

const asArray = (v) => (Array.isArray(v) ? v : []);
const findScore = (scores, type) => asArray(scores).find((s) => s?.score_type === type);

/** "9th January 2026" — day (with ordinal) + full month + year. */
const ordinal = (n) => {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
};
const fullDate = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const month = new Intl.DateTimeFormat(undefined, { month: "long" }).format(d);
  return `${ordinal(d.getDate())} ${month} ${d.getFullYear()}`;
};

/** Section heading — matches the candidate dialog's label scale. */
const sectionTitleSx = (color) => ({
  fontFamily: "var(--font-jakarta), system-ui, sans-serif",
  fontWeight: 700,
  fontSize: "13px",
  lineHeight: "16px",
  letterSpacing: "0.2px",
  color,
});

/** Band-coloured determinate meter — identical to the pipeline board's rows. */
const Meter = ({ value }) => {
  const has = value != null;
  const color = has ? bandHex(scoreBand(value).color) : TRACK;
  return (
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
  );
};

/** A green chevron toggle whose label stays fixed while the caret flips. */
const ChevronToggle = ({ open, onClick, label }) => (
  <Button
    onClick={onClick}
    disableRipple
    size="small"
    endIcon={
      <ExpandMoreIcon
        sx={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .2s" }}
      />
    }
    sx={{
      color: GREEN,
      fontWeight: 700,
      textTransform: "none",
      minWidth: 0,
      p: 0,
      flexShrink: 0,
      "&:hover": { bgcolor: "transparent" },
    }}
  >
    {label}
  </Button>
);

/** A score row: title + meter + score disc, with the score's date beneath.
 *  `footer` renders on the date line's right (e.g. a Breakdown toggle). */
const ScoreRow = ({ label, value, date, footer }) => (
  <Box>
    <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ ...ROW_TITLE_SX, mb: 1 }}>{label}</Typography>
        <Meter value={value} />
      </Box>
      <ScoreBadge value={value} />
    </Stack>
    {(date || footer) && (
      <Stack
        direction="row"
        spacing={1}
        sx={{ alignItems: "center", justifyContent: "space-between", mt: 0.75 }}
      >
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          {date}
        </Typography>
        {footer}
      </Stack>
    )}
  </Box>
);

/* ── One project card: a job stint, collapsed to its title until opened ────── */

const ProjectCard = ({ stint, defaultOpen }) => {
  const [open, setOpen] = useState(!!defaultOpen);
  const [showBreakdown, setShowBreakdown] = useState(false);
  const [showComment, setShowComment] = useState(false);

  const scores = asArray(stint.scores);
  const sim = findScore(scores, "similarity");
  const hf = findScore(scores, "hard_filter");
  const profileValue = sim ? toScore(sim.value) : null;
  const cvValue = hf ? toScore(hf.value) : null;
  const cvBreakdown =
    hf?.breakdown && Object.keys(hf.breakdown).length > 0 ? hf.breakdown : null;

  const hasProfile = sim && profileValue != null;
  const hasCv = hf && cvValue != null;

  const isArchived = stint.status === "archived";
  const commentDate = fullDate(stint.archived_at) || fullDate(stint.stage_updated_at);
  const showCommentRow = isArchived || !!stint.move_to_pool_reason;
  const reason = stint.move_to_pool_reason;

  // Cards moved back to the pool lead with that date; live/other stints fall
  // back to a status · stage line since they were never pooled from this job.
  const subtitle =
    isArchived && stint.archived_at
      ? `Moved to Talent Pool on ${fullDate(stint.archived_at)}`
      : `${statusLabel(stint.status)} · ${stageLabel(stint.stage)}`;

  return (
    <Box sx={cardSx}>
      <Box sx={{ px: 2, py: 1.75 }}>
        <Stack
          direction="row"
          spacing={1.5}
          sx={{ alignItems: "flex-start", justifyContent: "space-between" }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              sx={{
                fontFamily: "var(--font-sans), system-ui, Arial, sans-serif",
                fontWeight: 600,
                fontSize: "16px",
                lineHeight: "21px",
                letterSpacing: 0,
                color: "#2C2C2A",
              }}
            >
              {stint.job_title || "Untitled Job"}
            </Typography>
            <Typography variant="caption" sx={{ color: "text.secondary" }}>
              {subtitle}
            </Typography>
          </Box>
          <ChevronToggle open={open} onClick={() => setOpen((o) => !o)} label="View Details" />
        </Stack>

        <Collapse in={open} timeout="auto" unmountOnExit>
          <Box sx={{ mt: 2 }}>
            {hasProfile && (
              <ScoreRow
                label="Profile Match"
                value={profileValue}
                date={fullDate(sim.computed_at)}
              />
            )}

            {hasProfile && (hasCv || showCommentRow) && (
              <Divider sx={{ my: 2, borderColor: CARD_BORDER }} />
            )}

            {hasCv && (
              <ScoreRow
                label="CV Score"
                value={cvValue}
                date={fullDate(hf.computed_at)}
                footer={
                  cvBreakdown ? (
                    <BreakdownToggle
                      open={showBreakdown}
                      onClick={() => setShowBreakdown((o) => !o)}
                    />
                  ) : null
                }
              />
            )}
            {hasCv && cvBreakdown && (
              <Collapse in={showBreakdown} timeout="auto" unmountOnExit>
                <Box sx={{ pt: 1.75 }}>
                  <RubricBreakdown data={cvBreakdown} />
                </Box>
              </Collapse>
            )}

            {hasCv && showCommentRow && <Divider sx={{ my: 2, borderColor: CARD_BORDER }} />}

            {showCommentRow && (
              <Box>
                <Typography sx={ROW_TITLE_SX}>Recruiter Comment</Typography>
                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ alignItems: "center", justifyContent: "space-between", mt: 0.75 }}
                >
                  <Typography variant="caption" sx={{ color: "text.secondary" }}>
                    {commentDate}
                  </Typography>
                  <ChevronToggle
                    open={showComment}
                    onClick={() => setShowComment((o) => !o)}
                    label={showComment ? "Hide Comment" : "View Comment"}
                  />
                </Stack>
                <Collapse in={showComment} timeout="auto" unmountOnExit>
                  <Typography
                    variant="body2"
                    sx={{
                      mt: 1,
                      color: reason ? "#2C2C2A" : "text.disabled",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {reason || "No comment was recorded for this move."}
                  </Typography>
                </Collapse>
              </Box>
            )}

            {!hasProfile && !hasCv && (
              <Typography variant="body2" sx={{ color: "text.secondary" }}>
                No scores were computed for this job.
              </Typography>
            )}
          </Box>
        </Collapse>
      </Box>
    </Box>
  );
};

const HistorySkeleton = () => (
  <Box sx={{ px: { xs: 2, sm: 3 }, py: 2 }}>
    <Stack spacing={2}>
      <Skeleton variant="rounded" height={24} width="40%" />
      <Skeleton variant="rounded" height={110} />
      <Skeleton variant="rounded" height={72} />
    </Stack>
  </Box>
);

/**
 * Talent-pool candidate history. Mirrors the pipeline candidate dialog's exact
 * chrome (green banner + "CANDIDATE SCORING" eyebrow + the shared score-row
 * visual language), scoped to a pooled candidate: the candidate's three most
 * recent job stints are shown as collapsible cards, each surfacing that job's
 * Profile Match + CV Score (with their score dates and the CV Score breakdown)
 * and the recruiter's comment from when they were moved to the pool.
 * Opened from a pool row's "History" action.
 */
const CandidateHistoryDialog = ({ candidateId, open, onClose }) => {
  const { data, isLoading, isError, error } = useCandidateHistory(candidateId, { enabled: open });
  const candidate = data?.candidate;
  // Newest-first from the API — the three most recent jobs the candidate went
  // through, whatever their status.
  const projects = asArray(data?.stints).slice(0, MAX_PROJECTS);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
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
          // Uniform shrink of secondary text + controls — identical to the
          // pipeline candidate dialog so the shared score rows render 1:1.
          "& .MuiTypography-body2": { fontSize: "0.72rem" },
          "& .MuiTypography-caption": { fontSize: "0.66rem" },
          "& .MuiTypography-overline": { fontSize: "0.6rem" },
          "& .MuiTypography-subtitle2": { fontSize: "0.75rem" },
          "& .MuiButton-root": { fontSize: "0.72rem" },
          "& .MuiChip-root": { fontSize: "0.66rem" },
          "& .MuiOutlinedInput-input": { fontSize: "0.655rem" },
          "& .MuiFormLabel-root": { fontSize: "0.655rem" },
        }}
      >
        {isError ? (
          <Box sx={{ p: 3 }}>
            <ErrorAlert error={error} />
          </Box>
        ) : isLoading || !data ? (
          <HistorySkeleton />
        ) : (
          <Box>
            {/* Header banner — same styling as the pipeline candidate dialog. */}
            <Box sx={{ bgcolor: GREEN, color: "#fff", px: { xs: 2, sm: 3 }, py: 2 }}>
              <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", pr: 4 }}>
                <Avatar
                  sx={{ width: 42, height: 42, bgcolor: COLORS.gold, color: "#fff", fontWeight: 700, fontSize: 11 }}
                >
                  {initials(candidate?.full_name)}
                </Avatar>
                <Box sx={{ minWidth: 0 }}>
                  <Typography
                    sx={{
                      fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                      fontWeight: 700,
                      fontSize: "18px",
                      lineHeight: "22.5px",
                      color: "#FFFFFF",
                    }}
                  >
                    {candidate?.full_name || "Candidate"}
                  </Typography>
                </Box>
              </Stack>
            </Box>

            {/* Body */}
            <Box sx={{ px: { xs: 2, sm: 3 }, py: 1.75 }}>
              {/* Eyebrow + email — same styling as the candidate dialog. */}
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  sx={{
                    fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                    fontWeight: 700,
                    fontSize: "12px",
                    lineHeight: "15px",
                    letterSpacing: "1px",
                    textTransform: "uppercase",
                    color: GREEN,
                  }}
                >
                  Candidate Scoring
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

              {/* Up to three most-recent job stints, each a collapsible card. */}
              <Box sx={{ mt: 2 }}>
                {projects.length > 0 ? (
                  <Stack spacing={1.5}>
                    {projects.map((stint, i) => (
                      <ProjectCard
                        key={stint.application_id}
                        stint={stint}
                        defaultOpen={i === 0}
                      />
                    ))}
                  </Stack>
                ) : (
                  <Box sx={cardSx}>
                    <Box sx={{ px: 2, py: 1.75 }}>
                      <Typography variant="body2" color="text.secondary">
                        This candidate hasn’t been part of any job yet.
                      </Typography>
                    </Box>
                  </Box>
                )}
              </Box>
            </Box>
          </Box>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default CandidateHistoryDialog;
