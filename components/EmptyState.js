/** Centered empty-state block used across the console pages. */
"use client";

import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

const EmptyState = ({ emoji = "📭", title, description, action }) => (
  <Box
    sx={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      textAlign: "center",
      py: 8,
      px: 2,
    }}
  >
    <Box sx={{ fontSize: 44, mb: 1.5 }}>{emoji}</Box>
    <Typography variant="h6" fontWeight={700} gutterBottom>
      {title}
    </Typography>
    {description && (
      <Typography color="text.secondary" mb={action ? 3 : 0}>
        {description}
      </Typography>
    )}
    {action}
  </Box>
);

export default EmptyState;
