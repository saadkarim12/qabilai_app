/**
 * Axios instance that talks directly to the Kabil backend.
 *
 * Interceptors centralise the cross-cutting concerns:
 *   - request:  attach `Authorization: Bearer <token>` from the token store.
 *   - response: unwrap the data, normalise the backend error envelope into a
 *               `KabilApiError`, and on 401 clear the session + bounce to /login.
 *
 * The backend's CORS allow-list must include this frontend's origin.
 */

import axios from "axios";
import { getToken, clearToken } from "./token";

export const BASE_URL =
  process.env.NEXT_PUBLIC_KABIL_API || "http://localhost:8000";

/** Normalised error thrown for every non-2xx response. */
export class KabilApiError extends Error {
  constructor(status, body) {
    super(body?.message || body?.error || `HTTP ${status}`);
    this.name = "KabilApiError";
    this.status = status;
    this.body = body; // { error, message, details?, correlation_id }
    this.correlationId = body?.correlation_id || null;
  }
}

export const apiClient = axios.create({
  baseURL: BASE_URL,
  // FormData uploads set their own multipart boundary, so we never hard-code
  // a default Content-Type here — axios infers it per request.
});

/* ── Request interceptor: attach the bearer token ───────────────────────── */
apiClient.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

/* ── Response interceptor: unwrap + normalise errors ────────────────────── */
const redirectToLogin = () => {
  if (typeof window === "undefined") return;
  if (window.location.pathname !== "/login") {
    const next = encodeURIComponent(
      window.location.pathname + window.location.search,
    );
    window.location.assign(`/login?next=${next}`);
  }
};

apiClient.interceptors.response.use(
  (response) => response.data,
  (error) => {
    // Network / CORS / no-response failures have no `error.response`.
    if (!error.response) {
      return Promise.reject(
        new KabilApiError(0, {
          error: "network_error",
          message: error.message || "Network request failed",
        }),
      );
    }

    const { status, data } = error.response;
    if (status === 401) {
      clearToken();
      redirectToLogin();
    }
    return Promise.reject(new KabilApiError(status, data || {}));
  },
);

/* ── Thin verb helpers (each resolves to the response body) ─────────────── */
export const apiJson = {
  get: (path, config) => apiClient.get(path, config),
  post: (path, body, config) => apiClient.post(path, body, config),
  patch: (path, body, config) => apiClient.patch(path, body, config),
  del: (path, config) => apiClient.delete(path, config),
};

/** Multipart upload — pass a FormData; axios sets the boundary itself. */
export const apiUpload = (path, formData) => apiClient.post(path, formData);

/* ── Public (anonymous) client ──────────────────────────────────────────────
 * The candidate-facing apply route hits `/public/...` with no session. This
 * instance deliberately skips the bearer token and the 401→/login redirect so a
 * stale token never leaks onto a public page and a candidate is never bounced to
 * the HR login. It still normalises errors into KabilApiError.
 */
export const publicClient = axios.create({ baseURL: BASE_URL });

publicClient.interceptors.response.use(
  (response) => response.data,
  (error) => {
    if (!error.response) {
      return Promise.reject(
        new KabilApiError(0, {
          error: "network_error",
          message: error.message || "Network request failed",
        }),
      );
    }
    const { status, data } = error.response;
    return Promise.reject(new KabilApiError(status, data || {}));
  },
);

export const apiPublic = {
  get: (path, config) => publicClient.get(path, config),
  upload: (path, formData) => publicClient.post(path, formData),
};
