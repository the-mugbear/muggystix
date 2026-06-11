/**
 * Axios client + project-scoping helpers.
 *
 * v2.29.0 — extracted from the monolithic ``services/api.ts``.  The
 * configured axios instance and the ``p()`` project-prefix helper
 * are used by every submodule under ``services/api/``.  ``api.ts``
 * itself is now a barrel re-exporting from those submodules so
 * consumers can keep importing from ``../services/api`` unchanged.
 */
import axios from 'axios';

import { getApiBaseUrl } from '../../utils/apiUrl';

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

// Add response interceptor to handle authentication errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Token is invalid or expired
      localStorage.removeItem('auth_token');
      localStorage.removeItem('auth_user');
      // Redirect to login page (guard against infinite loop if already on /login)
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    // Backend returns 403 with detail "password_change_required" when the
    // user's must_change_password flag is set.  Redirect to the forced
    // password-change page so the user can't do anything else first.
    if (
      error.response?.status === 403 &&
      error.response?.data?.detail === 'password_change_required' &&
      window.location.pathname !== '/force-change-password'
    ) {
      window.location.href = '/force-change-password';
    }
    // Mandatory 2FA (REQUIRE_2FA): the gate returns this until the user
    // enrolls.  Force them to the 2FA setup page so nothing else is reachable.
    if (
      error.response?.status === 403 &&
      error.response?.data?.detail === 'two_factor_setup_required' &&
      window.location.pathname !== '/force-2fa-setup'
    ) {
      window.location.href = '/force-2fa-setup';
    }
    return Promise.reject(error);
  }
);

// --- Project scoping ---
// All data endpoints require a project_id prefix.
// Call setCurrentProjectId() when the user selects a project.
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

/** Throws if no project is selected.  Submodules call ``p()`` inline
 *  to build the ``/projects/{id}`` prefix.  Kept internal (not
 *  re-exported from the barrel) — consumers should call the typed
 *  wrappers, not assemble URLs by hand. */
export function p(): string {
  const id = getCurrentProjectId();
  if (!id) throw new Error('No project selected');
  return `/projects/${id}`;
}
