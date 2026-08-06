"use client";

import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import IconButton from "@mui/material/IconButton";
import CloseIcon from "@mui/icons-material/Close";
import Stack from "@mui/material/Stack";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import VideocamOutlinedIcon from "@mui/icons-material/VideocamOutlined";
import PlaceOutlinedIcon from "@mui/icons-material/PlaceOutlined";
import { formatDateTime, DASHBOARD_UPCOMING_MAX } from "@/lib/kabil/constants";
import { useUpcomingInterviews } from "@/lib/kabil/queries";

/** A meeting-location line: virtual join link, else physical/phone text. */
const MeetingLocation = ({ interview }) => {
  if (interview.join_url) {
    return (
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
        <VideocamOutlinedIcon sx={{ fontSize: 16 }} color="action" />
        <Link href={interview.join_url} target="_blank" rel="noopener" variant="caption">
          Join link
        </Link>
      </Stack>
    );
  }
  if (interview.location_text) {
    return (
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
        <PlaceOutlinedIcon sx={{ fontSize: 16 }} color="action" />
        <Typography variant="caption" color="text.secondary">
          {interview.location_text}
        </Typography>
      </Stack>
    );
  }
  return null;
};

/**
 * The "View all" upcoming-interviews dialog. Fetches a large page (lazy — only
 * while open) of every future booked interview, soonest first, with full slot +
 * meeting details. Mirrors the card's data source so the list stays consistent.
 */
const AllInterviewsDialog = ({ open, onClose }) => {
  const { data, isLoading } = useUpcomingInterviews({
    limit: DASHBOARD_UPCOMING_MAX,
    enabled: open,
  });
  const interviews = data?.interviews ?? [];

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle sx={{ pr: 6 }}>
        Upcoming interviews
        <IconButton onClick={onClose} sx={{ position: "absolute", right: 8, top: 8 }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {isLoading ? (
          <Stack spacing={1.5}>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} variant="rounded" height={56} />
            ))}
          </Stack>
        ) : interviews.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 3, textAlign: "center" }}>
            No interviews scheduled.
          </Typography>
        ) : (
          <Stack spacing={1.5}>
            {interviews.map((iv) => (
              <Box
                key={iv.application_id}
                sx={{ p: 1.5, borderRadius: 2, border: "1px solid", borderColor: "divider" }}
              >
                <Stack
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
                  <Box sx={{ textAlign: "right", flexShrink: 0 }}>
                    <Typography variant="caption" sx={{ fontWeight: 600 }} component="div">
                      {formatDateTime(iv.scheduled_start_at)}
                    </Typography>
                    {iv.invitee_timezone && (
                      <Typography variant="caption" color="text.secondary">
                        {iv.invitee_timezone}
                      </Typography>
                    )}
                  </Box>
                </Stack>
                <Box sx={{ mt: 0.75 }}>
                  <MeetingLocation interview={iv} />
                </Box>
              </Box>
            ))}
          </Stack>
        )}
      </DialogContent>
    </Dialog>
  );
};

export default AllInterviewsDialog;
