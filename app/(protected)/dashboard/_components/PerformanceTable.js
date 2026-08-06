"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import Stack from "@mui/material/Stack";
import Skeleton from "@mui/material/Skeleton";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Box from "@mui/material/Box";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import { jobHealthChip, humanize, PIPELINE_BUCKETS } from "@/lib/kabil/constants";
import { useCandidatePipeline, useJobs } from "@/lib/kabil/queries";
import DonutChart from "./DonutChart";

/**
 * The Performance card body. The "Performance" heading and table/chart toggle
 * live in the dashboard page's section header; this component just renders the
 * body for the `view` it's handed:
 *  - a table — one row per non-draft job from `GET /dashboard/performance`
 *    (at-risk-first); each row links to that job's pipeline board.
 *  - a pie (donut) chart — "All Jobs" shows the health mix across every job in
 *    the payload; picking a single job shows that job's candidate pipeline
 *    buckets from `GET /dashboard/pipeline?job_id=…`.
 *
 * `candidates` / `shortlisted` exclude archived stints; `days_open` is whole
 * days since creation; `health` is the server-computed JobHealth verdict.
 */
const HEAD_CELLS = [
  { key: "title", label: "Role", align: "left" },
  { key: "status", label: "Status", align: "left" },
  { key: "candidates", label: "Candidates", align: "center" },
  { key: "shortlisted", label: "Shortlisted", align: "center" },
  { key: "days_open", label: "Days Open", align: "center" },
  { key: "health", label: "Health", align: "center" },
];

// Status dot color, keyed by JobStatus. Live roles read green; paused/ended
// roles read orange; anything else is a muted grey.
const STATUS_DOT = {
  open: "#0F6E56",
  closed: "#EF9F27",
  inactive: "#EF9F27",
  archived: "#8a948b",
  draft: "#8a948b",
};

// Health verdict → text color. Unhealthy reads red (worst), At Risk amber
// (early warning), Healthy green — matching the donut's segment colors.
const healthColor = (health) =>
  health === "unhealthy"
    ? "error.main"
    : health === "at_risk"
      ? "warning.main"
      : "success.main";

// Body cell typography. Role and the middle columns share Jakarta 500/10px (only
// the line-height differs); the Health column uses Inter 14px.
const ROLE_CELL_SX = {
  fontFamily: "var(--font-jakarta), system-ui, sans-serif",
  fontWeight: 500,
  fontSize: "10px",
  lineHeight: "12px",
  letterSpacing: "0.1px",
  color: "#2C2C2A",
};
const REST_CELL_SX = {
  fontFamily: "var(--font-jakarta), system-ui, sans-serif",
  fontWeight: 500,
  fontSize: "10px",
  lineHeight: "15px",
  letterSpacing: "0.1px",
  color: "#2C2C2A",
};
const HEALTH_CELL_SX = {
  fontFamily: "var(--font-sans), system-ui, sans-serif",
  fontWeight: 400,
  fontSize: "14px",
  lineHeight: "21px",
  letterSpacing: 0,
};

// Health categories for the "All Jobs" donut, in fixed legend order. Values are
// the backend JobHealth enum (see `jobHealthChip`); colors echo the brand.
const HEALTH_SEGMENTS = [
  { key: "healthy", label: "Healthy", note: "(<18 days)", color: "#0F6E56" },
  { key: "at_risk", label: "At Risk", note: "(18+ days)", color: "#EF9F27" },
  { key: "unhealthy", label: "Unhealthy", note: "(20+ days)", color: "#D85A30" },
];

/** Donut segments for the workspace-wide health distribution. */
const healthSegments = (rows) =>
  HEALTH_SEGMENTS.map((s) => ({
    label: s.label,
    note: s.note,
    color: s.color,
    value: rows.filter((r) => r.health === s.key).length,
  }));

/** Donut segments for one job's candidate pipeline buckets. */
const bucketSegments = (byBucket) =>
  PIPELINE_BUCKETS.map((b) => ({
    label: b.label,
    color: b.accent,
    value: byBucket?.[b.bucket] ?? 0,
  }));

const PerformanceTable = ({ data, loading, view = "table" }) => {
  const router = useRouter();
  const [selectedJob, setSelectedJob] = useState(""); // "" = All Jobs

  const allRows = data?.rows ?? [];

  // Job-selector options: the shared alphabetical-by-title list, so this
  // dropdown lists jobs in the same order as the Candidate Pipeline card's.
  // The table body below still renders `allRows` in its at-risk-first order.
  const { data: jobsData, isLoading: jobsLoading } = useJobs({
    order: "title",
    pageSize: 100,
  });
  const jobOptions = jobsData?.items ?? [];

  // Per-job pipeline — only fetched in chart mode with a specific job picked.
  const { data: pipeline, isLoading: pipelineLoading } = useCandidatePipeline(
    selectedJob || null,
    { enabled: view === "chart" && Boolean(selectedJob) },
  );

  const isChart = view === "chart";
  const chartLoading = loading || (Boolean(selectedJob) && pipelineLoading);
  const segments = selectedJob
    ? bucketSegments(pipeline?.by_bucket)
    : healthSegments(allRows);

  return (
    <Card sx={{ borderRadius: 2.5, height: "100%", display: "flex", flexDirection: "column" }}>
      <CardContent
        sx={{
          p: 2,
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          "&:last-child": { pb: 2 },
        }}
      >
        {isChart ? (
          <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <Stack
              direction="row"
              sx={{ justifyContent: "space-between", alignItems: "center", gap: 1.5, mb: 1 }}
            >
              <Stack direction="row" spacing={0.75} sx={{ alignItems: "center", minWidth: 0 }}>
                {/* Circle + activity-pulse glyph (inline so it inherits the
                    brand orange and the exact 12px size). */}
                <Box
                  component="svg"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                  sx={{ width: 20, height: 20, flexShrink: 0, color: "#EF9F27", display: "block" }}
                >
                  <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
                  <path
                    d="M6 13 L9 13 L11 16 L13 8 L15 13 L18 13"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </Box>
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
                  {/* All-Jobs shows the health mix; a single job shows its
                      pipeline buckets — label reflects what the donut plots. */}
                  {selectedJob ? "Candidate Pipeline" : "Health Status"}
                </Typography>
              </Stack>
              <TextField
                select
                size="small"
                value={selectedJob}
                onChange={(e) => setSelectedJob(e.target.value)}
                disabled={loading || jobsLoading}
                slotProps={{
                  select: {
                    displayEmpty: true,
                    MenuProps: { slotProps: { list: { sx: { fontSize: "0.8125rem" } } } },
                  },
                }}
                // Smaller font lets a narrower field fit the longest job title;
                // the menu inherits the trigger width so both stay in sync.
                sx={{ width: 210, "& .MuiSelect-select": { fontSize: "0.8125rem" } }}
              >
                <MenuItem value="" sx={{ fontSize: "0.8125rem" }}>
                  All Jobs
                </MenuItem>
                {jobOptions.map((j) => (
                  <MenuItem key={j.id} value={j.id} sx={{ fontSize: "0.8125rem" }}>
                    {j.title}
                  </MenuItem>
                ))}
              </TextField>
            </Stack>

            {chartLoading ? (
              <Stack sx={{ alignItems: "center", py: 3 }}>
                <Skeleton variant="circular" width={210} height={210} />
              </Stack>
            ) : (
              <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
                <DonutChart segments={segments} />
              </Box>
            )}
          </Box>
        ) : loading ? (
          <Stack spacing={1.25}>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} variant="rounded" height={40} />
            ))}
          </Stack>
        ) : allRows.length === 0 ? (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ py: 3, textAlign: "center" }}
          >
            No jobs yet.
          </Typography>
        ) : (
          <Box sx={{ overflow: "auto", flex: 1, minHeight: 0, mx: -2 }}>
            <Table
              size="small"
              stickyHeader
              sx={{ "& td, & th": { borderColor: "divider", py: 1.75 }, "& tbody td": REST_CELL_SX }}
            >
              <TableHead>
                <TableRow>
                  {HEAD_CELLS.map((c) => (
                    <TableCell
                      key={c.key}
                      align={c.align}
                      sx={{
                        fontWeight: 700,
                        color: "text.secondary",
                        whiteSpace: "nowrap",
                        textTransform: "uppercase",
                        letterSpacing: 0.4,
                        fontSize: "0.7rem",
                        bgcolor: "background.paper",
                      }}
                    >
                      {c.label}
                    </TableCell>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {allRows.map((r) => (
                  <TableRow
                    key={r.job_id}
                    hover
                    onClick={() => router.push(`/jobs/${r.job_id}/pipeline`)}
                    sx={{ cursor: "pointer", "&:last-child td": { border: 0 } }}
                  >
                    <TableCell sx={{ maxWidth: 220 }}>
                      <Typography noWrap sx={ROLE_CELL_SX}>
                        {r.title}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
                        <Box
                          sx={{
                            width: 8,
                            height: 8,
                            borderRadius: "50%",
                            flexShrink: 0,
                            bgcolor: STATUS_DOT[r.status] ?? "#8a948b",
                          }}
                        />
                        <Typography sx={REST_CELL_SX}>{humanize(r.status)}</Typography>
                      </Stack>
                    </TableCell>
                    <TableCell align="center">{r.candidates}</TableCell>
                    <TableCell align="center">{r.shortlisted}</TableCell>
                    <TableCell align="center">{r.days_open}</TableCell>
                    <TableCell align="center">
                      <Typography sx={{ ...HEALTH_CELL_SX, color: healthColor(r.health) }}>
                        {jobHealthChip(r.health).label}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        )}
      </CardContent>
    </Card>
  );
};

export default PerformanceTable;
