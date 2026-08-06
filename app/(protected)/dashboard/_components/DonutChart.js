"use client";

import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

/**
 * Dependency-free SVG donut chart. Renders the ring with each slice's
 * percentage label sat just *outside* the band (radially, angled to its slice)
 * plus a legend on the right. Slices with a zero value are dropped from the ring
 * but kept in the legend so the category list stays stable across selections.
 *
 * `segments`: `[{ label, value, color }]`. Pure presentational — the caller
 * shapes the data (job-health counts, pipeline buckets, …).
 */
// Coordinate space. Labels sit outside the ring, so the canvas has to hold the
// ring (outer radius = RADIUS + THICKNESS / 2) plus a label ring beyond it and a
// text margin so the "%" never clips; the <svg> renders it down to DISPLAY px.
const THICKNESS = 26;
const RADIUS = 82;
// Centerline the labels ride, just past the ring's outer edge.
const LABEL_RADIUS = RADIUS + THICKNESS / 2 + 20;
// Room around a label point for its text (widest is "100%").
const TEXT_MARGIN = 44;
const CANVAS = 2 * (LABEL_RADIUS + TEXT_MARGIN);
const DISPLAY = 290;
const CENTER = CANVAS / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
// White separator between adjacent slices, in user-space units along the arc.
const GAP = 3;
// Slices thinner than this fraction of the ring are left unlabelled so tiny
// slivers don't collide with their neighbours' labels.
const MIN_LABEL_FRACTION = 0.06;
// All external labels read in the brand's dark green, per the design.
const LABEL_TEXT = "#1C4A3E";

/** Polar (radians, 0 = top, clockwise) → SVG cartesian point. */
const polarToXY = (angle, radius) => ({
  x: CENTER + radius * Math.cos(angle - Math.PI / 2),
  y: CENTER + radius * Math.sin(angle - Math.PI / 2),
});

/** Anchor a label so its text grows outward (away from the ring), never over it. */
const labelAnchor = (x) => {
  const dx = x - CENTER;
  if (Math.abs(dx) < LABEL_RADIUS * 0.35) return "middle"; // top / bottom slices
  return dx > 0 ? "start" : "end"; // right / left slices
};

const DonutChart = ({ segments }) => {
  const total = segments.reduce((sum, s) => sum + s.value, 0);

  // Build the drawable arcs (value > 0) with cumulative offsets + label points.
  let acc = 0;
  const arcs = total
    ? segments
        .filter((s) => s.value > 0)
        .map((s) => {
          const fraction = s.value / total;
          const startFraction = acc;
          acc += fraction;
          const dash = Math.max(0, fraction * CIRCUMFERENCE - GAP);
          const midAngle = (startFraction + fraction / 2) * 2 * Math.PI;
          return {
            ...s,
            dash,
            offset: -startFraction * CIRCUMFERENCE,
            pct: Math.round(fraction * 100),
            showLabel: fraction >= MIN_LABEL_FRACTION,
            label_point: polarToXY(midAngle, LABEL_RADIUS),
          };
        })
    : [];

  return (
    <Box
      sx={{
        position: "relative",
        display: "flex",
        flexDirection: { xs: "column", sm: "row" },
        alignItems: "center",
        justifyContent: "center",
        gap: { xs: 2, sm: 0 },
        width: "100%",
        height: "100%",
        py: 1,
      }}
    >
      {/* Donut centered in the card, nudged ~20px left of dead-center. */}
      <Box sx={{ position: "relative", flexShrink: 0, transform: { sm: "translateX(-40px)" } }}>
        <svg width={DISPLAY} height={DISPLAY} viewBox={`0 0 ${CANVAS} ${CANVAS}`} role="img">
          {total === 0 ? (
            <circle
              cx={CENTER}
              cy={CENTER}
              r={RADIUS}
              fill="none"
              stroke="rgba(19,64,45,0.08)"
              strokeWidth={THICKNESS}
            />
          ) : (
            <g transform={`rotate(-90 ${CENTER} ${CENTER})`}>
              {arcs.map((a) => (
                <circle
                  key={a.label}
                  cx={CENTER}
                  cy={CENTER}
                  r={RADIUS}
                  fill="none"
                  stroke={a.color}
                  strokeWidth={THICKNESS}
                  strokeDasharray={`${a.dash} ${CIRCUMFERENCE - a.dash}`}
                  strokeDashoffset={a.offset}
                />
              ))}
            </g>
          )}
          {arcs
            .filter((a) => a.showLabel)
            .map((a) => (
              <text
                key={`${a.label}-pct`}
                x={a.label_point.x}
                y={a.label_point.y}
                textAnchor={labelAnchor(a.label_point.x)}
                dominantBaseline="central"
                style={{ fontWeight: 700, fontSize: 20, fill: LABEL_TEXT }}
              >
                {a.pct}%
              </text>
            ))}
        </svg>
        {total === 0 && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              position: "absolute",
              inset: 0,
              display: "grid",
              placeItems: "center",
              textAlign: "center",
            }}
          >
            No data
          </Typography>
        )}
      </Box>

      {/* Legend floats at the card's bottom-right (absolute on desktop so it
          doesn't push the donut off-center); on mobile it stacks under the
          donut. Health segments carry a day-range note ("(<18 days)"); when
          present it replaces the raw count, otherwise the count is shown
          (per-job bucket view). */}
      <Stack
        spacing={1.25}
        sx={{
          minWidth: 120,
          position: { xs: "static", sm: "absolute" },
          right: { sm: 20 },
          bottom: { sm: 20 },
        }}
      >
        {segments.map((s) => (
          <Stack key={s.label} direction="row" spacing={1} sx={{ alignItems: "center" }}>
            <Box
              sx={{ width: 11, height: 11, borderRadius: "50%", bgcolor: s.color, flexShrink: 0 }}
            />
            <Typography
              sx={{
                fontFamily: "var(--font-sans), Inter, system-ui, sans-serif",
                fontWeight: 400,
                fontSize: "12px",
                lineHeight: "18px",
                letterSpacing: 0,
                textAlign: "center",
                color: "#2C2C2A",
              }}
            >
              {s.note ? `${s.label} ${s.note}` : s.label}
              {!s.note && (
                <Box component="span" sx={{ ml: 0.75, fontWeight: 700 }}>
                  {s.value}
                </Box>
              )}
            </Typography>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
};

export default DonutChart;
