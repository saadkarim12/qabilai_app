"use client";

import { useState } from "react";
import Link from "next/link";
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
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import CircularProgress from "@mui/material/CircularProgress";
import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import ErrorAlert from "@/components/ErrorAlert";
import { useJobs, useSourceToJob } from "@/lib/kabil/queries";
import { jobStatusColor, humanize } from "@/lib/kabil/constants";

/** Jobs you can source onto: live or being prepared. Closed postings are excluded. */
const SOURCEABLE_STATUSES = ["open", "draft"];

/**
 * Picks a job and sources the given pooled candidate onto it. On success the
 * backend may report `already_existed` (the candidate was already on that job),
 * which we surface rather than pretend a new application was created.
 */
const SourceToJobDialog = ({ candidate, open, onClose }) => {
  const [jobId, setJobId] = useState("");
  const [result, setResult] = useState(null);

  const { data, isLoading, isError, error } = useJobs({ pageSize: 100 });
  const jobs = (data?.items ?? []).filter((j) => SOURCEABLE_STATUSES.includes(j.status));

  const source = useSourceToJob();

  const reset = () => {
    setJobId("");
    setResult(null);
    source.reset();
  };

  const close = () => {
    reset();
    onClose();
  };

  const handleSource = () => {
    if (!candidate?.id || !jobId) return;
    source.mutate(
      { candidateId: candidate.id, jobId },
      { onSuccess: (res) => setResult(res) },
    );
  };

  const selectedJob = jobs.find((j) => j.id === jobId);

  return (
    <Dialog open={open} onClose={close} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        Source to a job
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>
          {candidate?.full_name || "Candidate"}
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 2 }}>
        {result ? (
          <Alert severity={result.already_existed ? "info" : "success"}>
            <AlertTitle>
              {result.already_existed
                ? "Already on this job"
                : "Sourced — pipeline started"}
            </AlertTitle>
            <Stack spacing={1}>
              <Typography variant="body2">
                {result.already_existed
                  ? `${candidate?.full_name || "This candidate"} already had an application for ${selectedJob?.title || "this job"}.`
                  : `${candidate?.full_name || "The candidate"} was added to ${selectedJob?.title || "the job"} at the Applied stage. The CV scoring pipeline is running now.`}
              </Typography>
              <Box
                component={Link}
                href={`/jobs/${result.job_id}/pipeline`}
                sx={{ fontWeight: 700, color: "primary.main" }}
              >
                Open pipeline →
              </Box>
            </Stack>
          </Alert>
        ) : isError ? (
          <ErrorAlert error={error} />
        ) : (
          <Stack spacing={2} sx={{ mt: 2 }}>
            <TextField
              select
              fullWidth
              label="Job"
              value={jobId}
              onChange={(e) => setJobId(e.target.value)}
              disabled={isLoading || source.isPending || jobs.length === 0}
              helperText={
                isLoading
                  ? "Loading jobs…"
                  : jobs.length === 0
                    ? "No open or draft jobs to source onto."
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
                    <Box
                      component="span"
                      sx={{
                        flexGrow: 1,
                        minWidth: 0,
                        display: "block",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
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
          </Stack>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {result ? (
          <Button onClick={close} variant="contained">
            Done
          </Button>
        ) : (
          <>
            <Button onClick={close} color="inherit">
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handleSource}
              disabled={!jobId || source.isPending}
              startIcon={
                source.isPending ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <SendOutlinedIcon />
                )
              }
            >
              {source.isPending ? "Sourcing…" : "Source candidate"}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default SourceToJobDialog;
