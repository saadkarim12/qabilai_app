"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import InputAdornment from "@mui/material/InputAdornment";
import IconButton from "@mui/material/IconButton";
import CircularProgress from "@mui/material/CircularProgress";
import Visibility from "@mui/icons-material/Visibility";
import VisibilityOff from "@mui/icons-material/VisibilityOff";
import { useAuth } from "@/lib/auth/AuthContext";
import { COLORS } from "@/lib/theme";
import ErrorAlert from "@/components/ErrorAlert";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Diamond hero image wrapped in a soft gold radiance.
 * Radiance spec (from design): #EF9F27 @ 50% opacity, layer blur 100.
 */
const HeroPanel = () => (
  <Box
    sx={{
      position: "relative",
      display: { xs: "none", md: "flex" },
      alignItems: "center",
      justifyContent: "center",
      flex: 1,
      minWidth: 0,
      px: 6,
    }}
  >
    {/* Gold radiance behind the diamond */}
    <Box
      aria-hidden
      sx={{
        position: "absolute",
        width: { md: 360, lg: 440 },
        height: { md: 360, lg: 440 },
        transform: "rotate(45deg)",
        borderRadius: 8,
        bgcolor: `${COLORS.gold}80`,
        filter: "blur(100px)",
        pointerEvents: "none",
      }}
    />

    <Box
      component="img"
      src="/LoginImage.png"
      alt="Qabil"
      sx={{
        position: "relative",
        width: "100%",
        maxWidth: { md: 380, lg: 460 },
        height: "auto",
        display: "block",
      }}
    />
  </Box>
);

/** Labelled outlined field matching the design (label above the input). */
const Field = ({ label, children }) => (
  <Box>
    <Typography
      component="label"
      sx={{ display: "block", mb: 1, fontWeight: 600, fontSize: 13 }}
    >
      {label}
    </Typography>
    {children}
  </Box>
);

const FIELD_SX = {
  "& .MuiOutlinedInput-root": {
    borderRadius: "5px",
    bgcolor: "#fff",
  },
};

const LoginForm = () => {
  const router = useRouter();
  const params = useSearchParams();
  const { login } = useAuth();
  const [serverError, setServerError] = useState(null);
  const [showPassword, setShowPassword] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm({
    // Validate only after the first submit attempt, then live-revalidate on
    // change. Avoids showing errors before the user has interacted.
    mode: "onSubmit",
    reValidateMode: "onChange",
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = async ({ email, password }) => {
    setServerError(null);
    try {
      await login(email.trim(), password);
      router.replace(params.get("next") || "/jobs");
    } catch (err) {
      setServerError(err);
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        flex: 1,
        minWidth: 0,
        alignItems: "center",
        justifyContent: "center",
        px: { xs: 3, sm: 6, md: 8 },
        py: 6,
      }}
    >
      <Box sx={{ width: "100%", maxWidth: 440 }}>
        {/* Compact brand mark — only visible when the hero panel is hidden */}
        <Box sx={{ display: { md: "none" }, mb: 4 }}>
          <Box
            component="img"
            src="/Qabil_logo.svg"
            alt="Qabil"
            sx={{ height: 40, width: "auto", display: "block" }}
          />
        </Box>

        <Typography
          variant="h4"
          sx={{ mb: 4, fontWeight: 500, textAlign: "center" }}
        >
          Welcome back
        </Typography>

        <Box component="form" onSubmit={handleSubmit(onSubmit)} noValidate>
          <Stack spacing={2.5}>
            <ErrorAlert error={serverError} />

            <Field label="Work email">
              <TextField
                type="email"
                autoComplete="email"
                autoFocus
                fullWidth
                placeholder="you@company.com"
                disabled={isSubmitting}
                error={!!errors.email}
                helperText={errors.email?.message}
                sx={FIELD_SX}
                {...register("email", {
                  required: "Email is required",
                  pattern: {
                    value: EMAIL_PATTERN,
                    message: "Enter a valid email address",
                  },
                })}
              />
            </Field>

            <Field label="Password">
              <TextField
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                fullWidth
                placeholder="At least 8 characters"
                disabled={isSubmitting}
                error={!!errors.password}
                helperText={errors.password?.message}
                sx={FIELD_SX}
                slotProps={{
                  input: {
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          aria-label={
                            showPassword ? "Hide password" : "Show password"
                          }
                          onClick={() => setShowPassword((v) => !v)}
                          edge="end"
                        >
                          {showPassword ? <VisibilityOff /> : <Visibility />}
                        </IconButton>
                      </InputAdornment>
                    ),
                  },
                }}
                {...register("password", {
                  required: "Password is required",
                  minLength: {
                    value: 8,
                    message: "Password must be at least 8 characters",
                  },
                })}
              />
            </Field>

            <Button
              type="submit"
              variant="contained"
              size="large"
              disabled={isSubmitting}
              fullWidth
              sx={{ py: 1.5, borderRadius: "5px", mt: 1 }}
              startIcon={
                isSubmitting ? (
                  <CircularProgress size={18} color="inherit" />
                ) : null
              }
            >
              {isSubmitting ? "Signing in…" : "Sign in"}
            </Button>
          </Stack>
        </Box>
      </Box>
    </Box>
  );
};

const LoginPage = () => (
  <Box
    sx={{
      position: "relative",
      display: "flex",
      minHeight: "100dvh",
      bgcolor: "#fff",
      overflow: "hidden",
    }}
  >
    {/* Faint brand watermark behind the form */}
    <Box
      aria-hidden
      component="img"
      src="/Qabil_logo.svg"
      alt=""
      sx={{
        position: "absolute",
        top: "50%",
        right: "-4%",
        transform: "translateY(-50%)",
        width: 480,
        maxWidth: "45%",
        opacity: 0.04,
        pointerEvents: "none",
        display: { xs: "none", md: "block" },
      }}
    />

    {/* Logo — top-left */}
    <Box
      component="img"
      src="/Qabil_logo.svg"
      alt="Qabil"
      sx={{
        position: "absolute",
        top: 32,
        left: 40,
        height: 40,
        width: "auto",
        zIndex: 2,
        display: { xs: "none", md: "block" },
      }}
    />

    <HeroPanel />
    <Suspense>
      <LoginForm />
    </Suspense>
  </Box>
);

export default LoginPage;
