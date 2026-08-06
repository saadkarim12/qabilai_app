"use client";

import { useFormContext } from "react-hook-form";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import Divider from "@mui/material/Divider";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutlineOutlined";
import { humanize } from "@/lib/kabil/constants";
import {
  countryLabel,
  NOTICE_PERIOD_OPTIONS,
  VISA_OPTIONS,
  NATIONALITY_OPTIONS,
} from "@/lib/kabil/jobOptions";

const optionLabel = (options, value) =>
  options.find((o) => o.value === value)?.label;

const Row = ({ label, value }) => (
  <>
    <Stack
      direction="row"
      spacing={2}
      sx={{ justifyContent: "space-between", alignItems: "baseline", py: 1.75 }}
    >
      <Typography variant="body2" color="text.secondary" sx={{ flexShrink: 0 }}>
        {label}
      </Typography>
      <Typography sx={{ fontWeight: 600, textAlign: "right" }}>{value}</Typography>
    </Stack>
    <Divider sx={{ borderColor: "divider" }} />
  </>
);

const StepReview = ({ questions = [] }) => {
  const { getValues } = useFormContext();
  const v = getValues();

  const skills = (v.required_skills || []).join(", ") || "—";
  const preferredSkills = (v.preferred_skills || []).join(", ") || "—";
  const languages = (v.languages_required || []).join(", ") || "—";
  const minExp =
    v.min_experience_years === 1 ? "1 year" : `${v.min_experience_years || 0} years`;

  const hasSalary = v.min_salary !== "" || v.max_salary !== "";
  const salary = hasSalary
    ? `${v.currency} ${v.min_salary || 0}–${v.max_salary || 0} / month`
    : "—";

  return (
    <Card>
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
        <Typography variant="h6" sx={{ fontWeight: 700, mb: 2.5 }}>
          Review &amp; Publish
        </Typography>

        {/* Ready banner */}
        <Box
          sx={{
            borderRadius: 2,
            bgcolor: "rgba(47,125,91,0.1)",
            border: "1px solid rgba(47,125,91,0.25)",
            p: 2,
            mb: 3,
            display: "flex",
            gap: 1.5,
          }}
        >
          <CheckCircleOutlineIcon sx={{ color: "primary.main", mt: 0.25 }} />
          <Box>
            <Typography sx={{ fontWeight: 700 }}>Your job is ready to publish</Typography>
            <Typography variant="body2" color="text.secondary">
              Once published, candidates can apply and AI screening will begin
              automatically.
            </Typography>
          </Box>
        </Box>

        <Box>
          <Divider sx={{ borderColor: "divider" }} />
          <Row label="Job Title" value={v.title || "—"} />
          <Row label="Company" value={v.hiring_company || "—"} />
          <Row label="Location" value={`${v.city}, ${countryLabel(v.country)}`} />
          <Row label="Employment Type" value={humanize(v.employment_type)} />
          <Row label="Work Mode" value={humanize(v.work_mode)} />
          <Row label="Salary" value={salary} />
          <Row label="Notice Period" value={optionLabel(NOTICE_PERIOD_OPTIONS, v.notice_period) || "—"} />
          <Row label="Visa Requirement" value={optionLabel(VISA_OPTIONS, v.visa_requirement) || "—"} />
          <Row
            label="Nationality Preference"
            value={optionLabel(NATIONALITY_OPTIONS, v.nationality_preference) || "—"}
          />
          <Row label="Languages Required" value={languages} />
          <Row label="Min Experience" value={minExp} />
          <Row label="Required Skills" value={skills} />
          <Row label="Preferred Skills" value={preferredSkills} />
          <Row
            label="Screening Questions"
            value={`${questions.length} question${questions.length === 1 ? "" : "s"} configured`}
          />
        </Box>
      </CardContent>
    </Card>
  );
};

export default StepReview;
