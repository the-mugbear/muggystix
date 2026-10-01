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

/** `evidence` (5.320.0) is the per-command, per-target record that answers
 *  "was this signature against this host at this time ours?": one command an
 *  agent recorded, with its tool, the address it reached and the outcome.
 *  Until 5.320.0 that record was a test plan's execution result or a target
 *  probe, and execution runs were a kind too. */
export type ActivityKind =
  | 'scan'
  | 'evidence';

export interface ActivityItem {
  kind: ActivityKind;
  /** The scan's id, or the evidence record's. */
  ref_id: number;
  project_id: number;
  project_name: string;
  /** Human-readable primary label: the tool. */
  label: string;
  /** Optional secondary string for tooltip / detail (command line, notes, mode). */
  secondary_label: string | null;
  start_time: string; // ISO
  end_time: string | null;
  /**
   * v2.60.0 — `Scan.created_at` for scans only (the row's ingestion
   * time).  Always populated for scans; null for evidence.
   */
  recorded_time: string | null;
  /**
   * v2.61.0 — true iff `start_time` is the `created_at` fallback
   * because the scanner didn't write one (some `.txt` exports, bare
   * masscan list output).  The UI uses this to badge the timestamp
   * so the analyst doesn't read upload time as execution time.
   * False for scans with a real scanner timestamp and for evidence.
   */
  start_time_is_fallback: boolean;
  /**
   * True iff the row recorded an end_time.  Used by the UI to badge
   * "no end_time recorded" — NULL end_time is treated as a
   * single-instant event at start_time.
   */
  has_end_time: boolean;
  /** Host count for scans; null for evidence (one host, in `target`). */
  host_count: number | null;
  /** An evidence record's outcome (finding, no_finding, inconclusive,
   *  failed, info); null for scans. */
  status: string | null;
  /** v5.213.0 — the IP this row acted on when it is one (evidence); null
   *  for scans, which cover many. */
  target: string | null;
  /** For evidence, the agent session that recorded it (`ref_id` is the
   *  record's own id); the deep link goes to the session. */
  parent_id: number | null;
}

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
  /** Optional list of activity kinds to include.  Omit for all. */
  kinds?: ActivityKind[];
  /** v5.213.0 — attribution filters: tool name / command substring
   *  (case-insensitive) and one target IP. */
  tool?: string;
  target?: string;
}

export interface ScansBetweenParams {
  from: string;
  to: string;
  projectIds?: number[];
  kinds?: ActivityKind[];
  tool?: string;
  target?: string;
}

function appendAttribution(
  search: URLSearchParams,
  params: { projectIds?: number[]; kinds?: ActivityKind[]; tool?: string; target?: string },
): void {
  if (params.projectIds && params.projectIds.length > 0) {
    search.set('project_ids', params.projectIds.join(','));
  }
  if (params.kinds && params.kinds.length > 0) {
    search.set('kinds', params.kinds.join(','));
  }
  if (params.tool && params.tool.trim()) search.set('tool', params.tool.trim());
  if (params.target && params.target.trim()) search.set('target', params.target.trim());
}

export async function getScansAt(params: ScansAtParams): Promise<ActivityResponse> {
  const search = new URLSearchParams();
  search.set('ts', params.ts);
  if (params.toleranceSeconds !== undefined) {
    search.set('tolerance_seconds', String(params.toleranceSeconds));
  }
  appendAttribution(search, params);
  const { data } = await api.get<ActivityResponse>(
    `/activity/scans-at?${search.toString()}`,
  );
  return data;
}

export async function getScansBetween(params: ScansBetweenParams): Promise<ActivityResponse> {
  const search = new URLSearchParams();
  search.set('from', params.from);
  search.set('to', params.to);
  appendAttribution(search, params);
  const { data } = await api.get<ActivityResponse>(
    `/activity/scans-between?${search.toString()}`,
  );
  return data;
}
