/**
 * Client-side auth state. Because the token lives in localStorage (not an
 * httpOnly cookie), route protection happens in the browser — `ProtectedLayout`
 * consumes this context to gate the HR area. See lib/api/token.js.
 */
"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { getToken } from "@/lib/api/token";
import { login as apiLogin, logout as apiLogout, fetchMe } from "@/lib/api/auth";

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  // "loading" until we've resolved whether the stored token is valid.
  const [status, setStatus] = useState("loading"); // loading | authed | guest

  // On mount, hydrate from a stored token by fetching the current user.
  useEffect(() => {
    let active = true;
    const hydrate = async () => {
      if (!getToken()) {
        if (active) setStatus("guest");
        return;
      }
      try {
        const me = await fetchMe();
        if (active) {
          setUser(me);
          setStatus("authed");
        }
      } catch {
        // 401 is handled by the interceptor (token cleared); treat as guest.
        if (active) setStatus("guest");
      }
    };
    hydrate();
    return () => {
      active = false;
    };
  }, []);

  const login = async (email, password) => {
    await apiLogin(email, password);
    const me = await fetchMe();
    setUser(me);
    setStatus("authed");
    return me;
  };

  const logout = async () => {
    await apiLogout();
    setUser(null);
    setStatus("guest");
  };

  const value = {
    user,
    status,
    isLoading: status === "loading",
    isAuthenticated: status === "authed",
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
};
