/**
 * Free-text chips input (type, press Enter to add). Controlled: `value` is a
 * string[] and `onChange` receives the new array. Used for skills / nationality.
 */
"use client";

import Autocomplete from "@mui/material/Autocomplete";
import TextField from "@mui/material/TextField";
import Chip from "@mui/material/Chip";

const TagsInput = ({
  value = [],
  onChange,
  placeholder,
  error,
  helperText,
  max = 50,
}) => (
  <Autocomplete
    multiple
    freeSolo
    options={[]}
    value={value}
    onChange={(_, next) => {
      const trimmed = next.map((t) => t.trim()).filter(Boolean);
      onChange([...new Set(trimmed)].slice(0, max));
    }}
    renderTags={(tags, getTagProps) =>
      tags.map((tag, index) => (
        <Chip
          size="small"
          label={tag}
          {...getTagProps({ index })}
          key={tag}
        />
      ))
    }
    renderInput={(params) => (
      <TextField
        {...params}
        placeholder={value.length ? "" : placeholder}
        error={error}
        helperText={helperText}
      />
    )}
  />
);

export default TagsInput;
