"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useForm, FormProvider } from "react-hook-form";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Stepper from "@mui/material/Stepper";
import Step from "@mui/material/Step";
import StepLabel, { stepLabelClasses } from "@mui/material/StepLabel";
import StepConnector, { stepConnectorClasses } from "@mui/material/StepConnector";
import { styled } from "@mui/material/styles";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import ArrowBackIcon from "@mui/icons-material/ArrowBackOutlined";
import ArrowForwardIcon from "@mui/icons-material/ArrowForwardOutlined";
import {
  useCreateJob,
  useOpenJob,
  useJob,
  useSaveWhatsAppQuestions,
} from "@/lib/kabil/queries";
import { toJobSpecPayload } from "@/lib/kabil/jobOptions";
import ErrorAlert from "@/components/ErrorAlert";
import StepRoleBasics from "./_components/StepRoleBasics";
import StepJdBuilder from "./_components/StepJdBuilder";
import StepWhatsAppQuestions from "./_components/StepWhatsAppQuestions";
import StepReview from "./_components/StepReview";

// `next` drives the contextual "Next: …" button label; `fields` is the
// react-hook-form group validated before advancing.
const STEPS = [
  {
    label: "Role Basics",
    next: "JD Builder",
    fields: [
      "title",
      "hiring_company",
      "country",
      "city",
      "employment_type",
      "work_mode",
      "min_experience_years",
      "currency",
      "min_salary",
      "max_salary",
      "notice_period",
      "visa_requirement",
      "nationality_preference",
      "languages_required",
      "required_skills",
      "preferred_skills",
    ],
  },
  { label: "JD Builder", next: "Baseline Questions", fields: ["job_description"] },
  { label: "Baseline Questions", next: "Review", fields: [] },
  { label: "Review", next: null, fields: [] },
];

const CREATE_STEP = 1; // boundary where the job is created + opened (JD Builder)

const DEFAULT_VALUES = {
  title: "",
  hiring_company: "",
  country: "AE",
  city: "",
  employment_type: "permanent",
  work_mode: "onsite",
  min_experience_years: 0,
  currency: "AED",
  // Surfaced as inputs in StepRoleBasics; sent through toJobPayload below.
  min_salary: "",
  max_salary: "",
  notice_period: "any",
  visa_requirement: "any",
  nationality_preference: "any",
  languages_required: ["English"],
  required_skills: [],
  preferred_skills: [],
  job_description: "",
};

/** Strip the form-only fields and coerce types into a JobCreateRequest. */
const toJobPayload = (v) => ({
  ...toJobSpecPayload(v),
  job_description: v.job_description.trim(),
});

// Short, light connector line centred on the 32px step circles.
const WizardConnector = styled(StepConnector)(() => ({
  [`&.${stepConnectorClasses.alternativeLabel}`]: { top: 16 },
  [`& .${stepConnectorClasses.line}`]: {
    borderColor: "#E0DBD0",
    borderTopWidth: 1,
  },
}));

// Current/completed steps: filled green disc, white number.
// Future steps: transparent disc with a light outline, grey number.
const WizardStepIcon = ({ active, completed, icon }) => {
  const filled = active || completed;
  return (
    <Box
      sx={{
        width: 32,
        height: 32,
        borderRadius: "50%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        bgcolor: filled ? "#0F6E56" : "transparent",
        border: filled ? "none" : "1px solid #E0DBD0",
        color: filled ? "#FFFFFF" : "#6B7280",
        fontFamily: "var(--font-jakarta)",
        fontWeight: 700,
        fontSize: 13,
        lineHeight: 1,
      }}
    >
      {icon}
    </Box>
  );
};

const stepLabelSx = {
  [`& .${stepLabelClasses.label}`]: {
    mt: 1,
    fontFamily: "var(--font-jakarta)",
    fontWeight: 400,
    fontSize: 14,
    lineHeight: "21px",
    letterSpacing: 0,
    color: "#6B7280",
    [`&.${stepLabelClasses.active}`]: { fontWeight: 700, color: "#2C2C2A" },
    [`&.${stepLabelClasses.completed}`]: { fontWeight: 400, color: "#6B7280" },
  },
};

const NewJobPage = () => {
  const router = useRouter();
  const [active, setActive] = useState(0);
  const [jobId, setJobId] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [questions, setQuestions] = useState([]);

  const createJob = useCreateJob();
  const openJob = useOpenJob();
  const saveQuestions = useSaveWhatsAppQuestions(jobId);

  // Once the job is opened, poll it until the pipeline finishes
  // (`ready_for_applications`) and the AI questions are populated.
  const { data: job, error: pollError } = useJob(jobId, { poll: generating });

  const methods = useForm({
    defaultValues: DEFAULT_VALUES,
    mode: "onSubmit",
    reValidateMode: "onChange",
  });

  const isLast = active === STEPS.length - 1;

  // Generation finished: capture the AI questions and advance to the baseline
  // questions step. Reacting to async poll data arriving is a legitimate
  // external-system sync.
  useEffect(() => {
    if (!generating || !job?.ready_for_applications) return;
    /* eslint-disable react-hooks/set-state-in-effect */
    setQuestions(job.whatsapp_questions ?? []);
    setGenerating(false);
    setActive(CREATE_STEP + 1);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [generating, job]);

  const advanceWithValidation = async () => {
    const ok = await methods.trigger(STEPS[active].fields);
    if (ok) setActive((s) => s + 1);
  };

  // Next on the JD Builder step: validate the description, create the job, open
  // it (which kicks off the async embed-JD + generate-questions pipeline), then
  // stay put while it runs. The poll above advances to the baseline-questions
  // step once they land.
  const generateQuestions = async () => {
    // Came back to this step after already creating the job — just move forward.
    if (jobId) {
      setActive(CREATE_STEP + 1);
      return;
    }
    const ok = await methods.trigger(STEPS[active].fields);
    if (!ok) return;
    setGenerating(true);
    try {
      const { id } = await createJob.mutateAsync(toJobPayload(methods.getValues()));
      setJobId(id);
      await openJob.mutateAsync(id);
      // ready_for_applications is still false here; the useJob poll takes over.
    } catch {
      setGenerating(false); // surfaced via createJob/openJob error alerts
    }
  };

  const handleNext = () =>
    active === CREATE_STEP ? generateQuestions() : advanceWithValidation();

  const finish = async () => {
    try {
      if (jobId && questions.length) await saveQuestions.mutateAsync(questions);
      router.push(`/jobs/${jobId}`);
    } catch {
      // surfaced via saveQuestions error alert
    }
  };

  // Route the form's implicit submit (e.g. Enter) through the wizard logic so it
  // never short-circuits to job creation from an earlier step.
  const onSubmit = (e) => {
    e.preventDefault();
    if (generating) return;
    if (isLast) finish();
    else handleNext();
  };

  const renderStep = () => {
    if (active === 0) return <StepRoleBasics />;
    if (active === 1) return <StepJdBuilder />;
    if (active === 2)
      return <StepWhatsAppQuestions questions={questions} onChange={setQuestions} />;
    return <StepReview questions={questions} />;
  };

  return (
    <Stack spacing={3} sx={{ maxWidth: 1200, mx: "auto", pt: { xs: 1, md: 2 } }}>
      <Box>
        <Typography
          sx={{
            fontFamily: "var(--font-jakarta), system-ui, sans-serif",
            fontWeight: 700,
            fontSize: "24px",
            lineHeight: "36px",
            letterSpacing: 0,
            color: "#0F6E56",
          }}
        >
          Post New Job
        </Typography>
      </Box>

      <Stepper activeStep={active} alternativeLabel connector={<WizardConnector />}>
        {STEPS.map((s) => (
          <Step key={s.label}>
            <StepLabel slots={{ stepIcon: WizardStepIcon }} sx={stepLabelSx}>
              {s.label}
            </StepLabel>
          </Step>
        ))}
      </Stepper>

      <FormProvider {...methods}>
        <Box
          component="form"
          onSubmit={onSubmit}
          noValidate
          sx={{
            "& .MuiOutlinedInput-root": {
              bgcolor: "#F4F0E84D",
              borderRadius: "5px",
              "& fieldset": { borderColor: "#E0DBD0" },
              "&:hover fieldset": { borderColor: "#E0DBD0" },
              "&.Mui-focused fieldset": { borderColor: "primary.main" },
            },
          }}
        >
          {generating ? (
            <Card>
              <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
                <Stack
                  spacing={2}
                  sx={{ py: 6, textAlign: "center", alignItems: "center" }}
                >
                  <CircularProgress />
                  <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                    Generating baseline screening questions…
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 420 }}>
                    We&apos;re embedding the job description and drafting tailored
                    questions. This usually takes a few seconds — you&apos;ll move to the
                    next step automatically.
                  </Typography>
                </Stack>
              </CardContent>
            </Card>
          ) : (
            renderStep()
          )}

          {createJob.isError && <ErrorAlert error={createJob.error} sx={{ mt: 2 }} />}
          {openJob.isError && <ErrorAlert error={openJob.error} sx={{ mt: 2 }} />}
          {pollError && <ErrorAlert error={pollError} sx={{ mt: 2 }} />}
          {saveQuestions.isError && (
            <ErrorAlert error={saveQuestions.error} sx={{ mt: 2 }} />
          )}

          <Stack
            direction="row"
            spacing={1.5}
            sx={{ justifyContent: "space-between", mt: 3 }}
          >
            <Button
              type="button"
              variant="outlined"
              color="inherit"
              startIcon={active > 0 ? <ArrowBackIcon /> : undefined}
              disabled={generating || saveQuestions.isPending}
              onClick={() =>
                active === 0 ? router.push("/jobs") : setActive((s) => s - 1)
              }
              sx={{ borderRadius: "5px", px: 2.5 }}
            >
              {active === 0 ? "Cancel" : "Back"}
            </Button>

            {isLast ? (
              <Button
                type="submit"
                variant="contained"
                disabled={saveQuestions.isPending}
                sx={{ borderRadius: "5px", px: 3 }}
              >
                {saveQuestions.isPending ? "Publishing…" : "Publish Job"}
              </Button>
            ) : (
              <Button
                type="submit"
                variant="contained"
                endIcon={<ArrowForwardIcon />}
                disabled={generating || createJob.isPending}
                sx={{ borderRadius: "5px", px: 3 }}
              >
                {generating ? "Generating…" : `Next: ${STEPS[active].next}`}
              </Button>
            )}
          </Stack>
        </Box>
      </FormProvider>
    </Stack>
  );
};

export default NewJobPage;
