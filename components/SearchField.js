/** Consistent search input used across console pages. */
"use client";

import TextField from "@mui/material/TextField";
import InputAdornment from "@mui/material/InputAdornment";
import SearchOutlinedIcon from "@mui/icons-material/SearchOutlined";

const SearchField = ({ placeholder = "Search…", width = 300, sx, ...props }) => (
  <TextField
    size="small"
    placeholder={placeholder}
    sx={{
      width: { xs: "100%", sm: width },
      "& .MuiOutlinedInput-root": {
        borderRadius: 2,
        bgcolor: "background.paper",
      },
      ...sx,
    }}
    slotProps={{
      input: {
        startAdornment: (
          <InputAdornment position="start">
            <SearchOutlinedIcon fontSize="small" sx={{ color: "text.secondary" }} />
          </InputAdornment>
        ),
      },
    }}
    {...props}
  />
);

export default SearchField;
