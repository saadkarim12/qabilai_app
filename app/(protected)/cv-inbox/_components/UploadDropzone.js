"use client";

import { useRef, useState } from "react";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import Button from "@mui/material/Button";
import CloseIcon from "@mui/icons-material/Close";
import PictureAsPdfOutlinedIcon from "@mui/icons-material/PictureAsPdfOutlined";
import FileUploadOutlinedIcon from "@mui/icons-material/FileUploadOutlined";
import { CV_ACCEPT, BULK_MAX_FILES } from "@/lib/kabil/constants";

const formatBytes = (n) => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

/** Drag-and-drop CV picker with a selected-files list. Validation lives in the
 *  parent (it owns the file array); this only collects File objects. */
const UploadDropzone = ({ files, onAdd, onRemove, onClear, disabled }) => {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  const collect = (fileList) => {
    if (fileList && fileList.length) onAdd(Array.from(fileList));
  };

  return (
    <Stack spacing={2}>
      <Box
        role="button"
        tabIndex={disabled ? -1 : 0}
        onClick={() => !disabled && inputRef.current?.click()}
        onKeyDown={(e) => {
          if (!disabled && (e.key === "Enter" || e.key === " ")) inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!disabled) collect(e.dataTransfer.files);
        }}
        sx={{
          border: "1.5px dashed",
          borderColor: dragging ? "primary.main" : "#cdd5d1",
          borderRadius: "12px",
          bgcolor: dragging ? "rgba(19,64,45,0.04)" : "transparent",
          px: 2,
          py: 5,
          textAlign: "center",
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.6 : 1,
          transition: "border-color .15s ease, background-color .15s ease",
          "&:hover": { borderColor: disabled ? "#cdd5d1" : "primary.main" },
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={CV_ACCEPT}
          hidden
          disabled={disabled}
          onChange={(e) => {
            collect(e.target.files);
            e.target.value = "";
          }}
        />
        <FileUploadOutlinedIcon sx={{ fontSize: 34, color: "text.secondary", mb: 1 }} />
        <Typography sx={{ fontWeight: 700, fontSize: "0.9rem" }}>
          Drag and drop CVs here
        </Typography>
        <Typography color="text.secondary" sx={{ fontSize: "0.75rem" }}>
          PDF or Word — up to {BULK_MAX_FILES} files at once
        </Typography>
      </Box>

      {files.length > 0 && (
        <Stack spacing={1}>
          <Stack direction="row" sx={{ alignItems: "center", justifyContent: "space-between" }}>
            <Typography variant="body2" color="text.secondary">
              {files.length} file{files.length === 1 ? "" : "s"} selected
            </Typography>
            <Button size="small" color="inherit" onClick={onClear} disabled={disabled}>
              Clear all
            </Button>
          </Stack>

          <Stack spacing={1}>
            {files.map((file, i) => (
              <Stack
                key={`${file.name}-${file.size}-${i}`}
                direction="row"
                spacing={1.5}
                sx={{
                  alignItems: "center",
                  border: "1px solid #e7eae8",
                  borderRadius: 1.5,
                  px: 1.5,
                  py: 1,
                }}
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
                  aria-label={`Remove ${file.name}`}
                  onClick={() => onRemove(i)}
                  disabled={disabled}
                >
                  <CloseIcon fontSize="small" />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        </Stack>
      )}
    </Stack>
  );
};

export default UploadDropzone;
