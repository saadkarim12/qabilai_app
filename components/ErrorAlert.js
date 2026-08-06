/** Renders a KabilApiError (or any Error) as an MUI Alert with the correlation id. */
"use client";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";

const ErrorAlert = ({ error, sx }) => {
  if (!error) return null;
  const body = error.body || {};
  const message = body.message || error.message || "Something went wrong";
  const correlationId = error.correlationId || body.correlation_id;

  return (
    <Alert severity="error" sx={sx}>
      <AlertTitle>{message}</AlertTitle>
      {correlationId ? `ref: ${correlationId}` : null}
    </Alert>
  );
};

export default ErrorAlert;
