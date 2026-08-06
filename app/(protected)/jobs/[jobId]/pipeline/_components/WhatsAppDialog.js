"use client";

import { Fragment, useEffect, useRef } from "react";
import Dialog from "@mui/material/Dialog";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Avatar from "@mui/material/Avatar";
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import Skeleton from "@mui/material/Skeleton";
import CloseIcon from "@mui/icons-material/Close";
import DoneAllIcon from "@mui/icons-material/DoneAll";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import WhatsAppIcon from "@mui/icons-material/WhatsApp";
import ErrorAlert from "@/components/ErrorAlert";
import { useWhatsAppConversation } from "@/lib/kabil/queries";
import { whatsappStateLabel } from "@/lib/kabil/constants";

/* WhatsApp brand-ish palette, kept local to this transcript view. */
const WA_HEADER = "#075e54";
const WA_HEADER_SUB = "#cfe9e4";
const WA_BG = "#efeae2";
const WA_OUT = "#d9fdd3"; // our outbound bubbles (sent to candidate)
const WA_IN = "#ffffff"; // candidate's inbound replies

const initials = (name) =>
  (name || "?")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");

const msgTime = (ts) => {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
};

const dayKey = (ts) => {
  if (!ts) return "";
  try {
    return new Date(ts).toDateString();
  } catch {
    return "";
  }
};

/**
 * WhatsApp formats text with *bold*, _italic_ and ~strike~. Render those inline
 * so questions/headings (e.g. "*Question 1 of 9*") read like the real chat.
 */
const renderRichText = (text) => {
  if (!text) return null;
  const out = [];
  const re = /(\*[^*\n]+\*|_[^_\n]+_|~[^~\n]+~)/g;
  let last = 0;
  let key = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const token = m[0];
    const inner = token.slice(1, -1);
    if (token[0] === "*") out.push(<strong key={key++}>{inner}</strong>);
    else if (token[0] === "_") out.push(<em key={key++}>{inner}</em>);
    else out.push(<s key={key++}>{inner}</s>);
    last = m.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
};

/**
 * Group the transcript so each answer sits directly under the question it
 * replies to. The backend can return messages out of order — an inbound answer
 * is scored asynchronously and may land after the next question was already
 * sent — so we can't trust array order (or timestamps, which often share the
 * same minute). Every message carries its own `question_index`, so we sort by
 * that. Messages without one (intro greeting, closing note) inherit the most
 * recent question seen, keeping the greeting at the top and the sign-off at the
 * bottom. The sort is stable, so a question and its answer keep their natural
 * "question first, then answer" order within a group.
 */
const orderByQuestion = (list) => {
  if (!Array.isArray(list)) return [];
  let lastQi = -1; // anything before the first question (intro) sorts to the top
  return list
    .map((m, i) => {
      if (m.question_index != null) lastQi = m.question_index;
      return { m, i, qi: m.question_index != null ? m.question_index : lastQi };
    })
    .sort((a, b) => a.qi - b.qi || a.i - b.i)
    .map((d) => d.m);
};

/** Relevance: higher is better. AI-likelihood: lower (more human) is better. */
const scoreColor = (kind, n) => {
  if (n == null) return "default";
  if (kind === "relevance") return n >= 7 ? "success" : n >= 4 ? "warning" : "error";
  return n <= 3 ? "success" : n <= 6 ? "warning" : "error";
};

const dayLabel = (ts) => {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleDateString([], {
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
    });
  } catch {
    return "";
  }
};

/** Centered date pill between message groups (WhatsApp-style). */
const DaySeparator = ({ label }) => (
  <Box sx={{ display: "flex", justifyContent: "center", my: 1.5 }}>
    <Chip
      label={label}
      size="small"
      sx={{
        bgcolor: "rgba(255,255,255,0.85)",
        color: "text.secondary",
        fontWeight: 600,
        fontSize: 11,
        boxShadow: "0 1px 1px rgba(0,0,0,0.08)",
      }}
    />
  </Box>
);

/** One chat bubble. Outbound (our messages) sit right + green; inbound left. */
const Bubble = ({ msg }) => {
  const outbound = msg.direction === "outbound";
  const text = msg.button_title || msg.body || "";
  const isReply = msg.message_type === "button_reply";
  const hasScores =
    msg.answer_relevance_score != null || msg.answer_ai_score != null;

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: outbound ? "flex-end" : "flex-start",
        px: 0.5,
      }}
    >
      <Box
        sx={{
          maxWidth: "82%",
          bgcolor: outbound ? WA_OUT : WA_IN,
          borderRadius: 1.5,
          borderTopRightRadius: outbound ? 0 : 1.5 * 8,
          borderTopLeftRadius: outbound ? 1.5 * 8 : 0,
          px: 1.25,
          py: 0.75,
          boxShadow: "0 1px 0.5px rgba(0,0,0,0.13)",
        }}
      >
        {isReply && (
          <Typography
            variant="caption"
            sx={{ display: "block", color: WA_HEADER, fontWeight: 700, mb: 0.25 }}
          >
            Tapped reply
          </Typography>
        )}
        <Typography
          variant="body2"
          sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word", color: "#111b21" }}
        >
          {text ? renderRichText(text) : "—"}
        </Typography>

        {hasScores && (
          <Stack
            direction="row"
            spacing={0.5}
            useFlexGap
            sx={{ alignItems: "center", flexWrap: "wrap", mt: 0.75, pt: 0.75, borderTop: "1px dashed rgba(0,0,0,0.12)" }}
          >
            {msg.answer_relevance_score != null && (
              <Tooltip title="How well the answer addresses the question (0–10)" arrow>
                <Chip
                  size="small"
                  label={`Relevance ${msg.answer_relevance_score}/10`}
                  color={scoreColor("relevance", msg.answer_relevance_score)}
                  sx={{ height: 19, fontSize: 10, fontWeight: 700 }}
                />
              </Tooltip>
            )}
            {msg.answer_ai_score != null && (
              <Tooltip title="Likelihood the answer was AI-generated (0–10, lower is more human)" arrow>
                <Chip
                  size="small"
                  label={`AI ${msg.answer_ai_score}/10`}
                  color={scoreColor("ai", msg.answer_ai_score)}
                  variant="outlined"
                  sx={{ height: 19, fontSize: 10, fontWeight: 700 }}
                />
              </Tooltip>
            )}
            {msg.answer_score_rationale && (
              <Tooltip title={msg.answer_score_rationale} arrow>
                <InfoOutlinedIcon sx={{ fontSize: 15, color: "rgba(0,0,0,0.4)", cursor: "help" }} />
              </Tooltip>
            )}
          </Stack>
        )}

        <Stack
          direction="row"
          spacing={0.5}
          sx={{ alignItems: "center", justifyContent: "flex-end", mt: 0.25 }}
        >
          <Typography variant="caption" sx={{ color: "rgba(0,0,0,0.45)", fontSize: 10 }}>
            {msgTime(msg.created_at)}
          </Typography>
          {outbound && <DoneAllIcon sx={{ fontSize: 14, color: "#53bdeb" }} />}
        </Stack>
      </Box>
    </Box>
  );
};

const TranscriptSkeleton = () => (
  <Stack spacing={1.5} sx={{ p: 2 }}>
    {[60, 75, 45, 70, 50].map((w, i) => (
      <Box
        key={i}
        sx={{ display: "flex", justifyContent: i % 2 ? "flex-end" : "flex-start" }}
      >
        <Skeleton variant="rounded" width={`${w}%`} height={44} sx={{ borderRadius: 1.5 }} />
      </Box>
    ))}
  </Stack>
);

/** Friendly empty / not-started-yet state. */
const EmptyState = ({ title, subtitle }) => (
  <Stack spacing={1.5} sx={{ alignItems: "center", justifyContent: "center", height: "100%", px: 4, textAlign: "center" }}>
    <Avatar sx={{ bgcolor: "rgba(7,94,84,0.12)", color: WA_HEADER, width: 56, height: 56 }}>
      <WhatsAppIcon />
    </Avatar>
    <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
      {title}
    </Typography>
    <Typography variant="body2" color="text.secondary">
      {subtitle}
    </Typography>
  </Stack>
);

/**
 * WhatsApp-style read-only transcript of the screening conversation for one
 * application. Mounts only while `open` so the underlying poll is scoped to the
 * time the recruiter is actually looking at the chat.
 */
const WhatsAppDialog = ({ appId, candidate, open, onClose }) => {
  const { data: convo, isLoading, isError, error } = useWhatsAppConversation(appId, {
    enabled: open,
    poll: true,
  });

  const scrollRef = useRef(null);
  const messages = orderByQuestion(convo?.messages);

  // Pin to the newest message whenever the transcript grows or reopens.
  useEffect(() => {
    if (!open) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [open, messages.length]);

  const state = convo ? whatsappStateLabel(convo.state) : null;
  const phone = convo?.phone_e164 || candidate?.phone_e164;

  let body;
  if (isError) {
    body = (
      <Box sx={{ p: 2 }}>
        <ErrorAlert error={error} />
      </Box>
    );
  } else if (isLoading) {
    body = <TranscriptSkeleton />;
  } else if (!convo) {
    body = (
      <EmptyState
        title="No WhatsApp conversation yet"
        subtitle="The screening invite is sent when the candidate reaches L2. Messages will appear here once the conversation opens."
      />
    );
  } else if (messages.length === 0) {
    body = (
      <EmptyState
        title="Conversation opened"
        subtitle="No messages have been exchanged yet. This view updates automatically as the candidate replies."
      />
    );
  } else {
    body = (
      <Stack spacing={0.75} sx={{ p: 1.5 }}>
        {messages.map((m, i) => {
          const showDay = i === 0 || dayKey(m.created_at) !== dayKey(messages[i - 1].created_at);
          return (
            <Fragment key={m.id}>
              {showDay && <DaySeparator label={dayLabel(m.created_at)} />}
              <Bubble msg={m} />
            </Fragment>
          );
        })}
      </Stack>
    );
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      slotProps={{ paper: { sx: { borderRadius: 2, height: "82vh", overflow: "hidden" } } }}
    >
      <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
        {/* Header */}
        <Stack
          direction="row"
          spacing={1.5}
          sx={{ alignItems: "center", bgcolor: WA_HEADER, color: "#fff", px: 2, py: 1.5 }}
        >
          <Avatar sx={{ bgcolor: "rgba(255,255,255,0.2)", color: "#fff", width: 40, height: 40 }}>
            {initials(candidate?.full_name)}
          </Avatar>
          <Box sx={{ flexGrow: 1, minWidth: 0 }}>
            <Typography variant="subtitle1" noWrap sx={{ fontWeight: 700 }}>
              {candidate?.full_name || "Candidate"}
            </Typography>
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", minWidth: 0 }}>
              {phone && (
                <Typography variant="caption" sx={{ color: WA_HEADER_SUB }} noWrap>
                  {phone}
                </Typography>
              )}
              {state && (
                <Chip
                  size="small"
                  label={state.label}
                  color={state.color}
                  sx={{ height: 18, fontSize: 10, fontWeight: 700 }}
                />
              )}
            </Stack>
          </Box>
          <IconButton onClick={onClose} aria-label="Close" sx={{ color: "#fff" }}>
            <CloseIcon />
          </IconButton>
        </Stack>

        {/* Messages */}
        <Box ref={scrollRef} sx={{ flexGrow: 1, overflowY: "auto", bgcolor: WA_BG }}>
          {body}
        </Box>

        {/* Footer: this is a read-only mirror of the candidate's WhatsApp chat. */}
        <Box
          sx={{
            px: 2,
            py: 1,
            bgcolor: "#f0f2f5",
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Typography variant="caption" color="text.secondary">
            Read-only transcript — replies are handled automatically over WhatsApp.
          </Typography>
        </Box>
      </Box>
    </Dialog>
  );
};

export default WhatsAppDialog;
