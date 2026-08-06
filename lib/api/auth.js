/**
 * Raw auth calls against the backend. The token lifecycle (persisting,
 * clearing) is owned by these helpers; React state lives in AuthContext.
 */

import { apiJson } from "./client";
import { setToken, clearToken } from "./token";

/** POST /auth/login → persists the token, returns the login payload. */
export const login = async (email, password) => {
  const data = await apiJson.post("/auth/login", { email, password });
  setToken(data.access_token, data.expires_at);
  return data; // { access_token, token_type, expires_at }
};

/** POST /auth/logout → revokes server-side session, always clears locally. */
export const logout = async () => {
  try {
    await apiJson.post("/auth/logout");
  } finally {
    clearToken();
  }
};

/** GET /auth/me → current HR user. */
export const fetchMe = () => apiJson.get("/auth/me");
