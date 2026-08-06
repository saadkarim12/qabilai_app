"use client";

import { useState } from "react";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { PIPELINE_COLUMNS, NEXT_STAGE, humanize } from "@/lib/kabil/constants";
import CandidateCard from "./CandidateCard";

/** Stage a card may be dropped on (single forward step — backend rejects jumps). */
const validTargetFor = (app) => (app ? NEXT_STAGE[app.stage] : null);

const StageColumn = ({ column, apps, jobTitle, onOpen, dragApp, onDragStart, onDragEnd, onDrop }) => {
  const [over, setOver] = useState(false);
  const target = validTargetFor(dragApp);
  const isValidTarget = Boolean(dragApp) && target === column.stage;
  const dimmed = Boolean(dragApp) && !isValidTarget && dragApp.stage !== column.stage;

  return (
    <Box
      onDragOver={(e) => {
        if (!isValidTarget) return;
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (isValidTarget) onDrop(dragApp, column.stage);
      }}
      sx={{
        // Columns flex to share the row's width so all stages fit on screen at
        // once (no horizontal scroll) on desktop. Below lg they hold a 215px
        // minimum and the board scrolls instead of squashing cards too narrow.
        flexBasis: 0,
        flexGrow: 1,
        minWidth: { xs: 215, lg: 0 },
        flexShrink: { xs: 0, lg: 1 },
        display: "flex",
        flexDirection: "column",
        opacity: dimmed ? 0.5 : 1,
        transition: "opacity .15s ease",
      }}
    >
      <Box sx={{ px: 0.5, mb: 1.5 }}>
        <Stack
          direction="row"
          sx={{ alignItems: "center", justifyContent: "space-between", mb: 1 }}
        >
          <Typography
            sx={{
              fontFamily: "var(--font-jakarta), system-ui, sans-serif",
              fontWeight: 600,
              fontSize: "10.71px",
              lineHeight: "16.07px",
              letterSpacing: 0,
              color: "#1C4A3E",
            }}
          >
            {column.label}
          </Typography>
          {/* Final "Shortlist" column gets a dark-green badge; earlier stages orange. */}
          <Box
            sx={{
              minWidth: 24,
              height: 24,
              px: 0.75,
              borderRadius: 999,
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontFamily: "var(--font-jakarta), system-ui, sans-serif",
              fontSize: "9.06px",
              lineHeight: "13.6px",
              letterSpacing: 0,
              fontWeight: 700,
              color: "#fff",
              bgcolor: column.stage === "done" ? "#1C4A3E" : "#EF9F27",
            }}
          >
            {String(apps.length).padStart(2, "0")}
          </Box>
        </Stack>
        <Box
          sx={{
            height: 3,
            borderRadius: 2,
            background:
              "linear-gradient(90deg, #EF9F27 0%, rgba(239,159,39,0.35) 100%)",
          }}
        />
      </Box>
      <Stack
        spacing={1.5}
        sx={{
          flexGrow: 1,
          borderRadius: 2,
          p: isValidTarget ? 1 : 0,
          border: isValidTarget ? "2px dashed" : "2px dashed transparent",
          borderColor: over ? column.accent : isValidTarget ? "#cdd6d1" : "transparent",
          bgcolor: over ? "rgba(31,157,87,0.06)" : "transparent",
          transition: "background-color .15s ease, border-color .15s ease",
        }}
      >
        {isValidTarget && (
          <Typography
            variant="caption"
            sx={{ color: column.accent, fontWeight: 700, textAlign: "center", pt: 0.5 }}
          >
            Drop to move to {humanize(column.label)}
          </Typography>
        )}
        {apps.length ? (
          apps.map((app) => (
            <CandidateCard
              key={app.id}
              app={app}
              jobTitle={jobTitle}
              onOpen={onOpen}
              draggable
              dragging={dragApp?.id === app.id}
              onDragStart={onDragStart}
              onDragEnd={onDragEnd}
            />
          ))
        ) : (
          !isValidTarget && (
            <Box
              sx={{
                border: "1px dashed #d6dbd8",
                borderRadius: 2,
                py: 4,
                textAlign: "center",
                color: "text.disabled",
              }}
            >
              <Typography variant="caption">No candidates</Typography>
            </Box>
          )
        )}
      </Stack>
    </Box>
  );
};

/**
 * Kanban board for non-rejected applicants. Cards can be dragged a single stage
 * forward (the only transition the backend accepts); valid drop targets light
 * up while a card is in flight.
 */
const PipelineBoard = ({ byStage, jobTitle, onOpen, onMove }) => {
  const [dragApp, setDragApp] = useState(null);

  const handleDragStart = (e, app) => {
    setDragApp(app);
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <Stack
      direction="row"
      spacing={1.5}
      sx={{ overflowX: { xs: "auto", lg: "visible" }, pb: 1, alignItems: "flex-start" }}
    >
      {PIPELINE_COLUMNS.map((column) => (
        <StageColumn
          key={column.stage}
          column={column}
          apps={byStage[column.stage]}
          jobTitle={jobTitle}
          onOpen={onOpen}
          dragApp={dragApp}
          onDragStart={handleDragStart}
          onDragEnd={() => setDragApp(null)}
          onDrop={(app, stage) => {
            setDragApp(null);
            onMove(app, stage);
          }}
        />
      ))}
    </Stack>
  );
};

export default PipelineBoard;
