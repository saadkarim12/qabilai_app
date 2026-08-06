"use client";

/**
 * Public, anonymous candidate apply page (no auth).
 *
 *   GET  /public/apply/{slug}          → job details (title, JD, …)
 *   POST /public/apply/{slug}/upload   → submit CV + contact details
 *
 * Privacy by design: the upload endpoint returns an identical response for new,
 * duplicate and honeypot submissions, so we never tell a candidate "you already
 * applied" — we just confirm receipt. A device-local flag stops accidental
 * double-submits; the real one-application-per-candidate rule is enforced
 * server-side. Inputs are trimmed, length-capped and format-validated here;
 * injection safety itself lives in the backend's parameterized queries, so we
 * never blocklist characters (real names contain quotes, e.g. O'Brien).
 *
 * Next 16: `params` is a Promise — unwrap it with React's `use()`.
 */

import { use, useEffect, useMemo, useRef, useState } from "react";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Stack from "@mui/material/Stack";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import TextField from "@mui/material/TextField";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Skeleton from "@mui/material/Skeleton";
import IconButton from "@mui/material/IconButton";
import CircularProgress from "@mui/material/CircularProgress";
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import CloseIcon from "@mui/icons-material/Close";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutlined";
import WorkOutlineIcon from "@mui/icons-material/WorkOutlineOutlined";
import PlaceOutlinedIcon from "@mui/icons-material/PlaceOutlined";
import { usePublicJob, usePublicApply } from "@/lib/kabil/queries";
import { CV_ACCEPT, CV_MAX_MB, humanize } from "@/lib/kabil/constants";
import { countryLabel } from "@/lib/kabil/jobOptions";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE_PATTERN = /^\+?[0-9][0-9\s\-()]{6,19}$/;
const MAX_BYTES = CV_MAX_MB * 1024 * 1024;
const appliedKey = (slug) => `kabil:applied:${slug}`;

/** Trim + strip control chars; format/length validation does the rest. */
const clean = (v) => (v ?? "").replace(/\p{C}/gu, "").trim();
const fmtSize = (bytes) =>
  bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
const isPdf = (file) =>
  file.type === "application/pdf" || /\.pdf$/i.test(file.name);

const validateFile = (file) => {
  if (!file) return "Please attach your CV (PDF).";
  if (!isPdf(file)) return "Only PDF files are accepted.";
  if (file.size > MAX_BYTES) return `File is too large (max ${CV_MAX_MB} MB).`;
  if (file.size === 0) return "That file looks empty — please pick another.";
  return null;
};

/* ── CV dropzone ──────────────────────────────────────────────────────────── */

const CvDropzone = ({ file, error, disabled, onPick, onClear }) => {
  const inputRef = useRef(null);
  const [over, setOver] = useState(false);

  const handleDrop = (e) => {
    e.preventDefault();
    setOver(false);
    if (disabled) return;
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) onPick(dropped);
  };

  if (file) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          p: 1.5,
          borderRadius: 2,
          border: "1px solid",
          borderColor: error ? "error.main" : "#cdd6d1",
          bgcolor: "#f7faf8",
        }}
      >
        <DescriptionOutlinedIcon color="primary" />
        <Box sx={{ minWidth: 0, flexGrow: 1 }}>
          <Typography variant="body2" noWrap title={file.name} sx={{ fontWeight: 600 }}>
            {file.name}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {fmtSize(file.size)}
          </Typography>
        </Box>
        {!disabled && (
          <IconButton size="small" onClick={onClear} aria-label="Remove file">
            <CloseIcon fontSize="small" />
          </IconButton>
        )}
      </Box>
    );
  }

  return (
    <Box
      role="button"
      tabIndex={0}
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(e) => {
        if (!disabled && (e.key === "Enter" || e.key === " ")) inputRef.current?.click();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={handleDrop}
      sx={{
        cursor: disabled ? "not-allowed" : "pointer",
        textAlign: "center",
        px: 2,
        py: 4,
        borderRadius: 2,
        border: "2px dashed",
        borderColor: error ? "error.main" : over ? "primary.main" : "#cdd6d1",
        bgcolor: over ? "rgba(31,157,87,0.06)" : "#fafbfb",
        transition: "border-color .15s ease, background-color .15s ease",
        opacity: disabled ? 0.6 : 1,
      }}
    >
      <CloudUploadOutlinedIcon sx={{ fontSize: 36, color: "primary.main", mb: 1 }} />
      <Typography variant="body2" sx={{ fontWeight: 600 }}>
        Drag &amp; drop your CV here
      </Typography>
      <Typography variant="caption" color="text.secondary">
        or click to browse · PDF only · max {CV_MAX_MB} MB
      </Typography>
      <input
        ref={inputRef}
        type="file"
        accept={CV_ACCEPT}
        hidden
        onChange={(e) => {
          const picked = e.target.files?.[0];
          if (picked) onPick(picked);
          e.target.value = ""; // allow re-picking the same file
        }}
      />
    </Box>
  );
};

/* ── Job details panel ────────────────────────────────────────────────────── */

const Detail = ({ icon, children }) => (
  <Stack direction="row" spacing={0.75} sx={{ color: "text.secondary", alignItems: "center" }}>
    {icon}
    <Typography variant="body2">{children}</Typography>
  </Stack>
);

const salaryText = (job) => {
  const { min_salary, max_salary, currency } = job;
  if (min_salary == null && max_salary == null) return null;
  const fmt = (n) => n?.toLocaleString();
  if (min_salary != null && max_salary != null)
    return `${currency} ${fmt(min_salary)} – ${fmt(max_salary)}`;
  return `${currency} ${fmt(min_salary ?? max_salary)}`;
};

const JobSummary = ({ job }) => {
  const pay = salaryText(job);
  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          {job.title}
        </Typography>
        <Typography color="text.secondary" sx={{ mt: 0.5 }}>
          {job.hiring_company}
        </Typography>
      </Box>

      <Stack direction="row" sx={{ flexWrap: "wrap", rowGap: 1, columnGap: 2.5 }}>
        <Detail icon={<PlaceOutlinedIcon sx={{ fontSize: 18 }} />}>
          {[job.city, countryLabel(job.country)].filter(Boolean).join(", ")}
        </Detail>
        <Detail icon={<WorkOutlineIcon sx={{ fontSize: 18 }} />}>
          {humanize(job.employment_type)} · {humanize(job.work_mode)}
        </Detail>
        {job.min_experience_years > 0 && (
          <Detail>{job.min_experience_years}+ yrs experience</Detail>
        )}
        {pay && <Detail>{pay}</Detail>}
      </Stack>

      {!!job.required_skills?.length && (
        <Box>
          <Typography variant="overline" color="text.secondary">
            Required skills
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.5 }}>
            {job.required_skills.map((s) => (
              <Chip key={s} size="small" label={s} color="primary" variant="outlined" />
            ))}
          </Box>
        </Box>
      )}

      {!!job.preferred_skills?.length && (
        <Box>
          <Typography variant="overline" color="text.secondary">
            Nice to have
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.5 }}>
            {job.preferred_skills.map((s) => (
              <Chip key={s} size="small" label={s} variant="outlined" />
            ))}
          </Box>
        </Box>
      )}

      {job.job_description && (
        <Box>
          <Typography variant="overline" color="text.secondary">
            About the role
          </Typography>
          <Typography variant="body2" sx={{ mt: 0.5, whiteSpace: "pre-line", lineHeight: 1.7 }}>
            {job.job_description}
          </Typography>
        </Box>
      )}
    </Stack>
  );
};

/* ── Page ─────────────────────────────────────────────────────────────────── */

const Shell = ({ children }) => (
  <Box sx={{ minHeight: "100dvh", bgcolor: "#f2f5f3", py: { xs: 3, md: 6 }, px: 2 }}>
    <Box sx={{ maxWidth: 720, mx: "auto" }}>{children}</Box>
  </Box>
);

const ApplyPage = ({ params }) => {
  const { slug } = use(params);
  const { data: job, isLoading, isError, error } = usePublicJob(slug);
  const apply = usePublicApply(slug);

  const [form, setForm] = useState({ fullName: "", email: "", phone: "" });
  const [file, setFile] = useState(null);
  const [consent, setConsent] = useState(false);
  const [honeypot, setHoneypot] = useState("");
  const [errors, setErrors] = useState({});
  const [reference, setReference] = useState(null);
  const [alreadyApplied, setAlreadyApplied] = useState(false);

  // Best-effort device-local guard against accidental re-submission.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      // Post-mount read keeps SSR/first render consistent (no hydration mismatch).
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (window.localStorage.getItem(appliedKey(slug))) setAlreadyApplied(true);
    } catch {
      /* private mode / storage disabled — server still enforces uniqueness */
    }
  }, [slug]);

  const setField = (key) => (e) => {
    setForm((f) => ({ ...f, [key]: e.target.value }));
    setErrors((prev) => (prev[key] ? { ...prev, [key]: undefined } : prev));
  };

  const pickFile = (f) => {
    setFile(f);
    setErrors((prev) => ({ ...prev, file: validateFile(f) || undefined }));
  };

  const validate = () => {
    const name = clean(form.fullName);
    const email = clean(form.email);
    const phone = clean(form.phone);
    const next = {};
    if (name.length < 2) next.fullName = "Please enter your full name.";
    else if (name.length > 100) next.fullName = "Name is too long.";
    if (!EMAIL_PATTERN.test(email) || email.length > 254)
      next.email = "Enter a valid email address.";
    if (!PHONE_PATTERN.test(phone)) next.phone = "Enter a valid phone number.";
    next.file = validateFile(file) || undefined;
    if (!consent) next.consent = "Please confirm consent to continue.";
    setErrors(next);
    return Object.values(next).every((v) => !v);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    apply.reset();
    if (!validate()) return;
    apply.mutate(
      {
        pdf: file,
        full_name: clean(form.fullName),
        email: clean(form.email),
        phone: clean(form.phone),
        consent: true,
        honeypot,
      },
      {
        onSuccess: (res) => {
          setReference(res?.reference_number || null);
          try {
            window.localStorage.setItem(appliedKey(slug), "1");
          } catch {
            /* ignore storage failures */
          }
        },
      },
    );
  };

  const submitError = useMemo(() => {
    const err = apply.error;
    if (!err) return null;
    if (err.status === 410) return "This posting is no longer accepting applications.";
    if (err.status === 400)
      return err.body?.message || "Please check your details and try again.";
    if (err.status === 0) return "Network error — check your connection and try again.";
    return err.body?.message || "Something went wrong. Please try again.";
  }, [apply.error]);

  /* ── Loading / unavailable / success states ── */

  if (isLoading) {
    return (
      <Shell>
        <Card sx={{ borderRadius: 3 }}>
          <CardContent sx={{ p: { xs: 2.5, md: 4 } }}>
            <Skeleton variant="text" width="60%" height={40} />
            <Skeleton variant="text" width="40%" />
            <Skeleton variant="rounded" height={120} sx={{ mt: 2 }} />
          </CardContent>
        </Card>
      </Shell>
    );
  }

  if (isError || !job) {
    const gone = error?.status === 410;
    return (
      <Shell>
        <Card sx={{ borderRadius: 3 }}>
          <CardContent sx={{ p: { xs: 3, md: 5 }, textAlign: "center" }}>
            <Typography variant="h5" gutterBottom sx={{ fontWeight: 700 }}>
              {gone ? "Applications closed" : "Posting unavailable"}
            </Typography>
            <Typography color="text.secondary">
              {gone
                ? "This role is no longer accepting applications."
                : "We couldn't find this job posting. The link may be incorrect or expired."}
            </Typography>
          </CardContent>
        </Card>
      </Shell>
    );
  }

  if (reference || alreadyApplied) {
    return (
      <Shell>
        <Card sx={{ borderRadius: 3 }}>
          <CardContent sx={{ p: { xs: 3, md: 5 }, textAlign: "center" }}>
            <CheckCircleOutlineIcon sx={{ fontSize: 56, color: "success.main", mb: 1 }} />
            <Typography variant="h5" gutterBottom sx={{ fontWeight: 700 }}>
              {reference ? "Application received" : "You've already applied"}
            </Typography>
            <Typography color="text.secondary">
              {reference
                ? `Thanks for applying to ${job.title} at ${job.hiring_company}. We'll be in touch if there's a match.`
                : `Our records show an application for ${job.title} from this device. You only need to apply once.`}
            </Typography>
            {reference && (
              <Box
                sx={{
                  mt: 2.5,
                  display: "inline-block",
                  px: 2,
                  py: 1,
                  borderRadius: 2,
                  bgcolor: "#f2f5f3",
                  border: "1px solid #e0e6e2",
                }}
              >
                <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                  Reference number
                </Typography>
                <Typography variant="body2" sx={{ fontFamily: "var(--font-mono)", fontWeight: 700 }}>
                  {reference}
                </Typography>
              </Box>
            )}
          </CardContent>
        </Card>
      </Shell>
    );
  }

  /* ── Apply form ── */

  const busy = apply.isPending;

  return (
    <Shell>
      <Card sx={{ borderRadius: 3 }}>
        <CardContent sx={{ p: { xs: 2.5, md: 4 } }}>
          <JobSummary job={job} />

          <Divider sx={{ my: 3 }} />

          <Typography variant="h6" gutterBottom sx={{ fontWeight: 700 }}>
            Apply for this role
          </Typography>

          {submitError && (
            <Alert severity="error" sx={{ mb: 2 }}>
              <AlertTitle>Couldn&apos;t submit</AlertTitle>
              {submitError}
            </Alert>
          )}

          <Box component="form" onSubmit={handleSubmit} noValidate>
            <Stack spacing={2}>
              <TextField
                label="Full name"
                value={form.fullName}
                onChange={setField("fullName")}
                error={!!errors.fullName}
                helperText={errors.fullName}
                disabled={busy}
                required
                fullWidth
                inputProps={{ maxLength: 100 }}
                autoComplete="name"
              />
              <TextField
                label="Email"
                type="email"
                value={form.email}
                onChange={setField("email")}
                error={!!errors.email}
                helperText={errors.email}
                disabled={busy}
                required
                fullWidth
                inputProps={{ maxLength: 254 }}
                autoComplete="email"
              />
              <TextField
                label="Phone"
                value={form.phone}
                onChange={setField("phone")}
                error={!!errors.phone}
                helperText={errors.phone || "Include country code, e.g. +971 50 123 4567"}
                disabled={busy}
                required
                fullWidth
                inputProps={{ maxLength: 20, inputMode: "tel" }}
                autoComplete="tel"
              />

              <Box>
                <CvDropzone
                  file={file}
                  error={!!errors.file}
                  disabled={busy}
                  onPick={pickFile}
                  onClear={() => {
                    setFile(null);
                    setErrors((prev) => ({ ...prev, file: undefined }));
                  }}
                />
                {errors.file && (
                  <Typography variant="caption" color="error" sx={{ mt: 0.5, ml: 0.5, display: "block" }}>
                    {errors.file}
                  </Typography>
                )}
              </Box>

              {/* Honeypot — hidden from real users; bots that fill it are dropped server-side. */}
              <Box aria-hidden sx={{ position: "absolute", left: "-9999px", width: 1, height: 1, overflow: "hidden" }}>
                <input
                  type="text"
                  tabIndex={-1}
                  autoComplete="off"
                  value={honeypot}
                  onChange={(e) => setHoneypot(e.target.value)}
                />
              </Box>

              <Box>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={consent}
                      onChange={(e) => {
                        setConsent(e.target.checked);
                        setErrors((prev) => ({ ...prev, consent: undefined }));
                      }}
                      disabled={busy}
                    />
                  }
                  label={
                    <Typography variant="body2">
                      I consent to my data being processed for recruitment purposes.
                    </Typography>
                  }
                />
                {errors.consent && (
                  <Typography variant="caption" color="error" sx={{ display: "block", ml: 1.75 }}>
                    {errors.consent}
                  </Typography>
                )}
              </Box>

              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={busy}
                startIcon={busy ? <CircularProgress size={18} color="inherit" /> : null}
                sx={{ alignSelf: { xs: "stretch", sm: "flex-start" }, px: 4 }}
              >
                {busy ? "Submitting…" : "Submit application"}
              </Button>
            </Stack>
          </Box>
        </CardContent>
      </Card>
    </Shell>
  );
};

export default ApplyPage;
