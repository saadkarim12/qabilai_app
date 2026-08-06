"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Avatar from "@mui/material/Avatar";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import Drawer from "@mui/material/Drawer";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import LogoutOutlinedIcon from "@mui/icons-material/LogoutOutlined";
import { SIDEBAR_WIDTH, COLORS } from "@/lib/theme";
import { useAuth } from "@/lib/auth/AuthContext";
import { NAV_ITEMS, COMING_SOON_ITEMS } from "./navItems";

const initials = (name = "") =>
  name
    .split(" ")
    .map((p) => p[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();

/** The sidebar body — shared by the permanent (desktop) aside and the mobile
 *  drawer. `onNavigate` (drawer only) closes the drawer after a nav tap. */
const SidebarInner = ({ onNavigate }) => {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();

  const onLogout = async () => {
    onNavigate?.();
    await logout();
    router.replace("/login");
  };

  return (
    <Box
      sx={{
        height: "100%",
        fontFamily: "var(--font-jakarta), system-ui, sans-serif",
        fontSize: 14,
        "& .MuiTypography-root, & .MuiListItemText-primary": {
          fontFamily: "var(--font-jakarta), system-ui, sans-serif",
        },
        bgcolor: "rgba(244, 240, 232, 0.3)",
        color: COLORS.sidebarText,
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Brand */}
      <Box sx={{ px: 2.5, py: 2.5 }}>
        <Box
          component="img"
          src="/Qabil_logo.svg"
          alt="Qabil"
          sx={{ height: 40, width: "auto", display: "block" }}
        />
      </Box>

      {/* Primary nav */}
      <List sx={{ px: 1.5, py: 1, flex: 1, overflowY: "auto" }}>
        {NAV_ITEMS.map(({ label, href, icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <ListItemButton
              key={href}
              component={Link}
              href={href}
              selected={active}
              onClick={onNavigate}
              sx={{
                borderRadius: "5px",
                mb: 0.5,
                color: COLORS.sidebarText,
                "& .MuiListItemIcon-root": {
                  color: COLORS.sidebarIcon,
                  minWidth: 38,
                },
                "&.Mui-selected, &.Mui-selected:hover": {
                  bgcolor: COLORS.sidebarActive,
                  color: "#fff",
                  // Recolor the (black-stroked) SVG to white when active.
                  "& .MuiListItemIcon-root img": {
                    filter: "brightness(0) invert(1)",
                  },
                },
                "&:hover": { bgcolor: "rgba(47,125,91,0.08)" },
              }}
            >
              <ListItemIcon>
                <Box
                  component="img"
                  src={icon}
                  alt=""
                  aria-hidden
                  sx={{ width: 20, height: 20, objectFit: "contain", display: "block" }}
                />
              </ListItemIcon>
              <ListItemText
                primary={label}
                slotProps={{ primary: { sx: { fontSize: 14, fontWeight: active ? 700 : 500 } } }}
              />
            </ListItemButton>
          );
        })}
      </List>

      {/* Coming soon */}
      <Box
        sx={{
          mx: 1.5,
          mb: 1.5,
          px: 1.75,
          py: 1.5,
          borderRadius: 2,
          border: `1px dashed ${COLORS.sidebarBorder}`,
          bgcolor: "#F6F4EC",
        }}
      >
        <Typography
          variant="caption"
          sx={{
            display: "block",
            mb: 0.75,
            letterSpacing: 1,
            color: COLORS.sidebarMuted,
            fontWeight: 700,
          }}
        >
          COMING SOON
        </Typography>
        {COMING_SOON_ITEMS.map(({ label }) => (
          <Stack
            key={label}
            direction="row"
            sx={{ alignItems: "center", justifyContent: "space-between", py: 0.6 }}
          >
            <Typography sx={{ fontSize: 14, color: COLORS.sidebarText }}>
              {label}
            </Typography>
            <Chip
              label="SOON"
              size="small"
              sx={{
                height: 20,
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: 0.5,
                color: COLORS.gold,
                bgcolor: "rgba(201,162,63,0.12)",
                "& .MuiChip-label": { px: 0.75 },
              }}
            />
          </Stack>
        ))}
      </Box>

      {/* Footer: settings + user */}
      <Box sx={{ borderTop: `1px solid ${COLORS.sidebarBorder}`, px: 1.5, py: 1.5 }}>
        <ListItemButton
          disabled
          sx={{
            borderRadius: 2,
            mb: 0.5,
            "& .MuiListItemIcon-root": { color: COLORS.sidebarMuted, minWidth: 38 },
          }}
        >
          <ListItemIcon>
            <SettingsOutlinedIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText
            primary="Settings"
            slotProps={{ primary: { sx: { fontSize: 14, color: COLORS.sidebarText } } }}
          />
        </ListItemButton>

        <Stack direction="row" spacing={1.5} sx={{ px: 1, py: 0.5, alignItems: "center" }}>
          <Avatar sx={{ width: 32, height: 32, bgcolor: COLORS.sidebarActive, color: "#fff", fontSize: 13, fontWeight: 700 }}>
            {initials(user?.full_name) || "?"}
          </Avatar>
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography variant="body2" noWrap sx={{ fontWeight: 600, color: COLORS.sidebarText }}>
              {user?.full_name || "—"}
            </Typography>
            <Typography variant="caption" sx={{ color: COLORS.sidebarMuted, textTransform: "capitalize" }} noWrap>
              {user?.role || ""}
            </Typography>
          </Box>
          <Tooltip title="Log out">
            <IconButton size="small" onClick={onLogout} sx={{ color: COLORS.sidebarMuted }}>
              <LogoutOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      </Box>
    </Box>
  );
};

/**
 * App-shell sidebar. On md+ it's a permanent sticky column; below md it collapses
 * into a temporary drawer toggled from the TopBar menu button (`mobileOpen` /
 * `onClose` are owned by the layout).
 */
const Sidebar = ({ mobileOpen = false, onClose }) => (
  <>
    {/* Permanent sidebar (md and up) */}
    <Box
      component="aside"
      sx={{
        display: { xs: "none", md: "flex" },
        flexDirection: "column",
        width: SIDEBAR_WIDTH,
        maxWidth: 220,
        flexShrink: 0,
        height: "100dvh",
        position: "sticky",
        top: 0,
        borderRight: `1px solid ${COLORS.sidebarBorder}`,
      }}
    >
      <SidebarInner />
    </Box>

    {/* Mobile drawer (below md) */}
    <Drawer
      variant="temporary"
      open={mobileOpen}
      onClose={onClose}
      ModalProps={{ keepMounted: true }}
      sx={{
        display: { xs: "block", md: "none" },
        "& .MuiDrawer-paper": {
          width: SIDEBAR_WIDTH,
          maxWidth: 220,
          boxSizing: "border-box",
          border: 0,
        },
      }}
    >
      <SidebarInner onNavigate={onClose} />
    </Drawer>
  </>
);

export default Sidebar;
