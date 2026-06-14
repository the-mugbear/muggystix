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
 *      template — they all import ``api`` and optionally ``p`` from
 *      ``./client``).
 *   2. Re-exporting it from this barrel.
 *   3. New domain's types are then visible to every consumer.
 *
 * NOTE: ``import api from '../services/api'`` (the default import
 * pattern) still works — the barrel re-exports the axios instance
 * as the default below for code that hand-rolls a request.
 */
import { api, p, setCurrentProjectId, getCurrentProjectId } from './api/client';
import { asAxiosError } from '../utils/apiErrors';
import type { Paginated } from './api/shared';  // local use; also re-exported via the barrel below

// --- Core: axios instance + project scoping ---
export { api, setCurrentProjectId, getCurrentProjectId };

// --- Per-domain submodules.  Order doesn't matter; tsc resolves the
//     final flat namespace.  Keep this list alphabetically organised.
export * from './api/activity';
export * from './api/agent-sessions';
export * from './api/agents';
export * from './api/assist';
export * from './api/coverage';
export * from './api/dashboard';
export * from './api/execution-sessions';
export * from './api/feedback';
export * from './api/findings';
export * from './api/hosts';
export * from './api/insights';
export * from './api/integrations';
export * from './api/llm-providers';
export * from './api/notifications';
export * from './api/parse-errors';
export * from './api/portfolio';
export * from './api/posture';
export * from './api/projects';
export * from './api/recon-sessions';
export * from './api/references';
export * from './api/scans';
export * from './api/scopes';
export * from './api/shared';
export * from './api/sites';
export * from './api/test-plans';
export * from './api/uploads';

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
export const getScanDnsRecords = async (scanId: number): Promise<Paginated<DNSRecord>> => {
  try {
    const response = await api.get(`${p()}/scans/${scanId}/dns-records`, {
      params: { skip: 0, limit: DNS_RECORDS_PAGE },
    });
    return response.data;
  } catch (error) {
    if (asAxiosError(error)?.response?.status === 404) {
      return { items: [], total: 0, skip: 0, limit: DNS_RECORDS_PAGE, has_more: false };
    }
    throw error;
  }
};

export interface PortOfInterestSummary {
  port: number;
  protocol: string;
  label: string;
  category: string;
  weight: number;
  open_host_count: number;
  rationale: string;
  recommended_action: string;
}

export interface PortOfInterestHostEntry {
  port: number;
  protocol: string;
  label: string;
  service: string;
  weight: number;
  category: string;
}

export interface HostRiskExposure {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  ports_of_interest: PortOfInterestHostEntry[];
  critical: number;
  high: number;
  medium: number;
  low: number;
  risk_score: number;
  port_score: number;
  vulnerability_score: number;
}

export interface VulnerabilityHotspot {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  critical: number;
  high: number;
  medium: number;
  low: number;
  risk_score: number;
}

export interface RiskInsightResponse {
  ports_of_interest: {
    summary: PortOfInterestSummary[];
    top_hosts: HostRiskExposure[];
  };
  vulnerability_hotspots: VulnerabilityHotspot[];
}



export const getRiskInsights = async (): Promise<RiskInsightResponse> => {
  const response = await api.get(`${p()}/dashboard/risk-insights`);
  return response.data;
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

export const listProjectMembers = async (): Promise<ProjectMember[]> => {
  const response = await api.get(`${p()}/members`);
  return response.data;
};

// --- Cross-project member management (SoC manager / Portfolio) ---
// Absolute project paths (not the active-project `p()`) so the Portfolio
// can view/manage any project's roster.

export interface UserDirectoryEntry {
  id: number;
  username: string;
  full_name?: string | null;
  email?: string | null;
}

export const getProjectMembers = async (projectId: number): Promise<ProjectMember[]> => {
  const response = await api.get(`/projects/${projectId}/members`);
  return response.data;
};

export const getUserDirectory = async (): Promise<UserDirectoryEntry[]> => {
  const response = await api.get('/users/directory');
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

export const listWebhookEventTypes = async (): Promise<WebhookEventType[]> => {
  const response = await api.get(`${p()}/webhooks/event-types`);
  return response.data;
};

export const listWebhooks = async (): Promise<WebhookConfig[]> => {
  const response = await api.get(`${p()}/webhooks`);
  return response.data;
};

export const createWebhook = async (payload: WebhookCreatePayload): Promise<WebhookConfig> => {
  const response = await api.post(`${p()}/webhooks`, payload);
  return response.data;
};

export const updateWebhook = async (
  id: number,
  payload: Partial<WebhookCreatePayload>,
): Promise<WebhookConfig> => {
  const response = await api.patch(`${p()}/webhooks/${id}`, payload);
  return response.data;
};

export const deleteWebhook = async (id: number): Promise<void> => {
  await api.delete(`${p()}/webhooks/${id}`);
};

export const testWebhook = async (id: number): Promise<WebhookTestResult> => {
  const response = await api.post(`${p()}/webhooks/${id}/test`);
  return response.data;
};

// ---------------------------------------------------------------------------
// Scan staleness (v2.73.0)
// ---------------------------------------------------------------------------

export interface ScopeStaleness {
  scope_id: number;
  scope_name: string;
  last_activity_at?: string | null;
  days_since?: number | null;
  is_stale: boolean;
}

export interface StalenessResponse {
  stale_days: number;
  latest_scan_at?: string | null;
  days_since_last_scan?: number | null;
  project_is_stale: boolean;
  stale_scope_count: number;
  scopes: ScopeStaleness[];
}

export const getStaleness = async (staleDays?: number): Promise<StalenessResponse> => {
  const response = await api.get(`${p()}/dashboard/staleness`, {
    params: staleDays ? { stale_days: staleDays } : {},
  });
  return response.data;
};

// ---------------------------------------------------------------------------
// Network topology (v2.75.0)
// ---------------------------------------------------------------------------

export interface TopoNode {
  id: string;
  type: 'project' | 'scope' | 'subnet' | 'unscoped' | string;
  label: string;
  host_count: number;
  meta: Record<string, unknown>;
}

export interface TopoEdge {
  id: string;
  source: string;
  target: string;
}

export interface TopologyResponse {
  nodes: TopoNode[];
  edges: TopoEdge[];
  truncated: boolean;
}

export const getTopology = async (): Promise<TopologyResponse> => {
  const response = await api.get(`${p()}/dashboard/topology`);
  return response.data;
};


// ---------------------------------------------------------------------------
// Host workflow lineage (v3 alpha.9) — recon sessions that discovered
// this host + plan entries referencing it + execution sessions that
// have run results against any of those entries.  One round trip;
// drives the HostDetail "Workflow lineage" panel.
// ---------------------------------------------------------------------------

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

export const getScanCommandExplanation = async (scanId: number): Promise<CommandExplanation> => {
  const response = await api.get(`${p()}/scans/${scanId}/command-explanation`);
  return response.data;
};

// Parse Error API functions — only the singular fetch is wired up to
// the UI today; the list/stats/update/delete wrappers were removed in
// the cleanup pass after months of zero consumers.  Re-add when a
// CSV + HTML stream synchronously from the API and download directly. The heavy
// formats (json/zip bundles) are async report jobs — see enqueueReportJob.
export const generateHostsReport = async (
  format: 'csv' | 'html',
  filters: {
    scan_id?: number;
    state?: string;
    search?: string;
    ports?: string;
    services?: string;
    port_states?: string;
    has_open_ports?: boolean;
    os_filter?: string;
    subnets?: string;
    has_critical_vulns?: boolean;
    has_high_vulns?: boolean;
    follow_status?: string;
    out_of_scope_only?: boolean;
    scan_ids?: string;
    first_seen_in_scan?: boolean;
    with_notes_only?: boolean;
    q?: string;
  },
  // 'comprehensive' (full security report) | 'inventory' (concise host list).
  // Ignored by csv (always the inventory table) and the structured zip exports.
  reportType?: 'inventory' | 'comprehensive',
) => {
  const queryParams = new URLSearchParams();

  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined) {
      queryParams.append(key, value.toString());
    }
  });
  // CSV is always the inventory table; only HTML honours report_type.
  if (reportType && format === 'html') {
    queryParams.append('report_type', reportType);
  }

  const response = await api.get(`${p()}/reports/hosts/${format}?${queryParams}`, {
    responseType: 'blob'
  });

  // The backend caps a report at REPORT_MAX_HOSTS and flags a partial result
  // with X-Report-Truncated. Surface it so a partial export isn't mistaken for
  // a complete one (the client-side overCap estimate can disagree with the
  // server's actual cap, or the data can change during generation).
  const truncated = String(response.headers['x-report-truncated'] ?? '').toLowerCase() === 'true';

  // Create download
  const blob = new Blob([response.data]);
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const contentDisposition = response.headers['content-disposition'] as string | undefined;
  const filenameMatch = contentDisposition?.match(/filename="?([^"]+)"?/i);
  const fallbackExtension = format;
  a.download = filenameMatch?.[1] || `hosts_report_${new Date().toISOString().split('T')[0]}.${fallbackExtension}`;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);

  return { truncated };
};

// --- Async report jobs (pdf / json / agent-package / markdown-bundle) --------
// These build the whole document in memory, so they run on a dedicated report
// worker: enqueue a job, poll its status, then download the artifact.

export type AsyncReportFormat = 'json' | 'agent-package' | 'markdown-bundle';

export interface ReportJob {
  id: number;
  project_id: number;
  format: string;
  report_type: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  message?: string | null;
  error_message?: string | null;
  result_filename?: string | null;
  media_type?: string | null;
  file_size?: number | null;
  truncated: boolean;
  retry_count?: number | null;
  last_error?: string | null;
  last_heartbeat?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  expires_at?: string | null;
  dismissed_at?: string | null;
}

export const enqueueReportJob = async (
  format: AsyncReportFormat,
  filters: Record<string, string | number | boolean | undefined>,
  reportType?: 'inventory' | 'comprehensive',
): Promise<ReportJob> => {
  const query = new URLSearchParams();
  query.set('format', format);
  if (reportType) query.set('report_type', reportType);
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined) query.append(key, value.toString());
  });
  const response = await api.post(`${p()}/reports/jobs?${query}`);
  return response.data as ReportJob;
};

export const getReportJob = async (jobId: number): Promise<ReportJob> => {
  const response = await api.get(`${p()}/reports/jobs/${jobId}`);
  return response.data as ReportJob;
};

export const downloadReportJob = async (jobId: number): Promise<{ truncated: boolean }> => {
  const response = await api.get(`${p()}/reports/jobs/${jobId}/download`, { responseType: 'blob' });
  const truncated = String(response.headers['x-report-truncated'] ?? '').toLowerCase() === 'true';
  const blob = new Blob([response.data]);
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const contentDisposition = response.headers['content-disposition'] as string | undefined;
  const filenameMatch = contentDisposition?.match(/filename="?([^"]+)"?/i);
  a.download = filenameMatch?.[1] || `report_${jobId}`;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
  return { truncated };
};

export const listReportJobs = async (limit = 20): Promise<ReportJob[]> => {
  const response = await api.get(`${p()}/reports/jobs?limit=${limit}`);
  return response.data as ReportJob[];
};

export const dismissReportJob = async (jobId: number): Promise<ReportJob> => {
  const response = await api.post(`${p()}/reports/jobs/${jobId}/dismiss`);
  return response.data as ReportJob;
};

// Tool Ready Output API
export const getToolReadyOutput = async (
  format: string,
  // Accepts the full Hosts query context (same shape buildHostQueryContext
  // emits) plus the two tool-ready-only keys.  Serialized generically so a
  // new filter can never be silently dropped here — that would let an
  // analyst generate scanner targets for a broader set than the visible
  // list.  See generateHostsReport / getHosts for the same pattern.
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
    assigned_to?: string;
    q?: string;
    sort_by?: string;
    sort_order?: string;
    scanId?: number;
    includePorts?: boolean;
  }
): Promise<string> => {
  const params = new URLSearchParams();

  Object.entries(filters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    // Two keys use non-generic wire names; everything else passes through.
    if (key === 'includePorts') {
      if (value) params.append('include_ports', 'true');
      return;
    }
    if (key === 'scanId') {
      params.append('scan_id', String(value));
      return;
    }
    params.append(key, String(value));
  });

  const response = await api.get(`${p()}/hosts/tool-ready/${format}?${params}`, {
    responseType: 'text'
  });

  return response.data;
};

export default api;
