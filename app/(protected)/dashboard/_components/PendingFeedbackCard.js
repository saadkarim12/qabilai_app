"use client";

import { useRouter } from "next/navigation";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import Stack from "@mui/material/Stack";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Skeleton from "@mui/material/Skeleton";
import PendingActionsOutlinedIcon from "@mui/icons-material/PendingActionsOutlined";
import { usePendingFeedback } from "@/lib/kabil/queries";

/**
 * The Pending Feedback card — applications stalled at the interview stage past
 * the feedback SLA (`GET /dashboard/pending-feedback`, oldest first). Each row
 * links to the job's pipeline board so HR can record the decision. `days_waiting`
 * comes from the backend (whole days since the app entered the interview stage).
 */
const dayLabel = (n) => `${n} day${n === 1 ? "" : "s"} ago`;

const PendingFeedbackCard = () => {
  const router = useRouter();
  const { data, isLoading } = usePendingFeedback();
  const items = data?.items ?? [];

  return (
    <Card sx={{ borderRadius: 2.5, height: "100%" }}>
      <CardContent sx={{ p: 2 }}>
        <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", mb: 1.75 }}>
          <PendingActionsOutlinedIcon fontSize="small" color="primary" />
          <Typography sx={{ fontWeight: 700, fontSize: "0.95rem" }}>
            Pending Feedback
          </Typography>
        </Stack>

        {isLoading ? (
          <Stack spacing={1.5}>
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} variant="rounded" height={48} />
            ))}
          </Stack>
        ) : items.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 3, textAlign: "center" }}>
            No interviews are awaiting feedback. 🎉
          </Typography>
        ) : (
          <Stack spacing={1.25}>
            {items.map((it, idx) => (
              <Stack
                key={it.application_id}
                direction="row"
                onClick={() => router.push(`/jobs/${it.job_id}/pipeline`)}
                sx={{
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 1,
                  p: 1.25,
                  borderRadius: 2,
                  // Only the oldest (first) row is tinted, so the most overdue
                  // item stands out; the rest stay plain until hovered.
                  bgcolor: idx === 0 ? "rgba(196,69,69,0.08)" : "transparent",
                  cursor: "pointer",
                  transition: "background-color .15s ease",
                  "&:hover": { bgcolor: "rgba(196,69,69,0.12)" },
                }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>
                    {it.candidate_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap component="div">
                    {it.job_title} · interviewed {dayLabel(it.days_waiting)}
                  </Typography>
                </Box>
                <Chip
                  size="small"
                  color="error"
                  variant="outlined"
                  label="No feedback"
                  sx={{ flexShrink: 0 }}
                />
              </Stack>
            ))}
          </Stack>
        )}
      </CardContent>
    </Card>
  );
};

export default PendingFeedbackCard;
