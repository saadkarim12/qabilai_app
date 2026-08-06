"use client";

import { useState } from "react";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import Stack from "@mui/material/Stack";
import Box from "@mui/material/Box";
import Skeleton from "@mui/material/Skeleton";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import TimelineOutlinedIcon from "@mui/icons-material/TimelineOutlined";
import { PIPELINE_COLUMNS } from "@/lib/kabil/constants";
import { useCandidatePipeline, useJobs } from "@/lib/kabil/queries";

/** One funnel row: bucket label, count, and a proportional bar. */
const FunnelRow = ({ label, accent, count, max }) => (
  <Box>
    <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "baseline", mb: 0.5 }}>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        {label}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600 }}>
        {count}
      </Typography>
    </Stack>
    <LinearProgress
      variant="determinate"
      value={max > 0 ? (count / max) * 100 : 0}
      sx={{
        height: 8,
        borderRadius: 999,
        bgcolor: "rgba(19,64,45,0.06)",
        "& .MuiLinearProgress-bar": { borderRadius: 999, backgroundColor: accent },
      }}
    />
  </Box>
);

/**
 * Candidate Pipeline funnel with an "All Jobs" / per-job selector. The dropdown
 * is populated from the jobs list; selecting a job re-queries
 * `GET /dashboard/pipeline?job_id=…` (empty selection = workspace-wide). The
 * funnel rows mirror the kanban board's five stage columns, using the
 * per-stage counts (`by_stage`) from the same payload.
 */
const CandidatePipelinePanel = () => {
  // Default to "All Jobs" (value "") — the workspace-wide aggregate.
  const [jobId, setJobId] = useState("");
  // Alphabetical by title — the canonical dropdown order shared with the
  // Performance card's job selector so both list jobs identically.
  const { data: jobsData } = useJobs({ pageSize: 50, order: "title" });
  const jobs = jobsData?.items ?? [];

  const { data, isLoading } = useCandidatePipeline(jobId || null, {
    // Hold the pipeline query until the job list resolves.
    enabled: jobsData !== undefined,
  });
  const loading = isLoading || jobsData === undefined;

  // One funnel row per kanban stage column, counted from the payload's raw
  // per-stage map so the panel and the board list the exact same five steps.
  const rows = PIPELINE_COLUMNS.map((c) => ({
    ...c,
    count: data ? (data.by_stage?.[c.stage] ?? 0) : 0,
  }));
  const max = Math.max(1, ...rows.map((r) => r.count));

  return (
    <Card
      sx={{ borderRadius: 2.5, height: "100%" }}
    >
      <CardContent sx={{ p: 2 }}>
        <Stack
          direction="row"
          sx={{ alignItems: "center", justifyContent: "space-between", gap: 1.5, mb: 2 }}
        >
          <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", minWidth: 0 }}>
            <TimelineOutlinedIcon sx={{ width: 20, height: 20, color: "#EF9F27" }} />
            <Typography
              noWrap
              sx={{
                fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                fontWeight: 700,
                fontSize: "12px",
                lineHeight: "16px",
                letterSpacing: 0,
                color: "#1C4A3E",
              }}
            >
              Candidate Pipeline
            </Typography>
          </Stack>
          <TextField
            select
            size="small"
            value={jobId}
            onChange={(e) => setJobId(e.target.value)}
            slotProps={{
              select: {
                displayEmpty: true,
                renderValue: (value) =>
                  value ? (jobs.find((j) => j.id === value)?.title ?? "All Jobs") : "All Jobs",
                MenuProps: { slotProps: { list: { sx: { fontSize: "0.8125rem" } } } },
              },
            }}
            // Smaller font lets a narrower field fit the longest job title; the
            // menu inherits the trigger width so both stay in sync.
            sx={{ width: 210, "& .MuiSelect-select": { fontSize: "0.8125rem" } }}
          >
            <MenuItem value="" sx={{ fontSize: "0.8125rem" }}>
              All Jobs
            </MenuItem>
            {jobs.map((j) => (
              <MenuItem key={j.id} value={j.id} sx={{ fontSize: "0.8125rem" }}>
                {j.title}
              </MenuItem>
            ))}
          </TextField>
        </Stack>

        {loading ? (
          <Stack spacing={2.25}>
            {PIPELINE_COLUMNS.map((_, i) => (
              <Box key={i}>
                <Skeleton variant="text" width="40%" />
                <Skeleton variant="rounded" height={8} sx={{ borderRadius: 999 }} />
              </Box>
            ))}
          </Stack>
        ) : (
          <Stack spacing={2.25}>
            {rows.map((r) => (
              <FunnelRow key={r.stage} label={r.label} accent={r.accent} count={r.count} max={max} />
            ))}
          </Stack>
        )}
      </CardContent>
    </Card>
  );
};

export default CandidatePipelinePanel;
