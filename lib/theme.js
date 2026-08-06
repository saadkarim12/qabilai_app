/** Shared MUI theme + layout constants. Brand: deep forest green + gold accent. */
"use client";

import { createTheme } from "@mui/material/styles";

/** Layout / brand constants reused across the protected shell. */
export const SIDEBAR_WIDTH = 220;
// Brand: green #0F6E56 (primary) + orange #EF9F27 (accent). `gold` is kept as
// the accent-color key name for backwards-compat with existing call sites, but
// now resolves to the brand orange.
export const COLORS = {
  sidebarBg: "#11352a",
  sidebarBgHover: "#1a4636",
  gold: "#EF9F27",
  goldSoft: "#f2b34e",
  // Light sidebar shell (cream surface, forest-green active pill).
  sidebarLight: "#f6f7f2",
  sidebarBorder: "#e6e8e1",
  sidebarActive: "#0F6E56",
  sidebarText: "#3c463f",
  sidebarMuted: "#8a948b",
  sidebarIcon: "#0F6E56",
};

export const theme = createTheme({
  cssVariables: true,
  palette: {
    mode: "light",
    primary: { main: "#0F6E56", dark: "#0a4f3d", contrastText: "#ffffff" },
    secondary: { main: COLORS.gold, contrastText: "#11352a" },
    background: { default: "#faf8f3", paper: "#ffffff" },
    text: { primary: "#1c2522", secondary: "#647067" },
  },
  shape: { borderRadius: 10 },
  typography: {
    fontFamily: "var(--font-sans), system-ui, Arial, sans-serif",
    // Slightly smaller, denser baseline (MUI default is 14).
    fontSize: 13,
    h4: { fontWeight: 700, fontSize: "1.6rem" },
    h5: { fontWeight: 700, fontSize: "1.3rem" },
    h6: { fontWeight: 600, fontSize: "1.05rem" },
  },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { textTransform: "none", fontWeight: 600 } },
    },
    MuiCard: {
      styleOverrides: {
        root: { border: "1px solid #e7eae8", boxShadow: "none" },
      },
    },
    MuiToggleButton: {
      styleOverrides: {
        root: ({ theme }) => ({
          textTransform: "none",
          fontWeight: 600,
          "&.Mui-selected": {
            backgroundColor: theme.palette.primary.main,
            color: theme.palette.primary.contrastText,
            "&:hover": { backgroundColor: theme.palette.primary.dark },
          },
        }),
      },
    },
  },
});
