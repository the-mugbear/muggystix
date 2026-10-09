/**
 * Barrel for the per-domain API submodules.
 *
 * v2.29.0 — the previous 2200-line monolith was split along domain
 * lines into ``services/api/{client,projects,scans,hosts,...}``.
 * Every consumer in the codebase still imports from
 * ``../services/api`` (this file), so the split is invisible to
 * page code.
 *
 * Add a new domain by:
 *   1. Creating ``services/api/<domain>.ts`` (use any sibling as a
 *      template — they all import ``api`` and, when project-scoped,
 *      ``projectPath`` from ``./client``).
 *   2. Re-exporting it from this barrel.
 *   3. New domain's types are then visible to every consumer.
 *
 * A project-scoped function takes ``projectId: number`` as its FIRST
 * argument and builds its address with ``projectPath(projectId)``
 * (5.353.0).  No request reads the "current project":
 * ``getCurrentProjectId`` / ``setCurrentProjectId`` are only the
 * remembered selection.
 *
 * NOTE: the axios instance is still the barrel's default export, for
 * ONE importer: ``contexts/AuthContext`` (sign-in, sign-out and
 * session renewal, and ``verifyToken``, which reads the profile
 * outside a query).
 * Nothing else hand-rolls a request — a new call is a named function
 * in a submodule (``api/users.ts`` and ``api/auth.ts`` hold the
 * account and sign-in ones), called from a ``queryFn`` / ``mutationFn``.
 */
import { api, projectPath, setCurrentProjectId, getCurrentProjectId } from './api/client';
import { serializeHostParams } from './api/hosts';
import { asAxiosError } from '../utils/apiErrors';
import { filenameFromContentDisposition, saveBlob } from '../utils/download';
import type { Paginated } from './api/shared';  // local use; also re-exported via the barrel below

// --- Core: axios instance + the remembered project selection ---
export { api, setCurrentProjectId, getCurrentProjectId };

// --- Per-domain submodules.  Order doesn't matter; tsc resolves the
//     final flat namespace.  Keep this list alphabetically organised.
export * from './api/activity';
export * from './api/agent-sessions';
// './api/agents' removed in v5.182.0 with the AI Agents card — the project
// agent row is auto-provisioned by every workflow and managed through session
// lifecycle, so nothing in the UI called it. The /agents/ endpoints remain
// server-side for scripts.
export * from './api/assist';
export * from './api/auth';
export * from './api/client-reports';
export * from './api/coverage';
export * from './api/dashboard';
export * from './api/feedback';
export * from './api/findings';
export * from './api/hosts';
export * from './api/insights';
export * from './api/integrations';
export * from './api/llm-providers';
export * from './api/names';
export * from './api/notifications';
export * from './api/oversight';
export * from './api/parse-errors';
export * from './api/portfolio';
export * from './api/posture';
export * from './api/projects';
export * from './api/proposals';
export * from './api/references';
export * from './api/remediation';
export * from './api/report-writing-guidance';
export * from './api/scans';
export * from './api/scopes';
export * from './api/shared';
export * from './api/sites';
export * from './api/system';
export * from './api/host-tests';
export * from './api/agent-activity';
export * from './api/uploads';
export * from './api/users';

export interface DNSRecord {
  id: number;
  domain: string;
  record_type: string;
  value: string;
  ttl: number | null;
  resolver_name?: string | null;
  created_at: string;
  updated_at: string | null;
}

// DNS records produced by a scan (e.g. dnsx).  Only A/AAAA answers create
// host rows, so CNAME/MX/NS/TXT records are otherwise invisible — this lists
// the full answer set for a scan.  Returns a Paginated envelope so the UI can
// show the TRUE total (CR5-C3); we request up to the server max in one page
// (the tab is opt-in and most dnsx scans are far smaller) and the envelope's
// `total`/`has_more` let the UI flag the rare truncation.  Empty page on
// older deployments without the endpoint.
const DNS_RECORDS_PAGE = 2000;
export const getScanDnsRecords = async (projectId: number, scanId: number, signal?: AbortSignal): Promise<Paginated<DNSRecord>> => {
  try {
    const response = await api.get(`${projectPath(projectId)}/scans/${scanId}/dns-records`, {
      params: { skip: 0, limit: DNS_RECORDS_PAGE }, signal,
    });
    return response.data;
  } catch (error) {
    if (asAxiosError(error)?.response?.status === 404) {
      return { items: [], total: 0, skip: 0, limit: DNS_RECORDS_PAGE, has_more: false };
    }
    throw error;
  }
};

// Hosts as a scan OBSERVED them (v5.184.0).  Distinct from getHostsByScan,
// which returns the hosts' CURRENT records — every field here is read from the
// observation tables, so the response doesn't change when a later scan runs or
// a port is remediated.  There is deliberately no OS: the schema records only
// whether a scan touched the OS, not what it said, so an as-scanned OS cannot
// be reconstructed and printing today's value under a historical heading would
// be the bug this endpoint exists to fix.
export interface ScanPortSnapshot {
  port_number: number;
  protocol: string | null;
  state_at_scan: string | null;
  service_name: string | null;
}

export interface ScanHostSnapshot {
  host_id: number;
  ip_address: string;
  hostname_at_scan: string | null;
  state_at_scan: string | null;
  host_created: boolean;
  /** Did this scan authenticate to the host? true / false when the scanner
   *  said so (Nessus); null or absent when it did not say — never "no". */
  credentialed?: boolean | null;
  observed_port_count: number;
  open_port_count: number;
  ports: ScanPortSnapshot[];
}

const SNAPSHOT_PAGE = 1000;
export const getScanHostSnapshots = async (
  projectId: number,
  scanId: number,
  signal?: AbortSignal,
): Promise<Paginated<ScanHostSnapshot>> => {
  try {
    const response = await api.get(`${projectPath(projectId)}/scans/${scanId}/host-snapshots`, {
      params: { skip: 0, limit: SNAPSHOT_PAGE }, signal,
    });
    return response.data;
  } catch (error) {
    // A deployment mid-upgrade has the page but not the route yet; the caller
    // falls back to the current-inventory view rather than erroring the tab.
    if (asAxiosError(error)?.response?.status === 404) {
      return { items: [], total: 0, skip: 0, limit: SNAPSHOT_PAGE, has_more: false };
    }
    throw error;
  }
};

// --- Saved Hosts page filter views (per-user, per-project) ---

export interface ProjectMember {
  id: number;
  project_id: number;
  user_id: number;
  username?: string | null;
  full_name?: string | null;
  role: string;
  created_at: string;
}

export const listProjectMembers = async (projectId: number, signal?: AbortSignal): Promise<ProjectMember[]> => {
  const response = await api.get(`${projectPath(projectId)}/members`, { signal });
  return response.data;
};

// --- Cross-project member management (SoC manager / Portfolio) ---
// The Portfolio views and manages any project's roster with the same
// functions: `listProjectMembers(projectId)` above is the ONE roster read
// (5.353.1 — `getProjectMembers` asked the same address under a second name
// and a second key), and the writes below name their project.

export interface UserDirectoryEntry {
  id: number;
  username: string;
  full_name?: string | null;
  email?: string | null;
}

export const getUserDirectory = async (signal?: AbortSignal): Promise<UserDirectoryEntry[]> => {
  const response = await api.get('/users/directory', { signal });
  return response.data;
};

export const addProjectMember = async (
  projectId: number, userId: number, role: string,
): Promise<ProjectMember> => {
  const response = await api.post(`/projects/${projectId}/members`, { user_id: userId, role });
  return response.data;
};

export const updateProjectMemberRole = async (
  projectId: number, userId: number, role: string,
): Promise<ProjectMember> => {
  const response = await api.put(`/projects/${projectId}/members/${userId}`, { role });
  return response.data;
};

export const removeProjectMember = async (
  projectId: number, userId: number,
): Promise<void> => {
  await api.delete(`/projects/${projectId}/members/${userId}`);
};

// ---------------------------------------------------------------------------
// Outbound webhooks (v2.73.0)
// ---------------------------------------------------------------------------

export interface WebhookEventType {
  key: string;
  description: string;
}

export interface WebhookConfig {
  id: number;
  project_id: number;
  name: string;
  url: string;
  has_secret: boolean;
  events: string[];
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface WebhookCreatePayload {
  name: string;
  url: string;
  secret?: string | null;
  events?: string[];
  is_active?: boolean;
}

export interface WebhookTestResult {
  ok: boolean;
  status_code?: number;
  error?: string;
}

export const listWebhookEventTypes = async (projectId: number, signal?: AbortSignal): Promise<WebhookEventType[]> => {
  const response = await api.get(`${projectPath(projectId)}/webhooks/event-types`, { signal });
  return response.data;
};

export const listWebhooks = async (projectId: number, signal?: AbortSignal): Promise<WebhookConfig[]> => {
  const response = await api.get(`${projectPath(projectId)}/webhooks`, { signal });
  return response.data;
};

export const createWebhook = async (projectId: number, payload: WebhookCreatePayload): Promise<WebhookConfig> => {
  const response = await api.post(`${projectPath(projectId)}/webhooks`, payload);
  return response.data;
};

export const updateWebhook = async (
  projectId: number,
  id: number,
  payload: Partial<WebhookCreatePayload>,
): Promise<WebhookConfig> => {
  const response = await api.patch(`${projectPath(projectId)}/webhooks/${id}`, payload);
  return response.data;
};

export const deleteWebhook = async (projectId: number, id: number): Promise<void> => {
  await api.delete(`${projectPath(projectId)}/webhooks/${id}`);
};

export const testWebhook = async (projectId: number, id: number): Promise<WebhookTestResult> => {
  const response = await api.post(`${projectPath(projectId)}/webhooks/${id}/test`);
  return response.data;
};

// ---------------------------------------------------------------------------
// Webhook delivery outbox (v2.243.0)
// ---------------------------------------------------------------------------
//
// The delivery table has existed since the outbox landed; nothing rendered it,
// so a webhook that silently stopped delivering looked identical to one with
// nothing to say. These back the Deliveries panel in Project Settings.

export interface WebhookDeliveryRow {
  id: number;
  webhook_config_id: number | null;
  webhook_name: string | null;
  event: string;
  status: string;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  response_status: number | null;
  next_attempt_at?: string | null;
  created_at?: string | null;
  delivered_at?: string | null;
}

export const listWebhookDeliveries = async (
  projectId: number,
  params: { status?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<WebhookDeliveryRow[]> => {
  const response = await api.get(`${projectPath(projectId)}/webhooks/deliveries`, { params, signal });
  return response.data;
};

export const retryWebhookDelivery = async (projectId: number, id: number): Promise<WebhookDeliveryRow> => {
  const response = await api.post(`${projectPath(projectId)}/webhooks/deliveries/${id}/retry`);
  return response.data;
};

// ---------------------------------------------------------------------------
// Audit log (v2.243.0) — admin-only, deployment-wide (NOT project-scoped)
// ---------------------------------------------------------------------------

export interface AuditLogRow {
  id: number;
  user_id: number | null;
  /** The actor, resolved server-side (null: no user, or a deleted account). */
  user_username?: string | null;
  user_full_name?: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  // The backend `audit_logs.details` column is JSON — it arrives as an object
  // for structured events (e.g. login: {"method":"totp"}) and a string/null
  // otherwise. Typed accurately so callers coerce it before rendering.
  details: string | Record<string, unknown> | null;
  success: boolean;
  error_message: string | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at?: string | null;
}

export interface AuditLogPage {
  logs: AuditLogRow[];
  total: number;
  skip: number;
  limit: number;
}

export const listAuditLogs = async (
  params: {
    skip?: number;
    limit?: number;
    action?: string;
    resource_type?: string;
    user_id?: number;
  } = {},
  signal?: AbortSignal,
): Promise<AuditLogPage> => {
  const response = await api.get('/audit/logs', { params, signal });
  return response.data;
};

export interface AuditStats {
  total_logs: number;
  successful_logs: number;
  failed_logs: number;
  /** Events in the rolling last 24 hours (the backend's field name). */
  recent_logs_24h: number;
  top_actions: Array<{ action: string; count: number }>;
  top_users: Array<{
    user_id: number | null;
    user_username?: string | null;
    user_full_name?: string | null;
    count: number;
  }>;
}

export const getAuditStats = async (signal?: AbortSignal): Promise<AuditStats> => {
  const response = await api.get('/audit/stats', { signal });
  return response.data;
};

export interface CommandExplanation {
  has_command: boolean;
  tool: string;
  command?: string;
  target?: string;
  scan_type?: string;
  summary?: string;
  risk_assessment?: string;
  message?: string;
  arguments?: Array<{
    arg: string;
    description: string;
    category: string;
    risk_level: string;
    examples: string[];
  }>;
}

export const getScanCommandExplanation = async (projectId: number, scanId: number, signal?: AbortSignal): Promise<CommandExplanation> => {
  const response = await api.get(`${projectPath(projectId)}/scans/${scanId}/command-explanation`, { signal });
  return response.data;
};

// Parse Error API functions — only the singular fetch is wired up to
// the UI today; the list/stats/update/delete wrappers were removed in
// the cleanup pass after months of zero consumers.  Re-add when a
// --- The host inventory downloads (Hosts → "Download inventory") -------------
// Two files, both of every host matching the list's filters, neither capped:
// the CSV streams from the API and saves at once; the JSON carries each
// host's full record, so the report worker writes it — see
// enqueueInventoryJson.

/** The full host-filter context (whatever buildHostQueryContext produced),
 *  including array filters like orgs/asns/countries — so a download honours
 *  the same filters the list shows. */
export type InventoryFilters = Record<string, string | number | boolean | string[] | undefined>;

/** One row per host, as CSV: streamed, then saved. */
export const downloadInventoryCsv = async (projectId: number, filters: InventoryFilters): Promise<void> => {
  const queryParams = new URLSearchParams(serializeHostParams(filters));
  const response = await api.get(`${projectPath(projectId)}/reports/hosts/csv?${queryParams}`, {
    responseType: 'blob'
  });
  saveBlob(new Blob([response.data]), filenameFromContentDisposition(
    response.headers['content-disposition'] as string | undefined,
    `hosts_inventory_${new Date().toISOString().split('T')[0]}.csv`,
  ));
};

// --- Report jobs --------------------------------------------------------------
// Work the report worker does off the request: the inventory JSON, and a
// client report's preview or render (queued by the Reports page's own routes).
// Queue, poll the job's status, then download its file.

export interface ReportJob {
  id: number;
  project_id: number;
  format: string;
  report_type: string;
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled';
  message?: string | null;
  error_message?: string | null;
  result_filename?: string | null;
  media_type?: string | null;
  file_size?: number | null;
  retry_count?: number | null;
  last_error?: string | null;
  last_heartbeat?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  expires_at?: string | null;
  dismissed_at?: string | null;
  /** Who asked for it — retry / cancel / dismiss are the requester's, or a
   *  project analyst's.  Absent when the server does not send it. */
  requested_by_id?: number | null;
}

/** Queue the inventory JSON: every matching host's full record (ports,
 *  scanner observations, findings, tests, notes), followed by the project's
 *  findings and site / subnet / systemic roll-ups.  Returns the queued job. */
export const enqueueInventoryJson = async (projectId: number, filters: InventoryFilters): Promise<ReportJob> => {
  const query = new URLSearchParams(serializeHostParams(filters));
  query.set('format', 'json');
  const response = await api.post(`${projectPath(projectId)}/reports/jobs?${query}`);
  return response.data as ReportJob;
};

export const getReportJob = async (projectId: number, jobId: number, signal?: AbortSignal): Promise<ReportJob> => {
  const response = await api.get(`${projectPath(projectId)}/reports/jobs/${jobId}`, { signal });
  return response.data as ReportJob;
};

export const downloadReportJob = async (projectId: number, jobId: number): Promise<void> => {
  const response = await api.get(`${projectPath(projectId)}/reports/jobs/${jobId}/download`, { responseType: 'blob' });
  saveBlob(new Blob([response.data]), filenameFromContentDisposition(
    response.headers['content-disposition'] as string | undefined, `report_${jobId}`,
  ));
};

export const listReportJobs = async (projectId: number, limit = 20, signal?: AbortSignal): Promise<ReportJob[]> => {
  const response = await api.get(`${projectPath(projectId)}/reports/jobs?limit=${limit}`, { signal });
  return response.data as ReportJob[];
};

export const dismissReportJob = async (projectId: number, jobId: number): Promise<ReportJob> => {
  const response = await api.post(`${projectPath(projectId)}/reports/jobs/${jobId}/dismiss`);
  return response.data as ReportJob;
};

// Re-queue a failed report job (409 if it isn't in a failed state).
export const retryReportJob = async (projectId: number, jobId: number): Promise<ReportJob> => {
  const response = await api.post(`${projectPath(projectId)}/reports/jobs/${jobId}/retry`);
  return response.data as ReportJob;
};

// Cancel a queued report job before the worker claims it (409 if already
// processing or terminal).
export const cancelReportJob = async (projectId: number, jobId: number): Promise<ReportJob> => {
  const response = await api.post(`${projectPath(projectId)}/reports/jobs/${jobId}/cancel`);
  return response.data as ReportJob;
};

// --- AI-drafted narrative report (beta) --------------------------------------
// Asks a configured LLM provider to draft a markdown report from the project's
// promoted findings. The operator edits the returned draft — the AI never owns
// the final text. Errors: 400 (user-fixable: no provider / no findings — the
// reason is in `detail`), 502 (provider failure — generic `detail`).

export interface DraftReportRequest {
  /** Provider to use; omit to let the backend pick the user's default. */
  provider_id?: number;
  /** Optional free-text audience (e.g. "executive", "technical remediation"). */
  audience?: string;
  /** Optional free-text steering instructions for the draft. */
  instructions?: string;
  /** Optional severity filter for the findings fed to the model. */
  severities?: string[];
  /** Optional status filter for the findings fed to the model. */
  statuses?: string[];
}

export interface DraftReportResponse {
  /** The drafted report, as markdown. */
  content: string;
  provider_id: number;
  provider_type: string;
  model_id: string | null;
  /** How many promoted findings the draft was built from. */
  finding_total: number;
  severity_counts: Record<string, number>;
  usage: Record<string, unknown> | null;
}

export const draftReportWithAI = async (
  projectId: number,
  body: DraftReportRequest,
  // Optional axios opts so callers can pass an AbortController signal to
  // cancel a long (30-60s) draft mid-flight.
  opts?: { signal?: AbortSignal },
): Promise<DraftReportResponse> => {
  const response = await api.post<DraftReportResponse>(`${projectPath(projectId)}/reports/draft`, body, {
    signal: opts?.signal,
  });
  return response.data;
};

// Tool Ready Output API
export interface ToolReadyResult {
  output: string;
  /** Hosts the filter matched (X-Tool-Ready-Total); null if unreadable. */
  total: number | null;
  /** Hosts the output was built from (X-Tool-Ready-Returned). */
  returned: number | null;
  /** The server cap, set only when a port-loading format was truncated. */
  limit: number | null;
}

export const getToolReadyOutput = async (
  projectId: number,
  format: string,
  // Accepts the full Hosts query context (same shape buildHostQueryContext
  // emits) plus the two tool-ready-only keys.  Serialized generically so a
  // new filter can never be silently dropped here — that would let an
  // analyst generate scanner targets for a broader set than the visible
  // list.  See downloadInventoryCsv / getHosts for the same pattern.
  filters: {
    search?: string;
    state?: string;
    ports?: string;
    services?: string;
    port_states?: string;
    has_open_ports?: boolean;
    os_filter?: string;
    subnets?: string;
    has_critical_vulns?: boolean;
    has_high_vulns?: boolean;
    has_exploit_available?: boolean;
    has_test_execution?: boolean;
    follow_status?: string;
    out_of_scope_only?: boolean;
    scan_ids?: string;
    first_seen_in_scan?: boolean;
    with_notes_only?: boolean;
    has_web_interface?: boolean;
    tech?: string;
    tags?: string;
    subnet_labels?: string;
    sites?: string;
    orgs?: string[];
    asns?: string[];
    countries?: string[];
    assigned_to?: string;
    weaknesses?: string;
    checks?: string;
    q?: string;
    sort_by?: string;
    sort_order?: string;
    scanId?: number;
    includePorts?: boolean;
    /** Name-aware formats (names, web-targets, nuclei, json): only names a
     *  declared domain covers (default) or every bound name. */
    namesScope?: 'in_scope' | 'all';
  }
): Promise<ToolReadyResult> => {
  const params = new URLSearchParams();

  Object.entries(filters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    // Three keys use non-generic wire names; everything else passes through.
    if (key === 'includePorts') {
      if (value) params.append('include_ports', 'true');
      return;
    }
    if (key === 'scanId') {
      params.append('scan_id', String(value));
      return;
    }
    if (key === 'namesScope') {
      params.append('names_scope', String(value));
      return;
    }
    // Arrays (orgs/asns/countries) → repeated params, comma-safe.
    if (Array.isArray(value)) {
      value.forEach((v) => params.append(key, String(v)));
      return;
    }
    params.append(key, String(value));
  });

  const response = await api.get(`${projectPath(projectId)}/hosts/tool-ready/${format}?${params}`, {
    responseType: 'text'
  });

  // The counts ride in headers so the body stays clean for piping.
  const headers = (response.headers ?? {}) as Record<string, string | undefined>;
  const num = (v?: string) => (v ? (Number.isFinite(Number(v)) ? Number(v) : null) : null);
  return {
    output: response.data,
    total: num(headers['x-tool-ready-total']),
    returned: num(headers['x-tool-ready-returned']),
    limit: headers['x-tool-ready-truncated'] === 'true' ? num(headers['x-tool-ready-limit']) : null,
  };
};

export default api;
