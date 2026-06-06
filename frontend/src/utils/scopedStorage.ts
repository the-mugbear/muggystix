/**
 * Project- and user-scoped storage key helpers.
 *
 * Several pieces of persisted UI state (host filters, the dashboard
 * "new scans since last visit" cursor) were stored under flat global
 * keys.  That let state from one project — or one user on a shared
 * browser — bleed into another, making the destination context look
 * broken or empty for no visible reason.
 *
 * Namespacing the storage key by the current user and/or project makes
 * that bleed structurally impossible: each context gets its own bucket,
 * and switching back restores exactly the right state.  Stale buckets
 * for other contexts simply sit unused — they never leak.
 *
 * These return the *key* (not a storage wrapper) so callers keep using
 * localStorage/sessionStorage directly and only the key changes.
 */
import { getCurrentProjectId } from '../services/api';

/** The current user's id from the persisted auth_user blob, or 'anon'. */
function currentUserId(): string {
  try {
    const raw = localStorage.getItem('auth_user');
    if (!raw) return 'anon';
    const parsed = JSON.parse(raw) as { id?: number | string };
    return parsed?.id != null ? String(parsed.id) : 'anon';
  } catch {
    return 'anon';
  }
}

/**
 * `${base}:u<userId>` — isolated per user, stable across project switches.
 * Use for user preferences that should not vary by project.
 */
export function userScopedKey(base: string): string {
  return `${base}:u${currentUserId()}`;
}

/**
 * `${base}:u<userId>:p<projectId>` — isolated per user *and* project.
 * Use for any persisted state that describes a specific project's data
 * (filters, freshness cursors, per-project view state).  Falls back to
 * `p<none>` when no project is selected yet.
 */
export function projectScopedKey(base: string): string {
  const pid = getCurrentProjectId();
  return `${base}:u${currentUserId()}:p${pid ?? 'none'}`;
}
