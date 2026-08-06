"use client";

import { useState } from "react";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";
import Skeleton from "@mui/material/Skeleton";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import BusinessCenterOutlinedIcon from "@mui/icons-material/BusinessCenterOutlined";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutlined";
import Inventory2OutlinedIcon from "@mui/icons-material/Inventory2Outlined";
import FilterAltIcon from "@mui/icons-material/FilterAlt";
import ErrorAlert from "@/components/ErrorAlert";
import { useAuth } from "@/lib/auth/AuthContext";
import { useDashboard, usePerformance } from "@/lib/kabil/queries";
import PerformanceTable from "./_components/PerformanceTable";
import CandidatePipelinePanel from "./_components/CandidatePipelinePanel";
import UpcomingInterviewsCard from "./_components/UpcomingInterviewsCard";
import PendingFeedbackCard from "./_components/PendingFeedbackCard";

/** Brand accent (orange) shared by every Overview stat card. */
const ACCENT = "#EF9F27";
const ACCENT_SOFT = "rgba(239,159,39,0.14)";

/**
 * The four Overview cards, all job-focused. `jobs.by_status` is zero-filled by
 * the backend across every JobStatus value (draft/open/inactive/archived/closed),
 * so we read keys directly. "Closed Jobs" folds the legacy `closed` state into
 * the modern `archived` (both mean ended); "Paused / Canceled" is `inactive`
 * (temporarily paused, reactivatable). Every headline number is the brand orange
 * with a green title, per the dashboard design.
 */
const STAT_CARDS = [
  {
    label: "Total Jobs",
    icon: BusinessCenterOutlinedIcon,
    value: (d) => d.jobs.total,
    sub: () => "All job postings",
  },
  {
    label: "Open Jobs",
    icon: CheckCircleOutlineIcon,
    value: (d) => d.jobs.by_status.open,
    sub: () => "Live & accepting",
  },
  {
    label: "Closed Jobs",
    icon: Inventory2OutlinedIcon,
    value: (d) => d.jobs.by_status.closed + d.jobs.by_status.archived,
    sub: () => "Ended or archived",
  },
  {
    label: "Paused / Canceled",
    icon: FilterAltIcon,
    value: (d) => d.jobs.by_status.inactive,
    sub: () => "Temporarily paused",
  },
];

// Visual-only time-range pills. The dashboard endpoints aggregate the whole
// workspace and don't (yet) accept a range param, so this drives no query — it
// mirrors the design and reserves the control for when the API gains a window.
const TIME_RANGES = [
  { value: "month", label: "This Month" },
  { value: "quarter", label: "Quarter" },
  { value: "8months", label: "8 Months" },
  { value: "year", label: "Year" },
];

const grid = {
  display: "grid",
  gap: 1,
  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr", lg: "repeat(4, 1fr)" },
};

// Shared height (desktop) for the Performance and Candidate Pipeline cards so
// they always line up; the Performance table scrolls internally past this. On
// mobile the cards stack and size to their content.
const PANEL_HEIGHT = 360;

/** Greeting that tracks the viewer's local time of day. */
const partOfDay = (h) => (h < 12 ? "Good Morning" : h < 18 ? "Good Afternoon" : "Good Evening");

const StatCard = ({ label, icon: Icon, value, sub, loading }) => (
  <Card
    sx={{
      position: "relative",
      maxWidth: 274,
      borderRadius: "8px",
      boxShadow: "0px 4px 2px 0px #0000001A",
      overflow: "hidden",
      transition: "transform .2s ease, box-shadow .2s ease, border-color .2s ease",
      "&::before": {
        content: '""',
        position: "absolute",
        insetInline: 0,
        top: 0,
        height: 3,
        background: `linear-gradient(90deg, ${ACCENT} 0%, ${ACCENT}55 100%)`,
        opacity: 0,
        transition: "opacity .2s ease",
      },
      "&:hover": {
        transform: "translateY(-4px)",
        borderColor: "transparent",
        boxShadow: "0 12px 28px -10px rgba(15,110,86,.28)",
      },
      "&:hover::before": { opacity: 1 },
    }}
  >
    <CardContent sx={{ p: 2 }}>
      <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between" }}>
        <Box
          sx={{
            flexShrink: 0,
            width: 38,
            height: 38,
            borderRadius: 1.75,
            display: "grid",
            placeItems: "center",
            bgcolor: ACCENT_SOFT,
            color: ACCENT,
          }}
        >
          <Icon fontSize="small" />
        </Box>
        {loading ? (
          <Skeleton variant="text" width={40} sx={{ fontSize: "1.5rem" }} />
        ) : (
          <Typography
            sx={{
              fontFamily: "var(--font-sans), system-ui, Arial, sans-serif",
              fontWeight: 700,
              fontSize: "40px",
              lineHeight: "30px",
              letterSpacing: 0,
              color: ACCENT,
            }}
          >
            {value}
          </Typography>
        )}
      </Stack>
      <Typography
        sx={{
          mt: 1,
          fontFamily: "var(--font-jakarta), system-ui, sans-serif",
          fontWeight: 600,
          fontSize: "18px",
          lineHeight: "21.6px",
          letterSpacing: 0,
          color: "primary.main",
        }}
      >
        {label}
      </Typography>
      {loading ? (
        <Skeleton variant="text" width={90} />
      ) : (
        <Typography
          noWrap
          sx={{
            fontFamily: "var(--font-jakarta), system-ui, sans-serif",
            fontWeight: 400,
            fontSize: "14px",
            lineHeight: "18px",
            letterSpacing: 0,
            color: "#000000CC",
          }}
        >
          {sub}
        </Typography>
      )}
    </CardContent>
  </Card>
);

const DashboardPage = () => {
  const { user } = useAuth();
  const { data, isLoading, isError, error } = useDashboard();
  const performance = usePerformance();
  const [range, setRange] = useState("month");
  const [perfView, setPerfView] = useState("chart"); // "chart" | "table"

  const firstName = user?.full_name?.trim().split(/\s+/)[0] || "there";

  return (
    <Stack spacing={2.5}>
      {/* Greeting + time-range */}
      <Stack
        direction="row"
        sx={{ justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}
      >
        <Box>
          <Typography
            sx={{
              fontFamily: "var(--font-jakarta), system-ui, sans-serif",
              fontWeight: 500,
              fontSize: "20px",
              lineHeight: "32px",
              letterSpacing: 0,
              color: "#1C4A3E",
            }}
          >
            {partOfDay(new Date().getHours())} {firstName}!
          </Typography>
          <Typography
            sx={{
              fontFamily: "var(--font-jakarta), system-ui, sans-serif",
              fontWeight: 400,
              fontSize: "12px",
              lineHeight: "16px",
              letterSpacing: 0,
              color: "#6B7280",
            }}
          >
            Digycorp Recruitment — performance overview
          </Typography>
        </Box>
        <ToggleButtonGroup
          exclusive
          size="small"
          value={range}
          onChange={(_, v) => v && setRange(v)}
          sx={{
            bgcolor: "#F4F0E84D",
            borderRadius: "5px",
            p: 0.5,
            gap: 0.5,
            "& .MuiToggleButton-root": {
              px: 1.75,
              py: 0.6,
              border: 0,
              borderRadius: "5px !important",
              fontSize: "0.75rem",
              fontWeight: 600,
              color: "text.secondary",
              "&.Mui-selected, &.Mui-selected:hover": {
                bgcolor: "#0F6E56",
                color: "#fff",
              },
            },
          }}
        >
          {TIME_RANGES.map((r) => (
            <ToggleButton key={r.value} value={r.value}>
              {r.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Stack>

      {isError && <ErrorAlert error={error} />}
      {performance.isError && <ErrorAlert error={performance.error} />}

      {/* Overview */}
      <Box>
        <Typography
          sx={{
            fontFamily: "var(--font-jakarta), system-ui, sans-serif",
            fontWeight: 700,
            fontSize: "22px",
            lineHeight: "32px",
            letterSpacing: 0,
            color: "#1C4A3E",
            mb: 1.5,
          }}
        >
          Overview
        </Typography>
        <Box sx={grid}>
          {STAT_CARDS.map(({ label, icon, value, sub }) => (
            <StatCard
              key={label}
              label={label}
              icon={icon}
              loading={isLoading}
              value={data ? value(data) : "—"}
              sub={data ? sub(data) : ""}
            />
          ))}
        </Box>
      </Box>

      {/* Performance section header + table / pipeline. The header is a grid that
          mirrors the content below, so "View Pie Chart" right-aligns to the end
          of the table column rather than the far edge of the whole row. */}
      <Box>
        <Box
          sx={{
            display: "grid",
            gap: 2,
            gridTemplateColumns: { xs: "1fr", md: "3fr 2fr" },
            mb: 1.5,
          }}
        >
          <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center" }}>
            <Typography
              sx={{
                fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                fontWeight: 700,
                fontSize: "22px",
                lineHeight: "32px",
                letterSpacing: 0,
                color: "#1C4A3E",
              }}
            >
              Performance
            </Typography>
            <Button
              variant="text"
              size="small"
              onClick={() => setPerfView((v) => (v === "chart" ? "table" : "chart"))}
              sx={{ fontWeight: 700, color: "primary.main", fontSize: "0.8rem" }}
            >
              {perfView === "chart" ? "View Table" : "View Pie Chart"}
            </Button>
          </Stack>
        </Box>
        <Box
          sx={{
            display: "grid",
            gap: 2,
            gridTemplateColumns: { xs: "1fr", md: "3fr 2fr" },
            alignItems: "stretch",
          }}
        >
          <Box sx={{ height: { xs: "auto", md: PANEL_HEIGHT } }}>
            <PerformanceTable data={performance.data} loading={performance.isLoading} view={perfView} />
          </Box>
          <Box sx={{ height: { xs: "auto", md: PANEL_HEIGHT } }}>
            <CandidatePipelinePanel />
          </Box>
        </Box>
      </Box>

      {/* Upcoming interviews + Pending feedback */}
      <Box
        sx={{
          display: "grid",
          gap: 2,
          gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
          alignItems: "stretch",
        }}
      >
        <UpcomingInterviewsCard />
        <PendingFeedbackCard />
      </Box>
    </Stack>
  );
};

export default DashboardPage;
