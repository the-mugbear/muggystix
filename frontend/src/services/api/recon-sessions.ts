/**
 * JWT-facing recon-session detail client (v3 alpha.6 backend).
 *
 * Drives the Recon Run Detail page (alpha.6 UI).  The agent-facing
 * ``/agent/recon/*`` endpoints are API-key-authed and aren't used
 * here — they're a different surface for a different consumer.
 *
 * See ``backend/app/api/v1/endpoints/recon_sessions.py``.
 */
import { api, p } from './client';


export interface ReconSessionRow {
  id: number;
  project_id: number;
  scope_id: number;
  scope_name?: string | null;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  started_by_username?: string | null;
  agent_name?: string | null;
  generated_by_model?: string | null;
  generated_by_tool?: string | null;
  prompt_version?: string | null;
  uploads_submitted: number;
  scans_ingested: number;
  hosts_discovered: number;
  ports_discovered: number;
  /** Timestamp of the most recent agent API call against this recon
   *  session (from the agent_api_calls audit log), or null when the
   *  agent has never called in.  Mirrors the execution-session field
   *  of the same name. */
  last_activity_at?: string | null;
  /** Server-side "looks interrupted" judgment.  True when status is
   *  `active` AND has been silent for the 15-minute threshold —
   *  computed against `max(last_activity_at, started_at)` so a
   *  long-running but actively-calling session does NOT fire it. */
  is_stale?: boolean;
}


export interface ReconUploadRow {
  job_id: number;
  filename: string;
  status: string;
  scan_id?: number | null;
  created_at?: string | null;
  completed_at?: string | null;
  skipped_count: number;
  parser_warnings?: string | null;
  last_error?: string | null;
}


export interface ReconHostRow {
  host_id: number;
  ip_address: string;
  hostname?: string | null;
  open_port_count: number;
  open_ports: number[];
  services: string[];
}


// ─── v2.52.0 — aggregate stats replace the per-host array on the
// default detail-page payload.  See ReconSessionDetail below for the
// opt-in (?include_hosts=true) full-list path.
export interface ReconToolBreakdown {
  tool_name: string;
  scan_count: number;
  host_count: number;
  port_count: number;
}

export interface ReconServiceBreakdown {
  service_name: string;
  host_count: number;
}

export interface ReconPortBreakdown {
  port_number: number;
  protocol: string;
  host_count: number;
}

export interface ReconHostStats {
  host_count: number;
  host_count_with_open_ports: number;
  by_tool: ReconToolBreakdown[];
  top_services: ReconServiceBreakdown[];
  top_open_ports: ReconPortBreakdown[];
}


// ─── v2.52.0 diff endpoint.  Capped IP set difference between two
// recon sessions in the same project.
export interface ReconDiffHostRow {
  host_id: number;
  ip_address: string;
  hostname?: string | null;
}

export interface ReconSessionDiff {
  session_a_id: number;
  session_b_id: number;
  stats_a: ReconHostStats;
  stats_b: ReconHostStats;
  in_a_not_b_count: number;
  in_b_not_a_count: number;
  shared_count: number;
  in_a_not_b_sample: ReconDiffHostRow[];
  in_b_not_a_sample: ReconDiffHostRow[];
  limit: number;
}


export interface ReconPlanLink {
  plan_id: number;
  title: string;
  status: string;
  version: number;
  entry_count: number;
  created_at: string;
  generated_by_model?: string | null;
}


export interface ReconToolStatus {
  name: string;
  status: 'ok' | 'warn' | 'missing' | string;
  issue?: string | null;
}

export interface ReconEnvironmentSnapshot {
  probed_at?: string | null;
  probed_from_ip?: string | null;
  os_family?: string | null;
  os_release?: string | null;
  shell?: string | null;
  arch?: string | null;
  python?: string | null;
  notes?: string | null;
  tools_status: ReconToolStatus[];
  raw?: Record<string, unknown> | null;
}

export interface ReconSessionDetail {
  summary: ReconSessionRow;
  uploads: ReconUploadRow[];
  /** v2.87.0 — uploads/plans paginated.  ``uploads_total`` is the
   *  full count even when the array is a capped slice; the page
   *  uses it for the "showing N of T" caption and the Load more
   *  affordance.  Pre-v2.87.0 detail responses didn't carry these
   *  fields — guard reads with ``?? uploads.length`` if you need
   *  to fall back. */
  uploads_total: number;
  uploads_skip: number;
  uploads_limit: number;
  /** v2.52.0 — aggregate rollup that always rides on the response.
   *  This is the surface the Recon Run Detail page renders; the
   *  ``hosts`` array below is only populated when the request asks
   *  for it explicitly (it can be tens of MB on real sessions). */
  host_stats: ReconHostStats;
  /** v2.52.0 — opt-in via ``getReconSession(id, { includeHosts: true })``.
   *  Empty array when not requested.  The diff-fallback path and the
   *  occasional debug tool are the only consumers. */
  hosts: ReconHostRow[];
  plans_generated: ReconPlanLink[];
  plans_total: number;
  plans_skip: number;
  plans_limit: number;
  /** v2.87.0 — every scan_id this session produced, independent of
   *  the paginated uploads slice.  Drives the "View N hosts in
   *  Inventory" deep-link so it stays correct as the user pages
   *  through uploads. */
  all_scan_ids: number[];
  environment?: ReconEnvironmentSnapshot | null;
}


export interface ReconSessionListFilters {
  status?: string;
  scope_id?: number;
  search?: string;
  skip?: number;
  limit?: number;
}

export interface ReconSessionListResult {
  items: ReconSessionRow[];
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}

/** v2.86.10 — replaced the bare-array return with {items, total}; total
 *  was read from the ``X-Total-Count`` response header.
 *  v2.86.13 — the endpoint now returns the standard
 *  ``Paginated[ReconSessionRow]`` envelope
 *  (``{items, total, skip, limit, has_more}``).  The wrapper just
 *  passes the body through.  ``has_more`` is server-computed so
 *  pages no longer have to do ``loaded < total`` math themselves. */
export const listReconSessions = async (
  filters: ReconSessionListFilters = {},
): Promise<ReconSessionListResult> => {
  const response = await api.get<ReconSessionListResult>(
    `${p()}/recon-sessions/`,
    { params: filters },
  );
  return response.data;
};


export interface GetReconSessionOptions {
  /** Set true to also load the full per-host array (v2.52.0+).  Off
   *  by default — the Recon Run Detail page reads ``host_stats``
   *  instead.  Only set this when you actually need to iterate hosts
   *  client-side (debug tools, the diff-fallback path). */
  includeHosts?: boolean;
  /** v2.87.0 — pagination for the uploads and plans_generated child
   *  lists.  Server defaults: limit=50 / max=500.  Omit on the
   *  initial fetch; pass on Load more clicks with skip = currently
   *  loaded length. */
  uploadsSkip?: number;
  uploadsLimit?: number;
  plansSkip?: number;
  plansLimit?: number;
}

export const getReconSession = async (
  sessionId: number,
  options: GetReconSessionOptions = {},
): Promise<ReconSessionDetail> => {
  const params: Record<string, string | number | boolean> = {};
  if (options.includeHosts) params.include_hosts = true;
  if (options.uploadsSkip !== undefined) params.uploads_skip = options.uploadsSkip;
  if (options.uploadsLimit !== undefined) params.uploads_limit = options.uploadsLimit;
  if (options.plansSkip !== undefined) params.plans_skip = options.plansSkip;
  if (options.plansLimit !== undefined) params.plans_limit = options.plansLimit;
  const response = await api.get<ReconSessionDetail>(
    `${p()}/recon-sessions/${sessionId}`,
    { params: Object.keys(params).length > 0 ? params : undefined },
  );
  return response.data;
};


/**
 * Pairwise diff between two recon sessions in the same project
 * (v2.52.0 backend).  Cheap regardless of host count — the server
 * does the IP set difference in SQL and returns capped samples plus
 * the full counts.
 */
export const diffReconSessions = async (
  sessionAId: number,
  sessionBId: number,
  limit = 50,
): Promise<ReconSessionDiff> => {
  const response = await api.get<ReconSessionDiff>(
    `${p()}/recon-sessions/${sessionAId}/diff/${sessionBId}`,
    { params: { limit } },
  );
  return response.data;
};


/**
 * Operator-driven completion path for stuck recon sessions (v2.36.0
 * backend).  Used when the terminal-side agent never called
 * /agent/recon/complete — e.g. the agent process died, the user
 * killed the terminal, or the agent forgot.  Without this, the
 * session row sits at status='active' forever and the agent rail
 * keeps surfacing it as live.
 *
 * Requires the analyst role on the project.  Returns 409 if the
 * session is already in a terminal state (so accidental double-
 * abandons don't silently rewrite metadata).
 */
export const abandonReconSession = async (
  sessionId: number,
  reason?: string,
): Promise<ReconSessionRow> => {
  const response = await api.post<ReconSessionRow>(
    `${p()}/recon-sessions/${sessionId}/abandon`,
    { reason: reason?.trim() || null },
  );
  return response.data;
};
