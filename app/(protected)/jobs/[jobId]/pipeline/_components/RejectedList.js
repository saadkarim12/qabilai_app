"use client";

import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import Stack from "@mui/material/Stack";
import Chip from "@mui/material/Chip";
import Avatar from "@mui/material/Avatar";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import Typography from "@mui/material/Typography";
import ReplayIcon from "@mui/icons-material/Replay";
import { stageLabel, toScore, timeAgo } from "@/lib/kabil/constants";

const initials = (name) =>
  (name || "?")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");

const bestScore = (app) => {
  const hard = toScore(app.hard_filter_score);
  return hard != null ? hard : toScore(app.similarity_score);
};

const RejectedCard = ({ app, jobTitle, onOpen, onReactivate, busy }) => {
  const score = bestScore(app);

  return (
    <Card variant="outlined" sx={{ borderRadius: 2, borderColor: "#ecdcdc", display: "flex", flexDirection: "column" }}>
      <CardActionArea onClick={() => onOpen(app.id)} sx={{ p: 1.75, flexGrow: 1, alignItems: "stretch" }}>
        <Stack direction="row" spacing={1.25} sx={{ alignItems: "flex-start" }}>
          <Avatar sx={{ width: 38, height: 38, bgcolor: "#b9c2bd", fontSize: 13 }}>
            {initials(app.candidate_full_name)}
          </Avatar>
          <Box sx={{ minWidth: 0, flexGrow: 1 }}>
            <Typography variant="body2" noWrap title={app.candidate_full_name} sx={{ fontWeight: 700 }}>
              {app.candidate_full_name}
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap sx={{ display: "block" }}>
              {app.candidate_email || jobTitle}
            </Typography>
          </Box>
          {score != null && (
            <Typography variant="subtitle2" color="text.secondary" sx={{ fontWeight: 800 }}>
              {Math.round(score)}
            </Typography>
          )}
        </Stack>

        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 1.25 }}>
          <Chip size="small" label={`Rejected at ${stageLabel(app.stage)}`} color="error" variant="outlined" sx={{ height: 22 }} />
        </Box>

        {app.rejection_reason && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1.25, fontStyle: "italic" }}>
            “{app.rejection_reason}”
          </Typography>
        )}

        <Typography variant="caption" color="text.disabled" sx={{ display: "block", mt: 1.25 }}>
          Rejected {timeAgo(app.stage_updated_at || app.created_at)}
        </Typography>
      </CardActionArea>

      <Divider />
      <Box sx={{ p: 1 }}>
        <Button
          fullWidth
          size="small"
          startIcon={<ReplayIcon />}
          onClick={() => onReactivate(app)}
          disabled={busy}
          sx={{ textTransform: "none" }}
        >
          Move back to Accepted
        </Button>
      </Box>
    </Card>
  );
};

/** Rejected applicants as a simple, responsive card grid (no kanban). */
const RejectedList = ({ apps, jobTitle, onOpen, onReactivate, busyId }) => (
  <Box
    sx={{
      display: "grid",
      gridTemplateColumns: {
        xs: "1fr",
        sm: "repeat(2, 1fr)",
        lg: "repeat(3, 1fr)",
        xl: "repeat(4, 1fr)",
      },
      gap: 2,
    }}
  >
    {apps.map((app) => (
      <RejectedCard
        key={app.id}
        app={app}
        jobTitle={jobTitle}
        onOpen={onOpen}
        onReactivate={onReactivate}
        busy={busyId === app.id}
      />
    ))}
  </Box>
);

export default RejectedList;
