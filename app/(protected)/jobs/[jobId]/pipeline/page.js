"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Card from "@mui/material/Card";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import TextField from "@mui/material/TextField";
import InputAdornment from "@mui/material/InputAdornment";
import Skeleton from "@mui/material/Skeleton";
import Snackbar from "@mui/material/Snackbar";
import Alert from "@mui/material/Alert";
import SearchIcon from "@mui/icons-material/Search";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import SwapVertIcon from "@mui/icons-material/SwapVert";
import CheckIcon from "@mui/icons-material/Check";
import CircleIcon from "@mui/icons-material/Circle";
import PlaceOutlinedIcon from "@mui/icons-material/PlaceOutlined";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutlined";
import HighlightOffIcon from "@mui/icons-material/HighlightOff";
import ShareOutlinedIcon from "@mui/icons-material/ShareOutlined";
import PeopleAltOutlinedIcon from "@mui/icons-material/PeopleAltOutlined";
import LinkedInIcon from "@mui/icons-material/LinkedIn";
import FileDownloadOutlinedIcon from "@mui/icons-material/FileDownloadOutlined";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import {
  useJob,
  useApplications,
  useMoveStage,
  useSetStatus,
} from "@/lib/kabil/queries";
import { PIPELINE_COLUMNS, humanize, stageLabel, toScore } from "@/lib/kabil/constants";
import PipelineBoard from "./_components/PipelineBoard";
import RejectedList from "./_components/RejectedList";
import CandidateDialog from "./_components/CandidateDialog";

/** Shared compact styling for the header action buttons (Share/TP/LinkedIn/Export). */
const PILL_BTN_SX = {
  textTransform: "none",
  fontWeight: 600,
  fontSize: "0.78rem",
  borderRadius: "5px",
  px: 1.5,
  minHeight: 34,
  boxShadow: "none",
  whiteSpace: "nowrap",
  "& .MuiButton-startIcon": { mr: 0.5 },
  "& .MuiButton-startIcon > *:first-of-type": { fontSize: 16 },
};
const BEIGE_BTN_SX = {
  ...PILL_BTN_SX,
  bgcolor: "#F4F0E8",
  color: "#4a4a4a",
  "&:hover": { bgcolor: "#F4F0E8", boxShadow: "none" },
  "&.Mui-disabled": { bgcolor: "#f3f1ec", color: "#b7b2a6" },
};
const GREEN_BTN_SX = {
  ...PILL_BTN_SX,
  bgcolor: "#e6f1ec",
  color: "#0F6E56",
  border: "1px solid #0F6E56",
  "&:hover": { bgcolor: "#e6f1ec", border: "1px solid #0F6E56" },
};
const LINKEDIN_BTN_SX = {
  ...PILL_BTN_SX,
  bgcolor: "#eef4fb",
  color: "#2f6fb0",
  border: "1px solid #9db8d6",
  "&:hover": { bgcolor: "#eef4fb", border: "1px solid #2f6fb0" },
};

/* ── Sorting ────────────────────────────────────────────────────────────────
 * The board sorts each column independently: "Highest score" ranks a column by
 * that step's own headline score (Applied by Profile Match, Screening/Assessment
 * by CV Score, Interviewed/Shortlist by the interview mark), so a single global
 * sort won't do. All applicants are already in memory (one paged fetch), so the
 * sort is client-side, applied per column when bucketing. */
const SORT_OPTIONS = [
  { value: "newest", label: "Newest first" },
  { value: "score", label: "Highest score" },
  { value: "lowest", label: "Lowest score" },
];
const SORT_LABELS = Object.fromEntries(SORT_OPTIONS.map((o) => [o.value, o.label]));

/** The score that leads a card at its current stage, as a number (or null). */
const stageScore = (a) => {
  if (a.stage === "vector_screen") return toScore(a.similarity_score);
  if (a.stage === "interview" || a.stage === "done")
    return (
      toScore(a.interview_score) ?? toScore(a.hard_filter_score) ?? toScore(a.similarity_score)
    );
  return toScore(a.hard_filter_score) ?? toScore(a.similarity_score);
};

const byNewest = (a, b) => new Date(b.created_at) - new Date(a.created_at);

/** Sort by stage-score in `dir` (1 = highest first, -1 = lowest first). Unscored
 *  (pending) cards always sink to the bottom either way; newest breaks ties. */
const byScoreDir = (dir) => (a, b) => {
  const sa = stageScore(a);
  const sb = stageScore(b);
  if (sa == null && sb == null) return byNewest(a, b);
  if (sa == null) return 1;
  if (sb == null) return -1;
  return dir * (sb - sa) || byNewest(a, b);
};
const byScore = byScoreDir(1);
const byScoreAsc = byScoreDir(-1);

const comparatorFor = (mode) =>
  mode === "score" ? byScore : mode === "lowest" ? byScoreAsc : byNewest;

/** Accepted / Rejected filter pill in the header. */
const TabPill = ({ selected, label, count, icon: Icon, onSelect }) => (
  <Box
    role="button"
    tabIndex={0}
    onClick={onSelect}
    onKeyDown={(e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSelect();
      }
    }}
    sx={{
      display: "inline-flex",
      alignItems: "center",
      gap: 0.5,
      px: 1.25,
      py: 0.5,
      borderRadius: "5px",
      cursor: "pointer",
      userSelect: "none",
      fontWeight: 600,
      fontSize: "0.8rem",
      border: "1px solid",
      borderColor: selected ? "transparent" : "#e4ddcd",
      bgcolor: selected ? "#e6f1ec" : "#faf8f3",
      color: selected ? "#0F6E56" : "#5f6b66",
      transition: "background-color .15s ease, color .15s ease, border-color .15s ease",
      "&:hover": { bgcolor: selected ? "#e6f1ec" : "#f2efe8" },
      "&:focus-visible": { outline: "2px solid #0F6E56", outlineOffset: 2 },
    }}
  >
    <Icon sx={{ fontSize: 15 }} />
    {label}
    <Box
      component="span"
      sx={{
        minWidth: 20,
        height: 20,
        px: 0.5,
        borderRadius: 999,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: "0.7rem",
        fontWeight: 700,
        bgcolor: selected ? "#0F6E56" : "#eceae3",
        color: selected ? "#fff" : "#5f6b66",
      }}
    >
      {count}
    </Box>
  </Box>
);

const PipelinePage = ({ params }) => {
  const { jobId } = use(params);
  const { data: job, isLoading: jobLoading } = useJob(jobId);
  const {
    data: appsData,
    isLoading: appsLoading,
    isError,
    error,
  } = useApplications(jobId, { pageSize: 100 });

  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState("newest"); // "newest" | "score"
  const [sortAnchor, setSortAnchor] = useState(null);
  const [tab, setTab] = useState("accepted");
  const [selectedId, setSelectedId] = useState(null);
  const [pendingId, setPendingId] = useState(null);
  const [toast, setToast] = useState(null);
  const [actionsAnchor, setActionsAnchor] = useState(null);
  const closeActions = () => setActionsAnchor(null);

  const moveStage = useMoveStage();
  const setStatus = useSetStatus();

  const items = useMemo(() => appsData?.items ?? [], [appsData]);

  // Rejected applicants live in their own tab; everything else is "accepted".
  const acceptedItems = useMemo(() => items.filter((a) => a.status !== "rejected"), [items]);
  const rejectedItems = useMemo(() => items.filter((a) => a.status === "rejected"), [items]);

  const matchesSearch = (a) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      a.candidate_full_name?.toLowerCase().includes(q) ||
      a.candidate_email?.toLowerCase().includes(q)
    );
  };

  const byStage = useMemo(() => {
    const groups = Object.fromEntries(PIPELINE_COLUMNS.map((c) => [c.stage, []]));
    for (const app of acceptedItems) {
      if (!matchesSearch(app)) continue;
      (groups[app.stage] ??= []).push(app);
    }
    // Sort each column on its own, so "Highest score" ranks every stage by its
    // own headline score rather than one global column.
    const cmp = comparatorFor(sortMode);
    for (const stage of Object.keys(groups)) groups[stage].sort(cmp);
    return groups;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [acceptedItems, search, sortMode]);

  const filteredRejected = useMemo(
    () => rejectedItems.filter(matchesSearch).sort(comparatorFor(sortMode)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rejectedItems, search, sortMode],
  );

  const total = appsData?.total ?? 0;

  // Public application link built from the job's slug — candidates apply here
  // (anonymous). Shared via the native sheet, with a clipboard fallback.
  const applyUrl =
    job?.public_slug && typeof window !== "undefined"
      ? `${window.location.origin}/apply/${job.public_slug}`
      : "";

  const share = async () => {
    if (!applyUrl) {
      setToast({ severity: "error", msg: "No public link available for this job yet." });
      return;
    }
    const data = {
      title: `Apply: ${job?.title || "Open role"}`,
      text: `Apply for ${job?.title || "this role"}${job?.hiring_company ? ` at ${job.hiring_company}` : ""}.`,
      url: applyUrl,
    };
    try {
      if (navigator.share) {
        await navigator.share(data);
        return;
      }
      await navigator.clipboard.writeText(applyUrl);
      setToast({ severity: "success", msg: "Public apply link copied to clipboard" });
    } catch {
      /* user dismissed the share sheet — nothing to report */
    }
  };

  // Open LinkedIn's share composer pre-filled with the public apply link.
  const shareLinkedIn = () => {
    if (!applyUrl) {
      setToast({ severity: "error", msg: "No public link available for this job yet." });
      return;
    }
    window.open(
      `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(applyUrl)}`,
      "_blank",
      "noopener,noreferrer",
    );
  };

  // Client-side CSV of the currently-loaded applicants (no backend round-trip).
  const exportCsv = () => {
    if (!items.length) {
      setToast({ severity: "error", msg: "No applicants to export yet." });
      return;
    }
    const headers = ["Name", "Email", "Stage", "Status", "Score", "Applied"];
    const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const rows = items.map((a) => [
      a.candidate_full_name,
      a.candidate_email,
      stageLabel(a.stage),
      humanize(a.status),
      toScore(a.hard_filter_score) ?? toScore(a.similarity_score) ?? "",
      a.created_at,
    ]);
    const csv = [headers, ...rows].map((r) => r.map(esc).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(job?.title || "applicants").replace(/\s+/g, "-").toLowerCase()}-pipeline.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleMove = (app, stage) => {
    moveStage.mutate(
      { appId: app.id, stage },
      {
        onSuccess: () =>
          setToast({
            severity: "success",
            msg: `${app.candidate_full_name} moved to ${stageLabel(stage)}`,
          }),
        onError: () =>
          setToast({ severity: "error", msg: "Couldn't move candidate. Please try again." }),
      },
    );
  };

  const reactivate = (app) => {
    setPendingId(app.id);
    setStatus.mutate(
      { appId: app.id, status: "active" },
      {
        onSuccess: () =>
          setToast({
            severity: "success",
            msg: `${app.candidate_full_name} moved back to Accepted`,
          }),
        onError: () =>
          setToast({ severity: "error", msg: "Couldn't reactivate candidate. Please try again." }),
        onSettled: () => setPendingId(null),
      },
    );
  };

  const ready = !appsLoading && !isError && total > 0;

  return (
    <Box sx={{ bgcolor: "#F9F7F3", borderRadius: 2.5, p: 0 }}>
      <Stack spacing={1}>
        {/* Header — white panel */}
        <Box sx={{ bgcolor: "#fff", borderRadius: "5px", p: { xs: 1.25, sm: 1.5 } }}>
        {jobLoading ? (
          <Skeleton variant="text" width="50%" height={32} />
        ) : (
          <Stack spacing={1.5}>
            {/* Left: title + status + location + Accepted/Rejected.  Right: search + actions. */}
            <Stack
              direction={{ xs: "column", lg: "row" }}
              spacing={2}
              sx={{ justifyContent: "space-between", alignItems: { xs: "stretch", lg: "flex-start" } }}
            >
              <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
                  <Typography
                    sx={{
                      fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                      fontWeight: 700,
                      fontSize: "17px",
                      lineHeight: "25.5px",
                      letterSpacing: 0,
                      color: "#1C4A3E",
                    }}
                  >
                    {job?.title ?? "Pipeline"}
                  </Typography>
                    {job && (
                      <Stack
                        direction="row"
                        spacing={0.5}
                        sx={{
                          alignItems: "center",
                          px: 0.9,
                          py: 0.2,
                          borderRadius: 999,
                          bgcolor: job.status === "open" ? "#e6f1ec" : "#eef0ef",
                        }}
                      >
                        <CircleIcon
                          sx={{
                            fontSize: 8,
                            color: job.status === "open" ? "#0F6E56" : "text.disabled",
                          }}
                        />
                        <Typography
                          variant="caption"
                          sx={{
                            fontWeight: 700,
                            color: job.status === "open" ? "#0F6E56" : "text.secondary",
                          }}
                        >
                          {humanize(job.status)}
                        </Typography>
                      </Stack>
                    )}
                  </Stack>
                {job && (
                  <Stack
                    direction="row"
                    spacing={0.5}
                    sx={{ alignItems: "center", mt: 0.25, color: "#6B7280" }}
                  >
                    <PlaceOutlinedIcon sx={{ fontSize: 15 }} />
                    <Typography
                      sx={{
                        fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                        fontWeight: 400,
                        fontSize: "12.5px",
                        lineHeight: "18.75px",
                        letterSpacing: 0,
                        color: "#6B7280",
                      }}
                    >
                      {job.hiring_company} · {job.city}
                    </Typography>
                  </Stack>
                )}
                  {ready && (
                    <Stack direction="row" spacing={1} sx={{ mt: 1.25, flexWrap: "wrap", gap: 1 }}>
                      <TabPill
                        selected={tab === "accepted"}
                        label="Accepted"
                        count={acceptedItems.length}
                        icon={CheckCircleOutlineIcon}
                        onSelect={() => setTab("accepted")}
                      />
                      <TabPill
                        selected={tab === "rejected"}
                        label="Rejected"
                        count={rejectedItems.length}
                        icon={HighlightOffIcon}
                        onSelect={() => setTab("rejected")}
                      />
                      {/* Sort control — reorders each column by its own step score. */}
                      <Button
                        variant="text"
                        startIcon={<SwapVertIcon />}
                        endIcon={<KeyboardArrowDownIcon />}
                        onClick={(e) => setSortAnchor(e.currentTarget)}
                        sx={BEIGE_BTN_SX}
                      >
                        {SORT_LABELS[sortMode]}
                      </Button>
                      <Menu
                        anchorEl={sortAnchor}
                        open={Boolean(sortAnchor)}
                        onClose={() => setSortAnchor(null)}
                        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
                        transformOrigin={{ vertical: "top", horizontal: "left" }}
                      >
                        {SORT_OPTIONS.map((opt) => (
                          <MenuItem
                            key={opt.value}
                            selected={sortMode === opt.value}
                            onClick={() => {
                              setSortMode(opt.value);
                              setSortAnchor(null);
                            }}
                          >
                            <ListItemIcon>
                              {sortMode === opt.value ? <CheckIcon fontSize="small" /> : null}
                            </ListItemIcon>
                            {opt.label}
                          </MenuItem>
                        ))}
                      </Menu>
                    </Stack>
                  )}
                  {/* The board loads a single page of 100; sorting is over that
                      window, so say so when there are more than we've fetched. */}
                  {ready && total > items.length && (
                    <Typography variant="caption" sx={{ display: "block", mt: 0.75, color: "text.secondary" }}>
                      Showing the {items.length} most recent of {total} applicants — sorting applies to these.
                    </Typography>
                  )}
                </Box>

                {/* Search + actions */}
                <Stack
                  direction="row"
                  spacing={1}
                  sx={{ flexShrink: 0, flexWrap: "wrap", gap: 1, alignItems: "center" }}
                >
                  <TextField
                    size="small"
                    placeholder="Search by name, job…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    sx={{
                      width: { xs: "100%", sm: 220 },
                      "& .MuiOutlinedInput-root": { borderRadius: "5px" },
                    }}
                    InputProps={{
                      startAdornment: (
                        <InputAdornment position="start">
                          <SearchIcon fontSize="small" />
                        </InputAdornment>
                      ),
                    }}
                  />
                  {/* Desktop: individual action buttons */}
                  <Box sx={{ display: { xs: "none", md: "flex" }, flexWrap: "wrap", gap: 1, alignItems: "center" }}>
                    <Button
                      variant="text"
                      startIcon={<ShareOutlinedIcon />}
                      onClick={share}
                      disabled={!job?.public_slug}
                      sx={BEIGE_BTN_SX}
                    >
                      Share
                    </Button>
                    <Button
                      component={Link}
                      href="/talent-pool"
                      variant="outlined"
                      startIcon={<PeopleAltOutlinedIcon />}
                      sx={GREEN_BTN_SX}
                    >
                      TP Matches
                    </Button>
                    <Button
                      variant="outlined"
                      startIcon={<LinkedInIcon />}
                      onClick={shareLinkedIn}
                      disabled={!job?.public_slug}
                      sx={LINKEDIN_BTN_SX}
                    >
                      LinkedIn
                    </Button>
                    <Button
                      variant="text"
                      startIcon={<FileDownloadOutlinedIcon />}
                      onClick={exportCsv}
                      disabled={!total}
                      sx={BEIGE_BTN_SX}
                    >
                      Export
                    </Button>
                  </Box>

                  {/* Mobile: the four actions folded into one dropdown */}
                  <Box sx={{ display: { xs: "flex", md: "none" } }}>
                    <Button
                      variant="text"
                      endIcon={<KeyboardArrowDownIcon />}
                      onClick={(e) => setActionsAnchor(e.currentTarget)}
                      sx={GREEN_BTN_SX}
                    >
                      Actions
                    </Button>
                    <Menu
                      anchorEl={actionsAnchor}
                      open={Boolean(actionsAnchor)}
                      onClose={closeActions}
                      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
                      transformOrigin={{ vertical: "top", horizontal: "right" }}
                    >
                      <MenuItem
                        onClick={() => { closeActions(); share(); }}
                        disabled={!job?.public_slug}
                      >
                        <ListItemIcon><ShareOutlinedIcon fontSize="small" /></ListItemIcon>
                        Share
                      </MenuItem>
                      <MenuItem component={Link} href="/talent-pool" onClick={closeActions}>
                        <ListItemIcon><PeopleAltOutlinedIcon fontSize="small" /></ListItemIcon>
                        TP Matches
                      </MenuItem>
                      <MenuItem
                        onClick={() => { closeActions(); shareLinkedIn(); }}
                        disabled={!job?.public_slug}
                      >
                        <ListItemIcon><LinkedInIcon fontSize="small" /></ListItemIcon>
                        LinkedIn
                      </MenuItem>
                      <MenuItem
                        onClick={() => { closeActions(); exportCsv(); }}
                        disabled={!total}
                      >
                        <ListItemIcon><FileDownloadOutlinedIcon fontSize="small" /></ListItemIcon>
                        Export
                      </MenuItem>
                    </Menu>
                  </Box>
                </Stack>
              </Stack>

          </Stack>
        )}
        </Box>

        {/* Body */}
      {isError ? (
        <ErrorAlert error={error} />
      ) : appsLoading ? (
        <Stack direction="row" spacing={2.5} sx={{ overflowX: "auto", pb: 1 }}>
          {PIPELINE_COLUMNS.map((c) => (
            <Box key={c.stage} sx={{ width: { xs: 250, md: 300 }, flexShrink: 0 }}>
              <Skeleton variant="rounded" height={48} sx={{ mb: 1.5 }} />
              <Skeleton variant="rounded" height={120} sx={{ mb: 1.5 }} />
              <Skeleton variant="rounded" height={120} />
            </Box>
          ))}
        </Stack>
      ) : total === 0 ? (
        <Card sx={{ borderRadius: 2 }}>
          <EmptyState
            emoji="📥"
            title="No applications yet"
            description="Candidates who apply will move through the pipeline stages here."
          />
        </Card>
      ) : tab === "accepted" ? (
        <Box
          sx={{
            // Break the board out of the app-shell's main padding
            // (p: { xs:2, sm:3, md:4 }) so the columns span the full content
            // width — no white gutters on the sides — while the header above
            // keeps its normal contained width. Own beige bg so the full-width
            // strip stays consistent past the inset container's edges.
            bgcolor: "#F9F7F3",
            mx: { xs: -2, sm: -3, md: -4 },
            px: { xs: 1, sm: 1, md: 1 },
            py: { xs: 1.5, sm: 2 },
          }}
        >
          <PipelineBoard
            byStage={byStage}
            jobTitle={job?.title}
            onOpen={setSelectedId}
            onMove={handleMove}
          />
        </Box>
      ) : filteredRejected.length === 0 ? (
        <Card sx={{ borderRadius: 2 }}>
          <EmptyState
            emoji="✅"
            title="No rejected candidates"
            description="Candidates you reject from the pipeline will appear here."
          />
        </Card>
      ) : (
        <RejectedList
          apps={filteredRejected}
          jobTitle={job?.title}
          onOpen={setSelectedId}
          onReactivate={reactivate}
          busyId={pendingId}
        />
      )}

      <CandidateDialog
        appId={selectedId}
        open={Boolean(selectedId)}
        onClose={() => setSelectedId(null)}
      />

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={3500}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        {toast ? (
          <Alert severity={toast.severity} variant="filled" onClose={() => setToast(null)}>
            {toast.msg}
          </Alert>
        ) : undefined}
      </Snackbar>
      </Stack>
    </Box>
  );
};

export default PipelinePage;
