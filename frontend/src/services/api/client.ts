/**
 * Axios client + project-scoping helpers.
 *
 * v2.29.0 — extracted from the monolithic ``services/api.ts``.  The
 * configured axios instance and the ``projectPath(projectId)``
 * project-prefix helper are used by the submodules under
 * ``services/api/``.  ``api.ts`` itself is now a barrel re-exporting
 * from those submodules so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import axios from 'axios';

import { getApiBaseUrl } from '../../utils/apiUrl';
import { loginUrlFrom } from '../../utils/loginReturn';

const API_BASE_URL = getApiBaseUrl();

export const api = axios.create({
  baseURL: `${API_BASE_URL}/api/v1`,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add request interceptor to include authentication token
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('auth_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

/**
 * What an authentication refusal does, for EVERY request — the axios
 * interceptor below and the upload's raw XHR (`uploads.ts`) both call this.
 *
 * `sentToken` is the token the refused request carried (null for none).  A
 * 401 ends the session only when that is still the stored token: a request
 * sent under an older session that answers after this or another tab stored a
 * new one must not delete the new one (the same guard as
 * `AuthContext.renewSession`).
 */
export function handleAuthFailure(
  status: number | undefined,
  detail: unknown,
  sentToken: string | null,
): void {
  if (status === 401) {
    if (localStorage.getItem('auth_token') !== sentToken) return;
    localStorage.removeItem('auth_token');
    localStorage.removeItem('auth_user');
    // Redirect to login page (guard against infinite loop if already on /login)
    if (window.location.pathname !== '/login') {
      window.location.href = loginUrlFrom(window.location.pathname, window.location.search);
    }
    return;
  }
  if (status !== 403) return;
  // Backend returns 403 with detail "password_change_required" when the
  // user's must_change_password flag is set.  Redirect to the forced
  // password-change page so the user can't do anything else first.
  if (detail === 'password_change_required' && window.location.pathname !== '/force-change-password') {
    window.location.href = '/force-change-password';
  }
  // Mandatory 2FA (REQUIRE_2FA): the gate returns this until the user
  // enrolls.  Force them to the 2FA setup page so nothing else is reachable.
  if (detail === 'two_factor_setup_required' && window.location.pathname !== '/force-2fa-setup') {
    window.location.href = '/force-2fa-setup';
  }
}

/** The bearer token a request was sent with, or null. */
function tokenSentWith(config: { headers?: unknown } | undefined): string | null {
  const headers = config?.headers as { Authorization?: unknown } | undefined;
  const auth = headers?.Authorization;
  return typeof auth === 'string' && auth.startsWith('Bearer ') ? auth.slice('Bearer '.length) : null;
}

/** A Blob's text (`FileReader` where `Blob.text` is missing — jsdom). */
function blobText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

/**
 * A download (`responseType: 'blob'`) gets its ERROR body as a Blob too, so
 * the server's `detail` would never be read: decode a JSON one back into an
 * object before anything looks at it.  A body that is not JSON stays as it was.
 */
async function decodeBlobErrorBody(error: { response?: { data?: unknown } }): Promise<void> {
  const data = error.response?.data;
  if (!(data instanceof Blob) || !/json/i.test(data.type)) return;
  try {
    error.response!.data = JSON.parse(await blobText(data));
  } catch {
    // Not readable as JSON: leave the Blob; callers fall back to the status.
  }
}

// Add response interceptor to handle authentication errors
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    await decodeBlobErrorBody(error);
    handleAuthFailure(error.response?.status, error.response?.data?.detail, tokenSentWith(error.config));
    return Promise.reject(error);
  }
);

// --- The remembered project selection ---
// Which project the reader last chose, kept across reloads (localStorage).
// It is ONLY that: no request reads it (5.353.0).  A request is addressed by
// the project its caller passes — see `projectPath` below.
let _currentProjectId: number | null = null;

export function setCurrentProjectId(id: number | null) {
  _currentProjectId = id;
  if (id !== null) {
    localStorage.setItem('current_project_id', String(id));
  } else {
    localStorage.removeItem('current_project_id');
  }
}

export function getCurrentProjectId(): number | null {
  if (_currentProjectId !== null) return _currentProjectId;
  const stored = localStorage.getItem('current_project_id');
  if (stored) {
    _currentProjectId = parseInt(stored, 10);
    return _currentProjectId;
  }
  return null;
}

/**
 * The address prefix of ONE project's data (5.353.0).  Every project-scoped
 * API function takes the project as its FIRST argument and builds its address
 * with this — the project a request goes to is the one its caller named when
 * it rendered, never "whichever is current when the request happens to be
 * built" (the code review of 2026-10-09: a second request made after an
 * `await` went to the project the reader had switched to).
 * `0` / null is "no project selected" (`hooks/useProjectId` gives 0 then).
 */
export function projectPath(projectId: number | null | undefined): string {
  if (!projectId) throw new Error('No project selected');
  return `/projects/${projectId}`;
}
