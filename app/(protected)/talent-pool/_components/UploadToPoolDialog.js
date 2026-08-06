"use client";

import { useRef, useState } from "react";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import DialogActions from "@mui/material/DialogActions";
import Stack from "@mui/material/Stack";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import CircularProgress from "@mui/material/CircularProgress";
import CloseIcon from "@mui/icons-material/Close";
import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined";
import PictureAsPdfOutlinedIcon from "@mui/icons-material/PictureAsPdfOutlined";
import ErrorAlert from "@/components/ErrorAlert";
import { useTalentPoolUpload } from "@/lib/kabil/queries";
import {
  CV_ACCEPT,
  CV_MAX_MB,
  PDPL_CONSENT_TEXT,
} from "@/lib/kabil/constants";

const isPdf = (file) =>
  file.type === "application/pdf" || /\.pdf$/i.test(file.name);

const formatBytes = (n) => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

/**
 * Drops a single CV straight into the pool (no job). Identity is parsed from the
 * CV; the candidate becomes searchable once the embed pipeline finishes
 * (`enqueued`). PDPL consent is a client-side gate — the backend takes only the
 * file, so we don't send the flag.
 */
const UploadToPoolDialog = ({ open, onClose }) => {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState(null);
  const [localError, setLocalError] = useState(null);
  const [consent, setConsent] = useState(false);
  const [result, setResult] = useState(null);

  const upload = useTalentPoolUpload();

  const reset = () => {
    setFile(null);
    setLocalError(null);
    setConsent(false);
    setResult(null);
    upload.reset();
  };

  const close = () => {
    reset();
    onClose();
  };

  const pick = (fileList) => {
    const f = fileList?.[0];
    if (!f) return;
    if (!isPdf(f)) {
      setLocalError("Only PDF files are accepted.");
      return;
    }
    if (f.size > CV_MAX_MB * 1024 * 1024) {
      setLocalError(`File exceeds the ${CV_MAX_MB} MB limit.`);
      return;
    }
    setLocalError(null);
    setFile(f);
  };

  const handleUpload = () => {
    if (!file) return;
    upload.mutate(file, { onSuccess: (res) => setResult(res) });
  };

  const canUpload = !!file && consent && !upload.isPending;

  return (
    <Dialog open={open} onClose={close} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        Upload CV to pool
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25 }}>
          Adds a candidate to the pool without tying them to a job.
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 2 }}>
        {result ? (
          <Alert severity="success">
            <AlertTitle>
              {result.candidate_created
                ? "Candidate added to the pool"
                : "Candidate updated in the pool"}
            </AlertTitle>
            <Typography variant="body2">
              {result.entry?.candidate?.full_name
                ? `${result.entry.candidate.full_name} is in the pool. `
                : "The candidate is in the pool. "}
              {result.enqueued
                ? "We're indexing their CV — they'll appear in search results shortly."
                : "They're ready to search and source."}
            </Typography>
          </Alert>
        ) : (
          <Stack spacing={2.5}>
            <Box
              role="button"
              tabIndex={0}
              onClick={() => !upload.isPending && inputRef.current?.click()}
              onKeyDown={(e) => {
                if (!upload.isPending && (e.key === "Enter" || e.key === " "))
                  inputRef.current?.click();
              }}
              onDragOver={(e) => {
                e.preventDefault();
                if (!upload.isPending) setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (!upload.isPending) pick(e.dataTransfer.files);
              }}
              sx={{
                border: "1.5px dashed",
                borderColor: dragging ? "primary.main" : "#cdd5d1",
                borderRadius: 2,
                bgcolor: dragging ? "rgba(19,64,45,0.04)" : "transparent",
                px: 2,
                py: 4,
                textAlign: "center",
                cursor: upload.isPending ? "not-allowed" : "pointer",
                opacity: upload.isPending ? 0.6 : 1,
                transition: "border-color .15s ease, background-color .15s ease",
                "&:hover": { borderColor: upload.isPending ? "#cdd5d1" : "primary.main" },
              }}
            >
              <input
                ref={inputRef}
                type="file"
                accept={CV_ACCEPT}
                hidden
                disabled={upload.isPending}
                onChange={(e) => {
                  pick(e.target.files);
                  e.target.value = "";
                }}
              />
              <CloudUploadOutlinedIcon sx={{ fontSize: 34, color: "text.secondary", mb: 1 }} />
              <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                Drag and drop a CV here
              </Typography>
              <Typography variant="body2" color="text.secondary">
                One PDF — up to {CV_MAX_MB} MB
              </Typography>
            </Box>

            {file && (
              <Stack
                direction="row"
                spacing={1.5}
                sx={{ alignItems: "center", border: "1px solid #e7eae8", borderRadius: 1.5, px: 1.5, py: 1 }}
              >
                <PictureAsPdfOutlinedIcon sx={{ color: "error.main", fontSize: 22 }} />
                <Box sx={{ minWidth: 0, flexGrow: 1 }}>
                  <Typography variant="body2" noWrap title={file.name} sx={{ fontWeight: 600 }}>
                    {file.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {formatBytes(file.size)}
                  </Typography>
                </Box>
                <IconButton
                  size="small"
                  aria-label="Remove file"
                  onClick={() => setFile(null)}
                  disabled={upload.isPending}
                >
                  <CloseIcon fontSize="small" />
                </IconButton>
              </Stack>
            )}

            {localError && (
              <Alert severity="warning" onClose={() => setLocalError(null)}>
                {localError}
              </Alert>
            )}

            <Box
              sx={{
                border: "1px solid",
                borderColor: "primary.main",
                bgcolor: "rgba(19,64,45,0.04)",
                borderRadius: 1.5,
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
                  <Typography variant="body2">{PDPL_CONSENT_TEXT}</Typography>
                }
                sx={{ alignItems: "flex-start", m: 0, py: 0.5 }}
              />
            </Box>

            {upload.isError && <ErrorAlert error={upload.error} />}
          </Stack>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {result ? (
          <>
            <Button onClick={reset} color="inherit">
              Upload another
            </Button>
            <Button onClick={close} variant="contained">
              Done
            </Button>
          </>
        ) : (
          <>
            <Button onClick={close} color="inherit">
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={handleUpload}
              disabled={!canUpload}
              startIcon={
                upload.isPending ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <CloudUploadOutlinedIcon />
                )
              }
            >
              {upload.isPending ? "Uploading…" : "Upload to pool"}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default UploadToPoolDialog;
