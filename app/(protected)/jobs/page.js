"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Card from "@mui/material/Card";
import Tabs from "@mui/material/Tabs";
import Tab from "@mui/material/Tab";
import Button from "@mui/material/Button";
import Snackbar from "@mui/material/Snackbar";
import Alert from "@mui/material/Alert";
import AddIcon from "@mui/icons-material/Add";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import SearchField from "@/components/SearchField";
import { useDashboard, useJobs } from "@/lib/kabil/queries";
import JobCard, { JobCardSkeleton } from "./_components/JobCard";

// Tabs as drawn in the design. `status: null` → no status filter; every other
// tab filters the list by its backend status value ("Active" === `open`).
const TABS = [
  { label: "Active", status: "open" },
  { label: "All", status: null },
  { label: "Drafts", status: "draft" },
  { label: "Inactive", status: "inactive" },
  { label: "Archived", status: "archived" },
];

/** Responsive grid (1 / 2 / 3 / 4 columns). `minmax(0, 1fr)` (not the bare
 *  `1fr`, which is `minmax(auto, 1fr)`) lets each track shrink below its card's
 *  intrinsic width so the columns always fit the row instead of overflowing. */
const grid = {
  display: "grid",
  gap: 2.5,
  gridTemplateColumns: {
    xs: "1fr",
    sm: "repeat(2, minmax(0, 1fr))",
    md: "repeat(3, minmax(0, 1fr))",
    lg: "repeat(4, minmax(0, 1fr))",
  },
};

/** Circular count pill shown beside each tab label (design spec: #EF9F27 bg,
 *  white Plus Jakarta Sans 700 / 10px / 13.6px). Rounds to a pill for 3+ digits. */
const CountBadge = ({ count }) => (
  <Box
    component="span"
    sx={{
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      ml: 0.75,
      minWidth: 20,
      height: 20,
      px: 0.75,
      borderRadius: 999,
      bgcolor: "#EF9F27",
      color: "#fff",
      fontFamily: '"Plus Jakarta Sans", sans-serif',
      fontWeight: 700,
      fontSize: "10px",
      lineHeight: "13.6px",
      letterSpacing: 0,
    }}
  >
    {count}
  </Box>
);

const JobsGridSkeleton = () => (
  <Box sx={grid}>
    {Array.from({ length: 6 }).map((_, i) => (
      <JobCardSkeleton key={i} />
    ))}
  </Box>
);

const JobsPageInner = () => {
  const searchParams = useSearchParams();
  const [tab, setTab] = useState(0);
  const [search, setSearch] = useState("");
  const [createdToast, setCreatedToast] = useState(!!searchParams.get("created"));

  const activeTab = TABS[tab];
  const { data, isLoading, isError, error, refetch } = useJobs({
    status: activeTab.status || undefined,
    search: search.trim() || undefined,
    pageSize: 50,
  });

  const jobs = data?.items ?? [];

  // Per-tab counts come from the workspace rollup (jobs.total + jobs.by_status,
  // zero-filled across every JobStatus), so the badges are independent of the
  // active tab's filter/search. `null` while the summary loads → badge hidden.
  const { data: summary } = useDashboard();
  const countFor = (status) => {
    if (!summary?.jobs) return null;
    return status === null ? summary.jobs.total : summary.jobs.by_status[status] ?? 0;
  };

  return (
    <Stack spacing={2.5}>
      {/* Toolbar: search + primary action */}
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={2}
        sx={{
          alignItems: { xs: "stretch", sm: "center" },
          justifyContent: "space-between",
        }}
      >
        <SearchField
          placeholder="Search Jobs…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{
            width: { xs: "100%", sm: 300 },
            "& .MuiOutlinedInput-root": {
              borderRadius: "5px",
              bgcolor: "#f2efe8",
              py: 0,
              "& fieldset": { border: "none" },
              "&:hover fieldset": { border: "none" },
              "&.Mui-focused fieldset": { border: "none" },
            },
          }}
        />
        <Button
          component={Link}
          href="/jobs/new"
          variant="contained"
          startIcon={<AddIcon />}
          sx={{ flexShrink: 0, borderRadius: "5px", px: 2.5 }}
        >
          Post New Job
        </Button>
      </Stack>

      <Box>
        <Tabs value={tab} onChange={(_, v) => setTab(v)}>
          {TABS.map((t) => {
            const count = countFor(t.status);
            return (
              <Tab
                key={t.label}
                iconPosition="end"
                icon={count === null ? undefined : <CountBadge count={count} />}
                label={t.label}
                sx={{ textTransform: "none", fontWeight: 600, minHeight: "auto" }}
              />
            );
          })}
        </Tabs>
      </Box>

      {isLoading ? (
        <JobsGridSkeleton />
      ) : isError ? (
        <ErrorAlert error={error} />
      ) : jobs.length === 0 ? (
        <Card sx={{ borderRadius: 2 }}>
          <EmptyState
            emoji="💼"
            title={search ? "No matching jobs" : "No jobs here yet"}
            description={
              search
                ? "Try a different search term."
                : activeTab.status
                  ? `No ${activeTab.label.toLowerCase()} jobs. Post your first job to get started.`
                  : "No jobs yet. Post your first job to get started."
            }
            action={
              !search && (
                <Button component={Link} href="/jobs/new" variant="contained">
                  Post a Job
                </Button>
              )
            }
          />
        </Card>
      ) : (
        <Box sx={grid}>
          {jobs.map((job) => (
            <JobCard key={job.id} job={job} />
          ))}
        </Box>
      )}

      <Snackbar
        open={createdToast}
        autoHideDuration={5000}
        onClose={() => setCreatedToast(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          severity="success"
          variant="filled"
          onClose={() => setCreatedToast(false)}
          action={
            <Button color="inherit" size="small" onClick={() => refetch()}>
              Refresh
            </Button>
          }
        >
          Job created — it’s now in your list.
        </Alert>
      </Snackbar>
    </Stack>
  );
};

const JobsPage = () => (
  <Suspense fallback={<JobsGridSkeleton />}>
    <JobsPageInner />
  </Suspense>
);

export default JobsPage;
