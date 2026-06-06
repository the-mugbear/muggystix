/**
 * Cross-project SOC-correlation activity surface.
 *
 * Pairs with backend/app/api/v1/endpoints/activity.py (v2.56.0).
 * Top-level (not project-scoped) because the use case is "across
 * every project the analyst can see".  The backend computes the
 * caller's accessible-projects set from ProjectMembership and
 * intersects it with the optional `projectIds` filter — clients can
 * safely pass any ids; the server silently drops ones the user
 * can't see (no project-existence leak).
 */
import { api } from './client';

export type ActivityKind = 'scan' | 'recon_session' | 'execution_session';

export interface ActivityItem {
  kind: ActivityKind;
  /** scan_id for scans, session_id for recon/execution sessions. */
  ref_id: number;
  project_id: number;
  project_name: string;
  /** Human-readable primary label (tool / scope / plan). */
  label: string;
  /** Optional secondary string for tooltip / detail (command line, notes, mode). */
  secondary_label: string | null;
  start_time: string; // ISO
  end_time: string | null;
  /**
   * v2.60.0 — `Scan.created_at` for scans only (the row's ingestion
   * time).  Always populated for scans; null for recon / execution
   * sessions (which don't carry a separate ingestion timestamp —
   * `start_time` already is the row-creation moment for those).
   */
  recorded_time: string | null;
  /**
   * v2.61.0 — true iff `start_time` is the `created_at` fallback
   * because the scanner didn't write one (some `.txt` exports, bare
   * masscan list output).  The UI uses this to badge the timestamp
   * so the analyst doesn't read upload time as execution time.
   * False for scans with a real scanner timestamp and for sessions.
   */
  start_time_is_fallback: boolean;
  /**
   * True iff the row recorded an end_time.  Used by the UI to badge
   * "no end_time recorded" — NULL end_time is treated as a
   * single-instant event at start_time.
   */
  has_end_time: boolean;
  /** Host count for scan + recon_session; null for execution_session. */
  host_count: number | null;
  /** Status string for recon/execution sessions; null for scans. */
  status: string | null;
}

/** Legacy alias kept temporarily for callers that still use v1's
 *  scan-only name.  Same shape. */
export type ActivityScanItem = ActivityItem;

export interface ActivityResponse {
  items: ActivityItem[];
  total: number;
  truncated: boolean;
  accessible_project_ids: number[];
  requested_project_ids: number[] | null;
  window_start: string; // ISO
  window_end: string; // ISO
}

export interface ScansAtParams {
  /** ISO timestamp.  Naive (no timezone) treated as UTC by the server. */
  ts: string;
  /** Default 300 (5 min).  Server cap is 3600s (1h). */
  toleranceSeconds?: number;
  /** Optional list of project ids to narrow the query. */
  projectIds?: number[];
  /** Optional list of activity kinds to include.  Omit for all three. */
  kinds?: ActivityKind[];
}

export interface ScansBetweenParams {
  from: string;
  to: string;
  projectIds?: number[];
  kinds?: ActivityKind[];
}

export async function getScansAt(params: ScansAtParams): Promise<ActivityResponse> {
  const search = new URLSearchParams();
  search.set('ts', params.ts);
  if (params.toleranceSeconds !== undefined) {
    search.set('tolerance_seconds', String(params.toleranceSeconds));
  }
  if (params.projectIds && params.projectIds.length > 0) {
    search.set('project_ids', params.projectIds.join(','));
  }
  if (params.kinds && params.kinds.length > 0) {
    search.set('kinds', params.kinds.join(','));
  }
  const { data } = await api.get<ActivityResponse>(
    `/activity/scans-at?${search.toString()}`,
  );
  return data;
}

export async function getScansBetween(params: ScansBetweenParams): Promise<ActivityResponse> {
  const search = new URLSearchParams();
  search.set('from', params.from);
  search.set('to', params.to);
  if (params.projectIds && params.projectIds.length > 0) {
    search.set('project_ids', params.projectIds.join(','));
  }
  if (params.kinds && params.kinds.length > 0) {
    search.set('kinds', params.kinds.join(','));
  }
  const { data } = await api.get<ActivityResponse>(
    `/activity/scans-between?${search.toString()}`,
  );
  return data;
}
