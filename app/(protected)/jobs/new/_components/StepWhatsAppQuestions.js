"use client";

import Stack from "@mui/material/Stack";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Typography from "@mui/material/Typography";
import TextField from "@mui/material/TextField";
import MenuItem from "@mui/material/MenuItem";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutlined";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { QUESTION_CATEGORIES, questionCategoryLabel } from "@/lib/kabil/jobOptions";

const MAX_QUESTIONS = 15;

/** New custom questions need a unique, backend-shaped id ("q_" + 8 chars). */
const newQuestionId = () =>
  "q_" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);

/** A question HR can edit/remove — only the ones they add themselves. */
const isCustom = (q) => !q.source_field && !q.is_ai_generated;

/**
 * Review step for a job's WhatsApp screening questions. The backend generates a
 * fixed set (locked) plus a few AI background-validation questions (locked, and
 * the only ones whose answers the AI scores). HR can only *add their own*
 * custom questions here. Questions are held in page state (not react-hook-form)
 * because they carry fields the form doesn't — `order`, `question_ar`,
 * `reasoning` — that we round-trip back on save.
 */
const StepWhatsAppQuestions = ({ questions, onChange }) => {
  const update = (index, patch) =>
    onChange(questions.map((q, i) => (i === index ? { ...q, ...patch } : q)));

  // Keep `order` contiguous (1..N) so the save payload stays valid.
  const renumber = (list) => list.map((q, i) => ({ ...q, order: i + 1 }));

  const removeAt = (index) =>
    onChange(renumber(questions.filter((_, i) => i !== index)));

  const addQuestion = () =>
    onChange([
      ...questions,
      {
        id: newQuestionId(),
        order: questions.length + 1,
        category: "commitment",
        subcategory: "",
        question_en: "",
        question_ar: "",
        reasoning: "",
        is_ai_generated: false,
        source_field: null,
        ai_verifies_response: false,
      },
    ]);

  return (
    <Card>
      <CardContent sx={{ p: { xs: 2.5, sm: 4 } }}>
        {/* Header */}
        <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>
          Screening Questions
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
          Sent to every candidate via WhatsApp after CV approval. AI guarantees
          coverage of Background Validations, Commitment to the Job, Salary
          Expectations. Edit or add your own.
        </Typography>

        <Stack spacing={2}>
          {questions.length === 0 && (
            <Box sx={{ py: 4, textAlign: "center", color: "text.secondary" }}>
              <Typography>No questions yet. Add your own below.</Typography>
            </Box>
          )}

          {questions.map((q, index) => {
            const editable = isCustom(q);
            const isBgValidation = q.category === "background_validation";
            return (
              <Box
                key={q.id}
                sx={{
                  borderRadius: "12px",
                  border: "1px solid",
                  borderColor: "#E0DBD0",
                  bgcolor: "#F4F0E8",
                  p: 2,
                }}
              >
                {/* Top row: number + category/type chips, then the question below */}
                <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
                  <Box
                    sx={{
                      width: 26,
                      height: 26,
                      flexShrink: 0,
                      borderRadius: "50%",
                      display: "grid",
                      placeItems: "center",
                      bgcolor: "primary.main",
                      color: "#fff",
                      fontSize: 13,
                      fontWeight: 700,
                    }}
                  >
                    {index + 1}
                  </Box>
                  <Stack
                    direction="row"
                    spacing={1}
                    useFlexGap
                    sx={{ alignItems: "center", flexWrap: "wrap", flex: 1, minWidth: 0 }}
                  >
                    {editable ? (
                      <TextField
                        select
                        label="Category"
                        size="small"
                        sx={{ minWidth: 220 }}
                        value={q.category}
                        onChange={(e) => update(index, { category: e.target.value })}
                      >
                        {QUESTION_CATEGORIES.map((c) => (
                          <MenuItem key={c.value} value={c.value}>
                            {c.label}
                          </MenuItem>
                        ))}
                      </TextField>
                    ) : (
                      <Chip
                        size="small"
                        label={questionCategoryLabel(q.category)}
                        sx={{
                          bgcolor: isBgValidation ? "#E8F5F1" : "#FEF3DC",
                          color: isBgValidation ? "#0F6E56" : "#EF9F27",
                          fontWeight: 700,
                          borderRadius: "5px",
                        }}
                      />
                    )}
                    {!q.source_field && !q.is_ai_generated && (
                      <Chip size="small" variant="outlined" label="Custom" sx={{ borderRadius: "5px" }} />
                    )}
                    {q.reasoning && (
                      <Tooltip title={q.reasoning} arrow>
                        <InfoOutlinedIcon
                          sx={{ fontSize: 18, color: "text.disabled", cursor: "help" }}
                        />
                      </Tooltip>
                    )}
                  </Stack>
                  {editable && (
                    <IconButton onClick={() => removeAt(index)} aria-label="Remove question">
                      <DeleteOutlineIcon />
                    </IconButton>
                  )}
                </Stack>

                <Box sx={{ mt: 1.5, pl: "38px" }}>
                  {editable ? (
                    <Stack spacing={1.5}>
                      <TextField
                        fullWidth
                        multiline
                        minRows={2}
                        label="Question (English)"
                        value={q.question_en}
                        onChange={(e) => update(index, { question_en: e.target.value })}
                      />
                      <TextField
                        fullWidth
                        multiline
                        minRows={1}
                        label="Question (Arabic)"
                        dir="rtl"
                        value={q.question_ar}
                        onChange={(e) => update(index, { question_ar: e.target.value })}
                      />
                    </Stack>
                  ) : (
                    <>
                      <Typography
                        sx={{
                          fontFamily: "var(--font-jakarta), system-ui, sans-serif",
                          fontWeight: 400,
                          fontSize: "14px",
                          lineHeight: "21px",
                          letterSpacing: 0,
                          color: "#2C2C2A",
                        }}
                      >
                        {q.question_en}
                      </Typography>
                      {q.question_ar && (
                        <Typography color="text.secondary" dir="rtl">
                          {q.question_ar}
                        </Typography>
                      )}
                    </>
                  )}
                </Box>
              </Box>
            );
          })}
        </Stack>

        <Box sx={{ mt: 2.5 }}>
          <Button
            variant="text"
            disableRipple
            disabled={questions.length >= MAX_QUESTIONS}
            onClick={addQuestion}
            sx={{
              p: 0,
              minWidth: 0,
              color: "#0F6E56",
              fontFamily: "var(--font-jakarta), system-ui, sans-serif",
              fontWeight: 500,
              fontSize: "14px",
              lineHeight: "21px",
              letterSpacing: 0,
              textAlign: "center",
              textTransform: "none",
              textDecoration: "underline",
              textDecorationStyle: "solid",
              "&:hover": { textDecoration: "underline", bgcolor: "transparent" },
            }}
          >
            + Add Custom Question
          </Button>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
            {questions.length} / {MAX_QUESTIONS}
          </Typography>
        </Box>
      </CardContent>
    </Card>
  );
};

export default StepWhatsAppQuestions;
