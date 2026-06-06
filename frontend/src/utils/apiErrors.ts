/**
 * Map an axios/fetch error to user-facing copy.
 *
 * Why this exists: every page used to do
 *   `err?.response?.data?.detail || 'Failed to <verb>'`
 * which silently degrades to a useless fallback when the failure is at
 * the network layer (no `response` at all — connection refused, DNS,
 * CORS, server down).  Users were told "Failed to update entry" when
 * the real problem was that the backend wasn't reachable.
 *
 * The mapping prefers specificity in this order:
 *   1. A backend `detail` string from the response body — these are
 *      hand-written and already user-appropriate.
 *   2. HTTP status families with stable copy.
 *   3. Network/timeout heuristics.
 *   4. The caller-provided fallback verb.
 *
 * The original error is left untouched for the caller to log via
 * `console.error` if it wants axios internals for triage.
 */
// Loose shape we expect from axios errors.  Typed as a union of "could be
// anything" + the known axios fields so call sites can pass `unknown` from
// `catch (err: unknown)` blocks and we narrow internally instead of forcing
// every page to `(err as any)`.
export type AxiosLikeError = {
  response?: { status?: number; data?: { detail?: unknown } & Record<string, unknown> };
  code?: string;
  message?: string;
  name?: string;
};

/**
 * Narrow an `unknown` caught error to the loose axios-like shape so call
 * sites can read `.response?.status` etc. without per-site casts.  Safe by
 * construction: every consumer property is optional, so reading off a
 * non-axios error (Error, string, null) silently yields undefined.
 *
 * Use this when the catch needs to branch on a specific field (status code,
 * cancel signal, response blob).  For pure user-facing copy, prefer the
 * default `formatApiError(err, fallback)`.
 */
export function asAxiosError(err: unknown): AxiosLikeError {
  return (err ?? {}) as AxiosLikeError;
}

export function formatApiError(err: unknown, fallback: string): string {
  // Treat the input as the loose axios-like shape; every property access
  // is optional-chained so non-axios inputs (Error, string, undefined)
  // fall through to the fallback safely.
  const e = err as AxiosLikeError | null | undefined;

  // Backend `detail` strings are already curated copy — surface them
  // verbatim before falling back to generic mapping.  We only accept
  // string detail; some endpoints return arrays of validation errors,
  // which would render as "[object Object]" — skip those.
  const detail = e?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }

  const status: number | undefined = e?.response?.status;

  if (status === 401) {
    return 'Your session has expired. Please sign in again.';
  }
  if (status === 403) {
    return 'You do not have permission to perform this action.';
  }
  if (status === 404) {
    return 'The requested item could not be found. It may have been deleted.';
  }
  if (status === 409) {
    return 'This item was changed by someone else. Reload and try again.';
  }
  if (status === 423) {
    return 'This resource is locked. Contact an administrator.';
  }
  if (status === 429) {
    return 'Too many requests. Please wait a moment and try again.';
  }
  if (status && status >= 500) {
    return 'The server is having trouble right now. Please try again shortly.';
  }

  // No HTTP response at all — true network-level failure.  Axios sets
  // err.code === 'ERR_NETWORK' for these; some browsers also surface
  // the legacy 'Network Error' message.
  if (e?.code === 'ERR_NETWORK' || e?.message === 'Network Error') {
    return 'Could not reach the server. Check your connection or whether the backend is running.';
  }
  if (e?.code === 'ECONNABORTED' || /timeout/i.test(e?.message || '')) {
    return 'The server took too long to respond. Please try again.';
  }

  return fallback;
}
