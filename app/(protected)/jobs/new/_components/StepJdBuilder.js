"use client";

import { useFormContext } from "react-hook-form";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import TextField from "@mui/material/TextField";
import Button from "@mui/material/Button";
import Tooltip from "@mui/material/Tooltip";
import CircularProgress from "@mui/material/CircularProgress";
import ContentPasteOutlinedIcon from "@mui/icons-material/ContentPasteOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import { useGenerateJobDescription } from "@/lib/kabil/queries";
import { toJobSpecPayload } from "@/lib/kabil/jobOptions";
import ErrorAlert from "@/components/ErrorAlert";

const MIN = 100;
const MAX = 20000;

const StepJdBuilder = () => {
  const {
    register,
    watch,
    getValues,
    setValue,
    formState: { errors },
  } = useFormContext();

  const generate = useGenerateJobDescription();

  const jd = watch("job_description") || "";
  const hasJd = jd.trim().length > 0;

  // The AI builder drafts from the Role-Basics fields; gate it on the two
  // load-bearing inputs so we never prompt Claude with near-empty context.
  const title = watch("title") || "";
  const requiredSkills = watch("required_skills") || [];
  const canGenerate = title.trim().length > 0 && requiredSkills.length > 0;

  const handleGenerate = async () => {
    const values = getValues();
    try {
      const data = await generate.mutateAsync({
        spec: toJobSpecPayload(values),
        // A repeat click once text exists should produce a fresh draft.
        regenerate: hasJd,
      });
      setValue("job_description", data.job_description, {
        shouldValidate: true,
        shouldDirty: true,
      });
    } catch {
      // surfaced via the ErrorAlert below
    }
  };

  return (
    <Card>
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
        {/* Header: title + source toggles */}
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={2}
          sx={{ alignItems: { sm: "center" }, justifyContent: "space-between", mb: 0.5 }}
        >
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              Job Description
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Paste your existing JD
            </Typography>
          </Box>
          <Stack direction="row" spacing={1}>
            <Button
              variant="contained"
              size="small"
              startIcon={<ContentPasteOutlinedIcon />}
              sx={{ borderRadius: "5px" }}
            >
              Paste JD
            </Button>
            <Tooltip
              title={
                canGenerate
                  ? "Draft a job description from your Role Basics"
                  : "Add a job title and at least one required skill first"
              }
            >
              <span>
                <Button
                  variant="outlined"
                  size="small"
                  color="secondary"
                  onClick={handleGenerate}
                  disabled={!canGenerate || generate.isPending}
                  startIcon={
                    generate.isPending ? (
                      <CircularProgress size={16} color="inherit" />
                    ) : (
                      <AutoAwesomeOutlinedIcon />
                    )
                  }
                  sx={{ borderRadius: "5px" }}
                >
                  {generate.isPending ? "Drafting…" : hasJd ? "Regenerate" : "AI Builder"}
                </Button>
              </span>
            </Tooltip>
          </Stack>
        </Stack>

        <Typography
          variant="overline"
          sx={{ display: "block", mt: 2, mb: 1, color: "text.secondary", fontWeight: 700 }}
        >
          About the Role
        </Typography>
        <TextField
          fullWidth
          multiline
          minRows={12}
          placeholder="Paste or write the full job description — responsibilities, requirements, and what success looks like…"
          error={!!errors.job_description}
          helperText={errors.job_description?.message}
          {...register("job_description", {
            required: "Job description is required",
            minLength: { value: MIN, message: `Add at least ${MIN} characters` },
            maxLength: { value: MAX, message: `Max ${MAX.toLocaleString()} characters` },
          })}
          sx={{ "& .MuiOutlinedInput-root": { bgcolor: "#F4F0E84D" } }}
        />
        <Stack
          direction="row"
          sx={{ justifyContent: "space-between", alignItems: "center", mt: 0.75 }}
        >
          <Typography variant="caption" color="text.secondary">
            Minimum {MIN} characters
          </Typography>
          <Typography
            variant="caption"
            color={jd.length > MAX ? "error" : "text.secondary"}
          >
            {jd.length.toLocaleString()} / {MAX.toLocaleString()}
          </Typography>
        </Stack>

        {generate.isError && <ErrorAlert error={generate.error} sx={{ mt: 2 }} />}

        <Typography variant="body2" color="text.secondary" sx={{ mt: 2, textAlign: "right" }}>
          {hasJd
            ? "AI-drafted text is editable — tweak anything before continuing."
            : "Don’t have a JD yet? Use AI builder"}
        </Typography>
      </CardContent>
    </Card>
  );
};

export default StepJdBuilder;
