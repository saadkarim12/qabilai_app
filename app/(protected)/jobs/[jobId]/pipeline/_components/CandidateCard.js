"use client";

import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Chip from "@mui/material/Chip";
import Avatar from "@mui/material/Avatar";
import Typography from "@mui/material/Typography";
import LinearProgress from "@mui/material/LinearProgress";
import Skeleton from "@mui/material/Skeleton";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import CircleIcon from "@mui/icons-material/Circle";
import { authenticityBandChip, bandColor, bandHex, scoreBand, toScore, timeAgo } from "@/lib/kabil/constants";
import { useApplication, useWhatsAppConversation } from "@/lib/kabil/queries";

const initials = (name) =>
  (name || "?")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");

/** Best score available for the candidate at their current stage. */
const bestScore = (app) => {
  const hard = toScore(app.hard_filter_score);
  return hard != null ? hard : toScore(app.similarity_score);
};

/** Background Validation (0–100) derived from the WhatsApp screening answers'
 *  relevance scores — the same mean-then-scale the candidate dialog uses. Null
 *  until at least one answer has been AI-scored. */
const bgValidationScore = (convo) => {
  const answers = Array.isArray(convo?.answers) ? convo.answers : [];
  const scored = answers.filter((a) => a?.relevance_score != null);
  return scored.length
    ? Math.round((scored.reduce((s, a) => s + a.relevance_score, 0) / scored.length) * 10)
    : null;
};

/** The score-bar caption per pipeline stage (what the number measures). The
 *  final "done" stage shows a Shortlisted verdict instead of a bar. */
const SCORE_TITLE = {
  vector_screen: "Profile Match",
  hard_filter: "CV Score",
  whatsapp: "Background Validation",
  interview: "Interview Scoring",
};

/** Solid + soft tint per MUI palette tone, for inline chip/badge styling. */
const TONE = {
  success: { solid: "#0F6E56", soft: "rgba(15,110,86,0.12)" },
  warning: { solid: " #EF9F27", soft: "rgba(239,159,39,0.16)" },
  error: { solid: "#d14343", soft: "rgba(209,67,67,0.12)" },
  info: { solid: "#2f7fd1", soft: "rgba(47,127,209,0.12)" },
  default: { solid: "#5f6b66", soft: "rgba(0,0,0,0.06)" },
};
const tone = (key) => TONE[key] ?? TONE.default;

/** Outlined tag with a leading status dot (e.g. "● Review"). */
const DotChip = ({ label, toneKey }) => {
  const t = tone(toneKey);
  return (
    <Chip
      size="small"
      variant="outlined"
      icon={<CircleIcon sx={{ fontSize: "9px !important", color: `${t.solid} !important` }} />}
      label={label}
      sx={{
        height: 24,
        fontWeight: 600,
        borderRadius: 1.5,
        bgcolor: "#fff",
        color: "#4a5650",
        borderColor: "#e7eae8",
        "& .MuiChip-label": { px: 0.75 },
        "& .MuiChip-icon": { ml: 0.75, mr: -0.25 },
      }}
    />
  );
};

/**
 * A compact applicant tile for the pipeline board. Click (or the ⋯ button)
 * opens the detail drawer; when `draggable` it can be dragged to an adjacent
 * stage column.
 */
const CandidateCard = ({ app, jobTitle, onOpen, draggable = false, onDragStart, onDragEnd, dragging = false }) => {
  const isDone = app.stage === "done";
  const isAssessment = app.stage === "whatsapp";

  // Assessment cards derive Background Validation from the WhatsApp screening
  // conversation (it isn't denormalized onto the list item); we only fetch it
  // for cards actually sitting in that stage.
  const convo = useWhatsAppConversation(app.id, { enabled: isAssessment });

  // CV authenticity band (authentic / review / fabricated) drives the corner
  // dot. It lives on the candidate, not the list item, so we read it from the
  // application detail — the same cached read the detail drawer uses, so
  // opening a card later is instant. Null until the authenticity step has run.
  const detail = useApplication(app.id);
  const authBand = detail.data?.candidate?.authenticity_band ?? null;
  const authDot = tone(bandColor(authBand));

  // The score that belongs to this candidate's current stage, plus whether it's
  // still being fetched/computed — while pending we skeleton the bar until it
  // lands (a card that just moved to a new stage waits on that stage's score).
  let stageScore = null;
  let pending = false;
  switch (app.stage) {
    case "vector_screen":
      stageScore = toScore(app.similarity_score);
      pending = stageScore == null;
      break;
    case "hard_filter":
      stageScore = toScore(app.hard_filter_score);
      pending = stageScore == null;
      break;
    case "whatsapp":
      stageScore = bgValidationScore(convo.data);
      pending = convo.isLoading || stageScore == null;
      break;
    default:
      // Interviewed (HR enters the mark) / anything else: best available score.
      stageScore = bestScore(app);
  }

  const band = scoreBand(stageScore);
  const accent = tone(band.color);

  return (
    <Card
      variant="outlined"
      draggable={draggable}
      onDragStart={draggable ? (e) => onDragStart?.(e, app) : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
      sx={{
        width: "100%",
        height: 168,
        borderRadius: "13.19px",
        borderWidth: "0.82px",
        borderColor: "#e7eae8",
        cursor: draggable ? "grab" : "default",
        opacity: dragging ? 0.4 : 1,
        "&:active": draggable ? { cursor: "grabbing" } : undefined,
        transition: "box-shadow .15s ease, border-color .15s ease, transform .15s ease, opacity .15s ease",
        "&:hover": {
          borderColor: "transparent",
          boxShadow: "0 8px 20px -10px rgba(15,110,86,.35)",
          transform: "translateY(-2px)",
        },
      }}
    >
      <CardActionArea
        component="div"
        onClick={() => onOpen(app.id)}
        sx={{ p: 1.75, height: "100%", display: "flex", flexDirection: "column", alignItems: "stretch" }}
      >
        <Stack direction="row" spacing={1.25} sx={{ alignItems: "flex-start" }}>
          <Avatar sx={{ width: 38, height: 38, bgcolor: "primary.main", fontSize: 13, fontWeight: 700 }}>
            {initials(app.candidate_full_name)}
          </Avatar>
          <Box sx={{ minWidth: 0, flexGrow: 1 }}>
            <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
              <Typography
                variant="body2"
                noWrap
                title={app.candidate_full_name}
                sx={{ fontWeight: 700, color: "#1c2522", flexGrow: 1, minWidth: 0 }}
              >
                {app.candidate_full_name}
              </Typography>
              <Box
                title={`CV Authenticity: ${authenticityBandChip(authBand).label}`}
                sx={{
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  bgcolor: authDot.solid,
                  flexShrink: 0,
                }}
              />
            </Stack>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
                lineHeight: 1.3,
              }}
            >
              {jobTitle}
            </Typography>
          </Box>
        </Stack>

        {isDone ? (
          <Stack
            direction="row"
            spacing={1}
            sx={{ alignItems: "center", justifyContent: "space-between", mt: 1.5 }}
          >
            <Typography
              sx={{
                fontFamily: "var(--font-sans), system-ui, Arial, sans-serif",
                fontWeight: 600,
                fontSize: "10px",
                lineHeight: "21px",
                letterSpacing: 0,
                color: "#2C2C2A",
              }}
            >
              Shortlist
            </Typography>
            <Typography
              sx={{
                fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                fontWeight: 700,
                fontSize: "9.89px",
                lineHeight: "14.83px",
                letterSpacing: 0,
                color: "#0F6E56",
              }}
            >
              Shortlisted
            </Typography>
          </Stack>
        ) : (pending || stageScore != null) ? (
          <Box sx={{ mt: 1.5 }}>
            <Typography
              sx={{
                fontFamily: "var(--font-sans), system-ui, Arial, sans-serif",
                fontWeight: 600,
                fontSize: "10px",
                lineHeight: "21px",
                letterSpacing: 0,
                color: "#2C2C2A",
                mb: 0,
              }}
            >
              {SCORE_TITLE[app.stage] ?? "Score"}
            </Typography>
            {pending ? (
              // Score for this stage hasn't landed yet — skeleton just the bar.
              <Stack direction="row" spacing={1.25} sx={{ alignItems: "center" }}>
                <Skeleton variant="rounded" height={5} sx={{ flexGrow: 1, borderRadius: 3 }} />
                <Skeleton variant="text" width={18} sx={{ minWidth: 22 }} />
              </Stack>
            ) : (
              <Stack direction="row" spacing={1.25} sx={{ alignItems: "center" }}>
                <LinearProgress
                  variant="determinate"
                  value={Math.min(100, Math.max(0, stageScore))}
                  sx={{
                    flexGrow: 1,
                    height: 5,
                    borderRadius: 3,
                    bgcolor: "rgba(0,0,0,0.06)",
                    "& .MuiLinearProgress-bar": { bgcolor: bandHex(band.color), borderRadius: 3 },
                  }}
                />
                <Typography
                  variant="subtitle2"
                  sx={{ fontWeight: 800, color: accent.solid, minWidth: 22, textAlign: "right" }}
                >
                  {Math.round(stageScore)}
                </Typography>
              </Stack>
            )}
          </Box>
        ) : null}


        <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", mt: "auto", pt: 1.5, color: "text.secondary" }}>
          <AccessTimeIcon sx={{ fontSize: 14 }} />
          <Typography variant="caption">{timeAgo(app.created_at)}</Typography>
        </Stack>
      </CardActionArea>
    </Card>
  );
};

export default CandidateCard;
