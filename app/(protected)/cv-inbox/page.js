"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import TextField from "@mui/material/TextField";
import MenuItem from "@mui/material/MenuItem";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Skeleton from "@mui/material/Skeleton";
import CircularProgress from "@mui/material/CircularProgress";
import AddIcon from "@mui/icons-material/Add";
import ArrowRightAltIcon from "@mui/icons-material/ArrowRightAlt";
import ErrorAlert from "@/components/ErrorAlert";
import UploadDropzone from "./_components/UploadDropzone";
import { useJobs, useBulkUpload } from "@/lib/kabil/queries";
import {
  CV_SOURCES,
  CV_INBOX_STEPS,
  CV_INBOX_HINT,
  BULK_MAX_FILES,
  CV_MAX_MB,
  BULK_REJECTION_LABELS,
  PDPL_CONSENT_TEXT,
} from "@/lib/kabil/constants";

const isPdf = (file) =>
  file.type === "application/pdf" || /\.pdf$/i.test(file.name);

/** Flat beige-filled look for the Source / Match-against-job selects. */
const BEIGE_FIELD_SX = {
  "& .MuiOutlinedInput-root": {
    bgcolor: "#efe9dc",
    borderRadius: "5px",
    fontSize: "0.8rem",
    "& fieldset": { borderColor: "transparent" },
    "&:hover fieldset": { borderColor: "transparent" },
    "&.Mui-focused fieldset": { borderColor: "primary.main" },
  },
  "& .MuiFormHelperText-root": { fontSize: "0.7rem" },
};

/** A numbered circle + label pill for one stage in the flow banner. */
const StepBadge = ({ step }) => (
  <Stack direction="row" spacing={0.75} sx={{ alignItems: "center" }}>
    <Box
      sx={{
        width: 24,
        height: 24,
        flexShrink: 0,
        borderRadius: "50%",
        display: "grid",
        placeItems: "center",
        fontSize: "0.72rem",
        fontWeight: 700,
        color: "#fff",
        bgcolor: step.color,
      }}
    >
      {step.n}
    </Box>
    <Box
      sx={{
        px: 1.5,
        py: 0.6,
        borderRadius: "5px",
        fontSize: "0.72rem",
        fontWeight: 700,
        color: "#fff",
        bgcolor: step.color,
      }}
    >
      {step.label}
    </Box>
  </Stack>
);

const FlowBanner = () => (
  <Box
    sx={{
      bgcolor: "#E8F5F1",
      border: "1px solid",
      borderColor: "#0F6E56",
      borderRadius: "5px",
      px: 2.5,
      py: 2,
    }}
  >
    <Stack
      direction="row"
      spacing={1.25}
      sx={{ alignItems: "center", justifyContent: "center", flexWrap: "wrap", rowGap: 1.25 }}
    >
      {CV_INBOX_STEPS.map((step, i) => (
        <Fragment key={step.n}>
          <StepBadge step={step} />
          {i < CV_INBOX_STEPS.length - 1 && (
            <ArrowRightAltIcon sx={{ color: "text.disabled" }} />
          )}
        </Fragment>
      ))}
    </Stack>
    <Typography
      sx={{
        mt: 1.5,
        textAlign: "center",
        fontFamily: "var(--font-jakarta), system-ui, sans-serif",
        fontWeight: 400,
        fontSize: "14px",
        lineHeight: "21px",
        letterSpacing: 0,
        color: "#0F6E56",
      }}
    >
      {CV_INBOX_HINT}
    </Typography>
  </Box>
);

const FieldLabel = ({ children, required }) => (
  <Typography sx={{ mb: 0.75, fontWeight: 600, fontSize: "0.78rem" }}>
    {children}
    {required && (
      <Box component="span" sx={{ color: "error.main" }}>
        {" "}
        *
      </Box>
    )}
  </Typography>
);

/** Loading placeholder mirroring the upload card layout. */
const CvInboxSkeleton = () => (
  <Card>
    <CardContent sx={{ p: { xs: 2.5, sm: 3 } }}>
      <Skeleton variant="text" width={120} height={28} sx={{ mb: 2 }} />
      <Skeleton variant="rounded" height={150} sx={{ borderRadius: 2, mb: 3 }} />
      <Box
        sx={{
          display: "grid",
          gap: 3,
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          mb: 3,
        }}
      >
        <Box>
          <Skeleton variant="text" width={80} sx={{ mb: 0.75 }} />
          <Skeleton variant="rounded" height={40} />
        </Box>
        <Box>
          <Skeleton variant="text" width={120} sx={{ mb: 0.75 }} />
          <Skeleton variant="rounded" height={40} />
        </Box>
      </Box>
      <Skeleton variant="rounded" height={56} sx={{ borderRadius: 1.5, mb: 3 }} />
      <Skeleton variant="rounded" height={48} sx={{ borderRadius: 1.5 }} />
    </CardContent>
  </Card>
);

const ResultSummary = ({ result, jobId, onReset }) => {
  const accepted = result.accepted_count ?? result.applications?.length ?? 0;
  const already = result.already_applied ?? [];
  const rejected = result.rejected ?? [];

  return (
    <Alert
      severity={accepted > 0 ? "success" : "warning"}
      action={
        <Button color="inherit" size="small" onClick={onReset}>
          Upload more
        </Button>
      }
    >
      <AlertTitle>
        {accepted > 0
          ? `${accepted} CV${accepted === 1 ? "" : "s"} queued for parsing`
          : "No new CVs were added"}
      </AlertTitle>
      <Stack spacing={0.5}>
        {accepted > 0 && (
          <Typography variant="body2">
            They&apos;ll appear in the job&apos;s Applied column once parsed.{" "}
            <Box
              component={Link}
              href={`/jobs/${jobId}/pipeline`}
              sx={{ fontWeight: 700, color: "inherit" }}
            >
              Open pipeline →
            </Box>
          </Typography>
        )}
        {already.length > 0 && (
          <Typography variant="body2">
            {already.length} candidate{already.length === 1 ? "" : "s"} had already
            applied to this job.
          </Typography>
        )}
        {rejected.length > 0 && (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {rejected.length} file{rejected.length === 1 ? "" : "s"} skipped:
            </Typography>
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {rejected.map((r) => (
                <Typography key={`${r.filename}`} component="li" variant="body2">
                  {r.filename} — {BULK_REJECTION_LABELS[r.reason] ?? r.reason}
                </Typography>
              ))}
            </Box>
          </Box>
        )}
      </Stack>
    </Alert>
  );
};

const CvInboxPage = () => {
  const { data, isLoading, isError, error } = useJobs({
    status: "open",
    pageSize: 100,
  });
  const jobs = data?.items ?? [];

  const [files, setFiles] = useState([]);
  const [localRejects, setLocalRejects] = useState([]);
  const [source, setSource] = useState(CV_SOURCES[0].value);
  const [jobId, setJobId] = useState("");
  const [consent, setConsent] = useState(false);
  const [result, setResult] = useState(null);

  const upload = useBulkUpload(jobId);

  const addFiles = (incoming) => {
    const rejects = [];
    const next = [...files];
    for (const f of incoming) {
      if (!isPdf(f)) {
        rejects.push({ filename: f.name, reason: "not_pdf" });
      } else if (f.size > CV_MAX_MB * 1024 * 1024) {
        rejects.push({ filename: f.name, reason: "too_large" });
      } else if (next.some((x) => x.name === f.name && x.size === f.size)) {
        // silent dedupe
      } else if (next.length >= BULK_MAX_FILES) {
        rejects.push({ filename: f.name, reason: "over_limit" });
      } else {
        next.push(f);
      }
    }
    setFiles(next);
    setLocalRejects(rejects);
  };

  const removeFile = (i) => setFiles((prev) => prev.filter((_, idx) => idx !== i));
  const clearFiles = () => {
    setFiles([]);
    setLocalRejects([]);
  };

  const canUpload =
    !!jobId && files.length > 0 && consent && !upload.isPending;

  const handleUpload = () => {
    setResult(null);
    upload.mutate(files, {
      onSuccess: (res) => {
        setResult(res);
        setFiles([]);
        setLocalRejects([]);
        setConsent(false);
      },
    });
  };

  return (
    <Stack spacing={2.5} sx={{ maxWidth: 960, mx: "auto" }}>
      <Typography
        sx={{
          fontFamily: "var(--font-jakarta), system-ui, sans-serif",
          fontWeight: 700,
          fontSize: "24px",
          lineHeight: "36px",
          letterSpacing: 0,
          color: "#2C2C2A",
        }}
      >
        Applicant Pool
      </Typography>

      <FlowBanner />

      {isError ? (
        <ErrorAlert error={error} />
      ) : isLoading ? (
        <CvInboxSkeleton />
      ) : (
        <Card>
          <CardContent sx={{ p: { xs: 2.5, sm: 3 } }}>
            <Typography sx={{ fontWeight: 700, mb: 2, fontSize: "0.95rem" }}>
              Upload CVs
            </Typography>

            <Stack spacing={3}>
              <UploadDropzone
                files={files}
                onAdd={addFiles}
                onRemove={removeFile}
                onClear={clearFiles}
                disabled={upload.isPending}
              />

              {localRejects.length > 0 && (
                <Alert severity="warning" onClose={() => setLocalRejects([])}>
                  <AlertTitle>
                    {localRejects.length} file
                    {localRejects.length === 1 ? "" : "s"} can&apos;t be uploaded
                  </AlertTitle>
                  <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                    {localRejects.map((r, i) => (
                      <Typography key={`${r.filename}-${i}`} component="li" variant="body2">
                        {r.filename} — {BULK_REJECTION_LABELS[r.reason] ?? r.reason}
                      </Typography>
                    ))}
                  </Box>
                </Alert>
              )}

              <Box
                sx={{
                  display: "grid",
                  gap: 3,
                  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                }}
              >
                <Box>
                  <FieldLabel required>Source</FieldLabel>
                  <TextField
                    select
                    fullWidth
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                    disabled={upload.isPending}
                    sx={BEIGE_FIELD_SX}
                  >
                    {CV_SOURCES.map((s) => (
                      <MenuItem key={s.value} value={s.value}>
                        {s.emoji} {s.label}
                      </MenuItem>
                    ))}
                  </TextField>
                </Box>

                <Box>
                  <FieldLabel required>Match against job</FieldLabel>
                  <TextField
                    select
                    fullWidth
                    value={jobId}
                    onChange={(e) => setJobId(e.target.value)}
                    disabled={upload.isPending || jobs.length === 0}
                    sx={BEIGE_FIELD_SX}
                    helperText={
                      jobs.length === 0 ? (
                        <Box component="span">
                          No open jobs.{" "}
                          <Box component={Link} href="/jobs" sx={{ fontWeight: 700 }}>
                            Activate a job first
                          </Box>
                        </Box>
                      ) : (
                        " "
                      )
                    }
                    slotProps={{ select: { displayEmpty: true } }}
                  >
                    <MenuItem value="" disabled>
                      Select a job…
                    </MenuItem>
                    {jobs.map((job) => (
                      <MenuItem key={job.id} value={job.id}>
                        {job.title} — {job.hiring_company}
                      </MenuItem>
                    ))}
                  </TextField>
                </Box>
              </Box>

              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "#0F6E56",
                  bgcolor: "#E8F5F1",
                  borderRadius: "5px",
                  px: 1.5,
                  py: 0.5,
                }}
              >
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={consent}
                      onChange={(e) => setConsent(e.target.checked)}
                      disabled={upload.isPending}
                    />
                  }
                  label={
                    <Typography sx={{ fontSize: "0.78rem" }}>{PDPL_CONSENT_TEXT}</Typography>
                  }
                  sx={{ alignItems: "flex-start", m: 0, py: 0.5 }}
                />
              </Box>

              {upload.isError && <ErrorAlert error={upload.error} />}
              {result && (
                <ResultSummary
                  result={result}
                  jobId={jobId}
                  onReset={() => setResult(null)}
                />
              )}

              <Box sx={{ textAlign: "center" }}>
                <Button
                  variant="contained"
                  size="large"
                  disabled={!canUpload}
                  onClick={handleUpload}
                  startIcon={
                    upload.isPending ? (
                      <CircularProgress size={18} color="inherit" />
                    ) : (
                      <AddIcon />
                    )
                  }
                  sx={{ px: 5, borderRadius: "5px", fontSize: "0.8rem" }}
                >
                  {upload.isPending ? "Uploading…" : "Upload CVs"}
                </Button>
              </Box>
            </Stack>
          </CardContent>
        </Card>
      )}
    </Stack>
  );
};

export default CvInboxPage;
