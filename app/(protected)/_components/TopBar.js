"use client";

import { usePathname } from "next/navigation";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Avatar from "@mui/material/Avatar";
import IconButton from "@mui/material/IconButton";
import MenuIcon from "@mui/icons-material/Menu";
import { COLORS } from "@/lib/theme";

// Dummy workspace/company shown in the top bar (the product is a single shared
// workspace, so there's no per-user org name to read). Swap for real workspace
// metadata once the API exposes it.
const COMPANY = { name: "Digycorp Pvt Ltd", initials: "DP" };

// Warm beige used for the notification button and the company pill.
const BEIGE = "#f1ece1";

/** Long-form date shown under the page title, e.g. "Thursday, 25 May 2026". */
const todayLabel = () =>
  new Date().toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });

const humanize = (seg = "") =>
  seg.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/** Friendly labels for known route segments. */
const SEGMENT_LABELS = {
  dashboard: "Dashboard",
  jobs: "Jobs",
  new: "New Job",
  pipeline: "Pipeline",
  "cv-inbox": "CV Inbox",
  "talent-pool": "Talent Pool",
};

/** Derive the page title from the last segment of the current route. */
const pageTitle = (pathname) => {
  const segments = pathname.split("/").filter(Boolean);
  if (!segments.length) return "";
  const last = segments[segments.length - 1];
  return SEGMENT_LABELS[last] || (segments[segments.length - 2] === "jobs" ? "Job" : humanize(last));
};

const TopBar = ({ onMenuClick }) => {
  const pathname = usePathname();
  const title = pageTitle(pathname);

  return (
    <Box
      component="header"
      sx={{
        bgcolor: "rgba(244, 240, 232, 0.3)",
        borderBottom: `1px solid ${COLORS.sidebarBorder}`,
        px: { xs: 2, md: 4 },
        py: 1.5,
        display: "flex",
        alignItems: "center",
        gap: { xs: 1.5, md: 3 },
      }}
    >
      {/* Menu toggle — opens the sidebar drawer on small screens */}
      <IconButton
        aria-label="Open navigation menu"
        onClick={onMenuClick}
        sx={{
          display: { xs: "inline-flex", md: "none" },
          color: "primary.main",
          ml: -1,
        }}
      >
        <MenuIcon />
      </IconButton>

      {/* Title + date */}
      <Box sx={{ minWidth: 0 }}>
        <Typography
          noWrap
          sx={{
            fontFamily: "var(--font-jakarta), system-ui, sans-serif",
            fontWeight: 700,
            fontSize: "18px",
            lineHeight: "22.5px",
            letterSpacing: 0,
            color: "#0F6E56",
          }}
        >
          {title}
        </Typography>
        <Typography noWrap sx={{ color: "#6B7280", fontSize: "12px" }}>
          {todayLabel()}
        </Typography>
      </Box>

      <Stack direction="row" spacing={1.25} sx={{ alignItems: "center", ml: "auto" }}>
        {/* Company pill */}
        <Stack
          direction="row"
          spacing={1}
          sx={{
            alignItems: "center",
            bgcolor: BEIGE,
            borderRadius: "5px",
            pl: 0.5,
            pr: 1.5,
            py: 0.5,
          }}
        >
          <Avatar
            sx={{ width: 30, height: 30, bgcolor: "secondary.main", color: "#fff", fontSize: 12, fontWeight: 700 }}
          >
            {COMPANY.initials}
          </Avatar>
          <Typography
            noWrap
            sx={{ fontWeight: 600, fontSize: "0.85rem", display: { xs: "none", sm: "block" } }}
          >
            {COMPANY.name}
          </Typography>
        </Stack>
      </Stack>
    </Box>
  );
};

export default TopBar;
