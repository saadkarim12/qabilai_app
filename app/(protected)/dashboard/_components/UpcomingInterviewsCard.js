"use client";

import { useState } from "react";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import Stack from "@mui/material/Stack";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import EventOutlinedIcon from "@mui/icons-material/EventOutlined";
import { formatDateTime, DASHBOARD_UPCOMING_PREVIEW } from "@/lib/kabil/constants";
import { useUpcomingInterviews } from "@/lib/kabil/queries";
import AllInterviewsDialog from "./AllInterviewsDialog";

/**
 * The Upcoming Interviews card — the nearest few booked interviews from
 * `GET /dashboard/upcoming-interviews`. "View all" opens a dialog with every
 * future interview; it only appears when the response's `total` exceeds the
 * preview count.
 */
const UpcomingInterviewsCard = () => {
  const [open, setOpen] = useState(false);
  const { data, isLoading } = useUpcomingInterviews({ limit: DASHBOARD_UPCOMING_PREVIEW });
  const interviews = data?.interviews ?? [];
  const total = data?.total ?? 0;

  return (
    <Card sx={{ borderRadius: 2.5, height: "100%"}}>
      <CardContent sx={{ p: 2 }}>
        <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", mb: 1.75 }}>
          <EventOutlinedIcon fontSize="small" color="primary" />
          <Typography sx={{ fontWeight: 700, fontSize: "0.95rem" }}>
            Upcoming Interviews
          </Typography>
        </Stack>

        {isLoading ? (
          <Stack spacing={1.5}>
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} variant="rounded" height={48} />
            ))}
          </Stack>
        ) : interviews.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 3, textAlign: "center" }}>
            No interviews scheduled.
          </Typography>
        ) : (
          <Stack spacing={1.5}>
            {interviews.map((iv) => (
              <Stack
                key={iv.application_id}
                direction="row"
                sx={{ justifyContent: "space-between", alignItems: "flex-start", gap: 1 }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>
                    {iv.candidate_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap component="div">
                    {iv.job_title}
                  </Typography>
                </Box>
                <Typography
                  variant="caption"
                  sx={{ fontWeight: 600, flexShrink: 0, textAlign: "right" }}
                >
                  {formatDateTime(iv.scheduled_start_at)}
                </Typography>
              </Stack>
            ))}
          </Stack>
        )}

        {total > interviews.length && (
          <Button
            size="small"
            onClick={() => setOpen(true)}
            sx={{ mt: 2 }}
            fullWidth
            variant="outlined"
          >
            View all ({total})
          </Button>
        )}
      </CardContent>

      <AllInterviewsDialog open={open} onClose={() => setOpen(false)} />
    </Card>
  );
};

export default UpcomingInterviewsCard;
