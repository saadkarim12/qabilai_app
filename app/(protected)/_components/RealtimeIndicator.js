"use client";

import Tooltip from "@mui/material/Tooltip";
import Box from "@mui/material/Box";
import { useRealtime } from "@/lib/realtime/RealtimeContext";
import { STREAM_STATUS } from "@/lib/api/events";

/**
 * A small live-connection dot for the TopBar. Green (pulsing) when the realtime
 * SSE stream is delivering events; amber while connecting/reconnecting (the UI
 * is on the polling fallback during this window). Renders nothing when realtime
 * is disabled — there's no live state worth signalling.
 */
const RealtimeIndicator = () => {
  const { status } = useRealtime();
  if (status === STREAM_STATUS.DISABLED) return null;

  const open = status === STREAM_STATUS.OPEN;
  const color = open ? "#0F6E56" : " #EF9F27";
  const label = open ? "Live — updates in real time" : "Reconnecting…";

  return (
    <Tooltip title={label} arrow>
      <Box
        role="status"
        aria-label={label}
        sx={{
          width: 9,
          height: 9,
          borderRadius: "50%",
          bgcolor: color,
          boxShadow: `0 0 0 0 ${color}`,
          animation: open ? "kabilPulse 2s infinite" : "none",
          "@keyframes kabilPulse": {
            "0%": { boxShadow: `0 0 0 0 ${color}66` },
            "70%": { boxShadow: `0 0 0 6px ${color}00` },
            "100%": { boxShadow: `0 0 0 0 ${color}00` },
          },
        }}
      />
    </Tooltip>
  );
};

export default RealtimeIndicator;
