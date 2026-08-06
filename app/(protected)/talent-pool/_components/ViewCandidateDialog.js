"use client";

import { useMemo, useState } from "react";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import DialogActions from "@mui/material/DialogActions";
import Stack from "@mui/material/Stack";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import TextField from "@mui/material/TextField";
import MenuItem from "@mui/material/MenuItem";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import ErrorAlert from "@/components/ErrorAlert";
import CandidateDialog from "../../jobs/[jobId]/pipeline/_components/CandidateDialog";
import { useJobs, useSourceToJob, useCandidateHistory } from "@/lib/kabil/queries";
import { jobStatusColor, humanize } from "@/lib/kabil/constants";

/** Jobs a candidate can be opened against: live or being prepared. */
const SOURCEABLE_STATUSES = ["open", "draft"];

const firstName = (name) => (name || "").split(/\s+/).filter(Boolean)[0] || "this candidate";

/**
 * Opens a pooled candidate's full profile by reusing the pipeline's
 * CandidateDialog. The pool only stores a snapshot, so the rich record (parsed
 * CV, scores, authenticity, CV file) lives on an *application*.
 *
 *  - Candidates that already have an application (they came from a job, or were
 *    sourced onto one before) — we find it via the read-only candidate history
 *    and open it directly. Viewing is **non-destructive**: it never sources the
 *    candidate, so it doesn't move them out of the pool.
 *  - Candidates uploaded straight to the pool have no application yet. Building a
 *    profile requires sourcing them onto a job, which *does* move them — so we
 *    ask for a job explicitly rather than doing it silently on open.
 */
const ViewCandidateDialog = ({ entry, open, onClose }) => {
  const candidate = entry?.candidate;
  const sourceJobId = entry?.source_job_id || "";
  const [jobId, setJobId] = useState("");
  const [appId, setAppId] = useState(null);

  const source = useSourceToJob();
  const { mutate: sourceMutate, reset: sourceReset } = source;
  const jobsQuery = useJobs({ pageSize: 100 });

  // Read-only: the history lists every application the candidate has ever had.
  // If one exists we open it directly — no sourcing, so no move out of the pool.
  const history = useCandidateHistory(candidate?.id, { enabled: open && !!candidate?.id });
  const existingAppId = useMemo(() => {
    const stints = history.data?.stints ?? [];
    if (stints.length === 0) return null;
    // Prefer the stint for the job this pool entry was collected from.
    const match = stints.find((s) => s.job_id === sourceJobId) ?? stints[0];
    return match?.application_id ?? null;
  }, [history.data, sourceJobId]);

  const handleClose = () => {
    setJobId("");
    setAppId(null);
    sourceReset();
    onClose();
  };

  // Prefer an application that already exists (no move); otherwise use one we
  // just created by sourcing a never-sourced direct-upload candidate.
  const openAppId = appId || existingAppId;
  if (openAppId) {
    return <CandidateDialog appId={openAppId} open={open} onClose={handleClose} readOnly />;
  }

  // Only reached for candidates with no application at all. Sourcing here is
  // explicit (a job pick + button), never automatic.
  const resolve = (targetJobId) => {
    if (!candidate?.id || !targetJobId) return;
    sourceMutate(
      { candidateId: candidate.id, jobId: targetJobId },
      { onSuccess: (res) => setAppId(res.application_id) },
    );
  };

  const jobs = (jobsQuery.data?.items ?? []).filter((j) =>
    SOURCEABLE_STATUSES.includes(j.status),
  );
  const loadingProfile = history.isLoading || source.isPending;

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        View candidate
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>
          {candidate?.full_name || "Candidate"}
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 2 }}>
        {loadingProfile ? (
          <Stack spacing={1.5} sx={{ alignItems: "center", py: 3 }}>
            <CircularProgress size={28} />
            <Typography variant="body2" color="text.secondary">
              Loading full profile…
            </Typography>
          </Stack>
        ) : (
          <Stack spacing={2}>
            <Typography variant="body2" color="text.secondary">
              The pool keeps only a snapshot, and {firstName(candidate?.full_name)} hasn’t been
              sourced to a job yet. Pick one to build their full profile — parsed CV, scores and
              authenticity. This moves them onto that job.
            </Typography>
            <TextField
              select
              fullWidth
              label="Job"
              value={jobId}
              onChange={(e) => setJobId(e.target.value)}
              disabled={jobsQuery.isLoading || jobs.length === 0}
              helperText={
                jobsQuery.isLoading
                  ? "Loading jobs…"
                  : jobs.length === 0
                    ? "No open or draft jobs to open this candidate against."
                    : "Open and draft jobs are valid targets."
              }
              slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
            >
              <MenuItem value="" disabled>
                Select a job…
              </MenuItem>
              {jobs.map((job) => (
                <MenuItem key={job.id} value={job.id}>
                  <Stack
                    direction="row"
                    spacing={1}
                    sx={{ alignItems: "center", justifyContent: "space-between", width: "100%" }}
                  >
                    <Box component="span" sx={{ minWidth: 0 }}>
                      {job.title}
                      {job.hiring_company ? ` — ${job.hiring_company}` : ""}
                    </Box>
                    <Chip
                      size="small"
                      label={humanize(job.status)}
                      color={jobStatusColor(job.status)}
                      sx={{ height: 20, flexShrink: 0 }}
                    />
                  </Stack>
                </MenuItem>
              ))}
            </TextField>

            {source.isError && <ErrorAlert error={source.error} />}
            {jobsQuery.isError && <ErrorAlert error={jobsQuery.error} />}
            {history.isError && <ErrorAlert error={history.error} />}
          </Stack>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={handleClose} color="inherit">
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={() => resolve(jobId)}
          disabled={!jobId || source.isPending}
          startIcon={<VisibilityOutlinedIcon />}
        >
          View profile
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ViewCandidateDialog;
