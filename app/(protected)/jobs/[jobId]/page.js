"use client";

import { use } from "react";
import Link from "next/link";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import Chip from "@mui/material/Chip";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import LockOpenOutlinedIcon from "@mui/icons-material/LockOpenOutlined";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import EmptyState from "@/components/EmptyState";
import ErrorAlert from "@/components/ErrorAlert";
import { useJob, useUpdateJobStatus } from "@/lib/kabil/queries";
import { humanize, jobStatusColor } from "@/lib/kabil/constants";
import { countryLabel } from "@/lib/kabil/jobOptions";

const formatDateTime = (iso) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

const formatSalary = (min, max, currency) => {
  const fmt = (n) => `${currency} ${Number(n).toLocaleString()}`;
  if (min == null && max == null) return "Not specified";
  if (min != null && max != null) return `${fmt(min)} – ${fmt(max)} / month`;
  return `${min != null ? `From ${fmt(min)}` : `Up to ${fmt(max)}`} / month`;
};

/** One label/value pair inside a detail card. */
const DetailRow = ({ label, children }) => (
  <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "200px 1fr" }, gap: { xs: 0.25, sm: 2 }, py: 1 }}>
    <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600 }}>
      {label}
    </Typography>
    <Box>{typeof children === "string" || typeof children === "number" ? (
      <Typography variant="body2">{children}</Typography>
    ) : (
      children
    )}</Box>
  </Box>
);

const Section = ({ title, children, action }) => (
  <Card sx={{ borderRadius: 2 }}>
    <CardContent sx={{ p: { xs: 2.5, sm: 3 } }}>
      <Stack
        direction="row"
        sx={{ alignItems: "center", justifyContent: "space-between", mb: 1.5 }}
      >
        <Typography variant="h6" sx={{ fontWeight: 700 }}>
          {title}
        </Typography>
        {action}
      </Stack>
      <Divider sx={{ mb: 1.5 }} />
      {children}
    </CardContent>
  </Card>
);

const ChipList = ({ items, color = "default", empty = "None" }) =>
  items && items.length ? (
    <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
      {items.map((it) => (
        <Chip
          key={it}
          label={it}
          size="small"
          color={color}
          variant="outlined"
          sx={{ px: 0.75 }}
        />
      ))}
    </Stack>
  ) : (
    <Typography variant="body2" color="text.secondary">
      {empty}
    </Typography>
  );

const ViewJobPage = ({ params }) => {
  const { jobId } = use(params);
  const { data: job, isLoading, isError, error } = useJob(jobId);
  const updateStatus = useUpdateJobStatus(jobId);

  if (isLoading) {
    return (
      <Stack spacing={2.5}>
        <Skeleton variant="text" width={120} />
        <Skeleton variant="rounded" height={88} />
        <Skeleton variant="rounded" height={240} />
        <Skeleton variant="rounded" height={180} />
      </Stack>
    );
  }

  if (isError) {
    return (
      <Stack spacing={2.5}>
        <BackLink />
        <ErrorAlert error={error} />
      </Stack>
    );
  }

  if (!job) {
    return (
      <Stack spacing={2.5}>
        <BackLink />
        <Card sx={{ borderRadius: 2 }}>
          <EmptyState emoji="🔍" title="Job not found" description="This job may have been removed." />
        </Card>
      </Stack>
    );
  }

  const isOpen = job.status === "open";
  // Active jobs archive; anything else (draft/inactive/archived/closed) activates.
  const toggleStatus = () => updateStatus.mutate(isOpen ? "archived" : "open");
  const publicUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/apply/${job.public_slug}`
      : `/apply/${job.public_slug}`;

  return (
    <Stack spacing={2.5}>
      <BackLink />

      {/* Header */}
      <Card sx={{ borderRadius: 2 }}>
        <CardContent sx={{ p: { xs: 2.5, sm: 3 } }}>
          <Stack
            direction={{ xs: "column", md: "row" }}
            spacing={2}
            sx={{
              justifyContent: "space-between",
              alignItems: { xs: "flex-start", md: "center" },
            }}
          >
            <Box>
              <Stack
                direction="row"
                spacing={1.5}
                sx={{ alignItems: "center", flexWrap: "wrap" }}
              >
                <Typography variant="h5" sx={{ fontWeight: 700 }}>
                  {job.title}
                </Typography>
                <Chip
                  size="small"
                  label={job.status === "open" ? "Active" : humanize(job.status)}
                  color={jobStatusColor(job.status)}
                  sx={{ fontWeight: 600 }}
                />
              </Stack>
              <Typography color="text.secondary" sx={{ mt: 0.5 }}>
                {job.hiring_company} · {job.city}, {countryLabel(job.country)}
              </Typography>
            </Box>

            <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
              <Button
                component={Link}
                href={`/jobs/${job.id}/pipeline`}
                variant="contained"
                size="small"
                startIcon={<AccountTreeOutlinedIcon />}
              >
                View Pipeline
              </Button>
              <Tooltip title={isOpen ? "Archive this job" : "Activate this job for applications"}>
                <span>
                  <Button
                    variant="outlined"
                    size="small"
                    color={isOpen ? "error" : "success"}
                    startIcon={isOpen ? <LockOutlinedIcon /> : <LockOpenOutlinedIcon />}
                    onClick={toggleStatus}
                    disabled={updateStatus.isPending}
                  >
                    {isOpen ? "Archive Job" : "Activate Job"}
                  </Button>
                </span>
              </Tooltip>
            </Stack>
          </Stack>
          {updateStatus.isError && <ErrorAlert error={updateStatus.error} sx={{ mt: 2 }} />}
        </CardContent>
      </Card>

      {/* Overview */}
      <Section title="Overview">
        <DetailRow label="Hiring company">{job.hiring_company}</DetailRow>
        <DetailRow label="Location">{`${job.city}, ${countryLabel(job.country)}`}</DetailRow>
        <DetailRow label="Employment type">{humanize(job.employment_type)}</DetailRow>
        <DetailRow label="Work mode">{humanize(job.work_mode)}</DetailRow>
        <DetailRow label="Min experience">{`${job.min_experience_years} year(s)`}</DetailRow>
        <DetailRow label="Salary">{formatSalary(job.min_salary, job.max_salary, job.currency)}</DetailRow>
        <DetailRow label="Notice period">
          {job.notice_period ? humanize(job.notice_period) : "Any"}
        </DetailRow>
        <DetailRow label="Visa requirement">
          {job.visa_requirement ? humanize(job.visa_requirement) : "Any"}
        </DetailRow>
        <DetailRow label="Nationality preference">
          <ChipList items={job.nationality_preference} empty="Any nationality" />
        </DetailRow>
        <DetailRow label="Languages required">
          <ChipList items={job.languages_required} empty="None specified" />
        </DetailRow>
      </Section>

      {/* Skills */}
      <Section title="Skills">
        <DetailRow label="Required (hard filter)">
          <ChipList items={job.required_skills} color="primary" empty="None" />
        </DetailRow>
        <DetailRow label="Preferred">
          <ChipList items={job.preferred_skills} empty="None" />
        </DetailRow>
      </Section>

      {/* Job description (collapsible) */}
      <Accordion
        defaultExpanded
        disableGutters
        sx={{
          borderRadius: 2,
          border: "1px solid #e7eae8",
          boxShadow: "none",
          "&:before": { display: "none" },
          "&.Mui-expanded": { margin: 0 },
        }}
      >
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: { xs: 2.5, sm: 3 } }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            Job description
          </Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: { xs: 2.5, sm: 3 }, pt: 0 }}>
          <Divider sx={{ mb: 1.5 }} />
          <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>
            {job.job_description || "No description provided."}
          </Typography>
        </AccordionDetails>
      </Accordion>

      {/* Pipeline / setup status */}
      <Section title="Setup status">
        <DetailRow label="Ready for applications">
          <Chip
            size="small"
            label={job.ready_for_applications ? "Ready" : "Preparing…"}
            color={job.ready_for_applications ? "success" : "warning"}
          />
        </DetailRow>
        {Object.keys(job.pipeline_status || {}).length > 0 && (
          <DetailRow label="Pipeline steps">
            <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
              {Object.entries(job.pipeline_status).map(([step, state]) => (
                <Chip
                  key={step}
                  size="small"
                  variant="outlined"
                  label={`${humanize(step)}: ${state}`}
                  color={state === "ok" ? "success" : state === "failed" ? "error" : "default"}
                  sx={{ px: 0.75 }}
                />
              ))}
            </Stack>
          </DetailRow>
        )}
      </Section>

      {/* Metadata */}
      <Section title="Details">
        <DetailRow label="Public apply link">
          <Button
            href={publicUrl}
            target="_blank"
            rel="noopener noreferrer"
            size="small"
            endIcon={<OpenInNewIcon />}
            sx={{ textTransform: "none" }}
          >
            /apply/{job.public_slug}
          </Button>
        </DetailRow>
        <DetailRow label="Created">{formatDateTime(job.created_at)}</DetailRow>
        <DetailRow label="Last updated">{formatDateTime(job.updated_at)}</DetailRow>
        {job.closed_at && <DetailRow label="Closed">{formatDateTime(job.closed_at)}</DetailRow>}
        <DetailRow label="Job ID">
          <Typography variant="body2" sx={{ fontFamily: "monospace", color: "text.secondary" }}>
            {job.id}
          </Typography>
        </DetailRow>
      </Section>

      {/* WhatsApp screening questions (kept at the bottom) */}
      <Section title={`WhatsApp screening questions (${job.whatsapp_questions?.length || 0})`}>
        {job.whatsapp_questions?.length ? (
          <Stack spacing={1.5} divider={<Divider flexItem />}>
            {job.whatsapp_questions
              .slice()
              .sort((a, b) => a.order - b.order)
              .map((q) => (
                <Box key={q.id}>
                  <Stack
                    direction="row"
                    spacing={1}
                    sx={{ alignItems: "center", flexWrap: "wrap", mb: 0.5 }}
                  >
                    <Chip size="small" label={`#${q.order}`} />
                    <Chip size="small" variant="outlined" label={humanize(q.category)} />
                    {q.is_ai_generated && (
                      <Chip size="small" color="secondary" label="✨ AI" sx={{ fontWeight: 600 }} />
                    )}
                  </Stack>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {q.question_en}
                  </Typography>
                  {q.question_ar && (
                    <Typography variant="body2" color="text.secondary" dir="rtl">
                      {q.question_ar}
                    </Typography>
                  )}
                  {q.reasoning && (
                    <Typography variant="caption" color="text.secondary">
                      Why: {q.reasoning}
                    </Typography>
                  )}
                </Box>
              ))}
          </Stack>
        ) : (
          <Typography variant="body2" color="text.secondary">
            No screening questions yet. They’re generated when the job is opened.
          </Typography>
        )}
      </Section>
    </Stack>
  );
};

const BackLink = () => (
  <Button
    component={Link}
    href="/jobs"
    startIcon={<ArrowBackIcon />}
    color="inherit"
    sx={{ alignSelf: "flex-start", textTransform: "none" }}
  >
    Back to Jobs
  </Button>
);

export default ViewJobPage;
