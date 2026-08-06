"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppRouterCacheProvider } from "@mui/material-nextjs/v16-appRouter";
import { ThemeProvider } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import { theme } from "@/lib/theme";
import { AuthProvider } from "@/lib/auth/AuthContext";

const Providers = ({ children }) => {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 },
        },
      }),
  );

  return (
    <AppRouterCacheProvider options={{ key: "mui" }}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <QueryClientProvider client={client}>
          <AuthProvider>{children}</AuthProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </AppRouterCacheProvider>
  );
};

export default Providers;
