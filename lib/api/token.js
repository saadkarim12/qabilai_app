/**
 * Auth-token store. The access token lives in localStorage (the app calls the
 * Kabil backend directly from the browser, so the axios interceptor needs to
 * read the token client-side). SSR-safe: every accessor guards `window`.
 *
 * There is no refresh-token endpoint — when the 8h token lapses the user
 * re-logs in. We persist `expires_at` so we can treat a stale token as gone.
 */

const TOKEN_KEY = "kabil_access_token";
const EXPIRES_KEY = "kabil_expires_at";

const hasWindow = () => typeof window !== "undefined";

export const getToken = () => {
  if (!hasWindow()) return null;
  const token = window.localStorage.getItem(TOKEN_KEY);
  if (!token) return null;
  if (isExpired()) {
    clearToken();
    return null;
  }
  return token;
};

export const getExpiresAt = () =>
  hasWindow() ? window.localStorage.getItem(EXPIRES_KEY) : null;

export const isExpired = () => {
  const expiresAt = getExpiresAt();
  if (!expiresAt) return false;
  return new Date(expiresAt).getTime() <= Date.now();
};

export const setToken = (token, expiresAt) => {
  if (!hasWindow()) return;
  window.localStorage.setItem(TOKEN_KEY, token);
  if (expiresAt) window.localStorage.setItem(EXPIRES_KEY, expiresAt);
};

export const clearToken = () => {
  if (!hasWindow()) return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(EXPIRES_KEY);
};
