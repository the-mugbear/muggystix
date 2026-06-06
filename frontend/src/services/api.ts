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

// --- Core: axios instance + project scoping ---
export { api, setCurrentProjectId, getCurrentProjectId };

// --- Per-domain submodules.  Order doesn't matter; tsc resolves the
//     final flat namespace.  Keep this list alphabetically organised.
export * from './api/activity';
export * from './api/agent-sessions';
export * from './api/agents';
export * from './api/assist';
export * from './api/coverage';
export * from './api/execution-sessions';
export * from './api/feedback';
export * from './api/integrations';
export * from './api/llm-providers';
export * from './api/notifications';
export * from './api/parse-errors';
export * from './api/portfolio';
export * from './api/projects';
export * from './api/recon-sessions';
export * from './api/references';
export * from './api/test-plans';
export * from './api/uploads';


export interface ScanVulnerabilitySummary {
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface ScanPortBreakdown {
  unique_ports: number;
  open_tcp_ports: number;
  open_udp_ports: number;
}

export interface Scan {
  id: number;
  filename: string;
  scan_type: string | null;
  tool_name: string | null;
  start_time?: string | null;
  end_time?: string | null;
  created_at: string;
  total_hosts: number;
  up_hosts: number;
  total_ports: number;
  open_ports: number;
  command_line?: string | null;
  version?: string | null;
  port_breakdown?: ScanPortBreakdown | null;
  vulnerability_summary?: ScanVulnerabilitySummary | null;
}

export interface HostVulnerabilitySummary {
  total_vulnerabilities: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  // Set true by the backend when the vulnerability service raised
  // during the per-host fetch.  The frontend should surface a warning
  // banner so analysts know the displayed counts are zeroes-from-error,
  // not zeroes-from-data — false negatives in this surface are exactly
  // the kind of bug analysts can't recover from on their own.
  error?: boolean;
}

export interface HostVulnerability {
  id: number;
  plugin_id: string | null;
  title: string | null;
  severity: string | null;
  source: string | null;
  cvss_score: number | null;
  cvss_vector: string | null;
  cve_id: string | null;
  scan_id: number | null;
  port_id: number | null;
  port_number: number | null;
  protocol: string | null;
  service_name: string | null;
  exploitable: boolean | null;
  first_seen: string | null;
  last_seen: string | null;
  solution: string | null;
  // v2.45.6 — previously stored server-side but never returned.
  description?: string | null;
  references?: string[];
  source_plugin_name?: string | null;
}

export interface Host {
  id: number;
  ip_address: string;
  hostname: string | null;
  state: string | null;
  os_name: string | null;
  ports: Port[];
  vulnerability_summary?: HostVulnerabilitySummary;
  vulnerabilities?: HostVulnerability[];
  follow?: HostFollowInfo | null;
  notes?: HostNote[];
  note_count?: number;
  test_plan_entry_count?: number;
  test_execution_count?: number;
  // v2.12.0: count of web interfaces (httpx / eyewitness / nikto rows)
  // observed on this host. Gates the HostDetail "Web Interfaces" card
  // and will drive a Hosts-list "Web" badge in phase 2.
  web_interface_count?: number;
  // Count of NetExec credentialed-enumeration rows — gates the
  // HostInspector NetExec card.
  netexec_result_count?: number;
  // Other users (not the caller) who have this host In Review —
  // drives the Hosts-list "In review · <name>" indicator (v4.9.1).
  other_reviewers?: { user_id: number; name: string }[];
  // v2.71.0 — project tags on this host, and users it's assigned to.
  tags?: HostTagInfo[];
  assignees?: HostAssignee[];
  discoveries?: HostDiscovery[];
  // Host-level NSE script output (smb-os-discovery, smb-security-mode,
  // etc.). Port-level scripts live on each Port.scripts.
  host_scripts?: NseScript[];
}

export interface HostTagInfo {
  id: number;
  name: string;
  color?: string | null;
}

export interface HostAssignee {
  user_id: number;
  name: string;
  assigned_at?: string | null;
  assigned_by_id?: number | null;
}

export interface HostListResponse {
  items: Host[];
  total: number | null;
  skip: number;
  limit: number;
  sort_by: string;
  sort_order: string;
  vulnerability_error?: boolean;
}

export type FollowStatus = 'watching' | 'in_review' | 'reviewed';
export type NoteStatus = 'open' | 'in_progress' | 'resolved';

export interface HostFollowInfo {
  status: FollowStatus;
  last_viewed_at?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface HostNote {
  id: number;
  body: string;
  status: NoteStatus;
  author_id: number;
  author_name: string | null;
  parent_id?: number | null;
  created_at: string;
  updated_at?: string | null;
}

export interface HostDiscovery {
  scan_id: number;
  scan_filename: string | null;
  scan_type: string | null;
  tool_name: string | null;
  // `scan_start` / `scan_end` are the actual scanning window pulled from
  // the tool's own output.  These are what an analyst correlating a SOC
  // alert at 14:32 needs — `discovered_at` is just when the file got
  // uploaded to BlueStick and can lag the scan by hours or days.
  scan_start: string | null;
  scan_end: string | null;
  command_line: string | null;
  discovered_at: string | null;
}

// NSE (Nmap Scripting Engine) script output. Captured by the nmap
// parser at both port level (Port.scripts) and host level
// (Host.host_scripts). `script_id` is the NSE script name, e.g.
// "ssl-enum-ciphers", "smb-security-mode".
export interface NseScript {
  id: number;
  script_id: string;
  output: string | null;
  scan_id: number;
}

export interface Port {
  id: number;
  port_number: number;
  protocol: string;
  state: string | null;
  service_name: string | null;
  service_product: string | null;
  service_version: string | null;
  scripts?: NseScript[];
}

export interface SubnetStats {
  id: number;
  cidr: string;
  scope_name: string;
  description: string | null;
  host_count: number;
  total_addresses?: number;
  usable_addresses?: number;
  utilization_percentage?: number;
  risk_level?: string;
  network_address?: string;
  is_private?: boolean;
}
export interface HostConflict {
  id: number;
  field_name: string;
  confidence_score: number;
  scan_type: string;
  data_source: string;
  method: string;
  scan_id: number;
  updated_at: string;
  additional_factors?: any;
}

export interface ConflictHistoryEntry {
  id: number;
  object_type: string;
  object_id: number;
  field_name: string;
  previous_value: string | null;
  previous_confidence: number;
  previous_scan_id: number | null;
  previous_method: string | null;
  new_value: string | null;
  new_confidence: number;
  new_scan_id: number | null;
  new_method: string | null;
  resolved_at: string | null;
}

export interface HostConflictsResponse {
  confidence: HostConflict[];
  conflict_history: ConflictHistoryEntry[];
}

export interface VulnerabilityStats {
  total_vulnerabilities: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  hosts_with_vulnerabilities: number;
}

export interface DashboardStats {
  total_scans: number;
  total_hosts: number;
  total_ports: number;
  up_hosts: number;
  open_ports: number;
  total_subnets: number;
  recent_scans: Scan[];
  subnet_stats: SubnetStats[];
  vulnerability_stats?: VulnerabilityStats;
  note_activity?: NoteActivitySummary;
}
export interface NoteActivityEntry {
  note_id: number;
  host_id: number;
  ip_address: string;
  hostname: string | null;
  status: NoteStatus;
  preview: string;
  created_at: string;
  updated_at?: string | null;
}

export interface ReviewProgress {
  total_hosts: number;
  not_reviewed: number;
  watching: number;
  in_review: number;
  reviewed: number;
}

export interface NoteActivitySummary {
  total_notes: number;
  active_host_count: number;
  following_count: number;
  review_progress?: ReviewProgress;
  recent_notes: NoteActivityEntry[];
}

export interface Scope {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string | null;
  subnets: Subnet[];
  // v2.94.0 — server-paginated subnets.  subnets_total is the unpaginated
  // count; the /scopes editor uses it to drive a "load more" affordance so
  // 6000-subnet projects don't ship every subnet in one payload.
  subnets_total?: number;
  subnets_skip?: number | null;
  subnets_limit?: number | null;
}

export interface ScopeSummary {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  subnet_count: number;
}

export interface Subnet {
  id: number;
  scope_id: number;
  cidr: string;
  description: string | null;
  created_at: string;
  // v2.86.0 — subnet labels attached to this row.  Optional in the
  // type because older API responses (pre-2.86.0) don't include the
  // field; the backend always sends an empty array once the field
  // is wired so callers can treat it as never-null at runtime.
  labels?: SubnetLabelInfo[];
}

export interface SubnetFileUploadResponse {
  message: string;
  scope_id: number;
  subnets_added: number;
  filename: string;
}

export interface HostSubnetMapping {
  id: number;
  host_id: number;
  subnet_id: number;
  created_at: string;
  subnet: Subnet;
}

export interface ScopeCoverageHost {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  last_seen: string | null;
  last_scan_id: number | null;
  last_scan_filename: string | null;
}

export interface TopTechnology {
  name: string;
  host_count: number;
}

export interface ScopeCoverageSummary {
  total_scopes: number;
  total_subnets: number;
  total_hosts: number;
  scoped_hosts: number;
  out_of_scope_hosts: number;
  coverage_percentage: number;
  has_scope_configuration: boolean;
  recent_out_of_scope_hosts: ScopeCoverageHost[];
  // v2.12.1: top technologies observed across scoped hosts
  top_technologies?: TopTechnology[];
}

export interface EyewitnessResult {
  id: number;
  scan_id: number;
  url: string;
  protocol: string | null;
  port: number | null;
  ip_address: string | null;
  title: string | null;
  server_header: string | null;
  content_length: number | null;
  screenshot_path: string | null;
  response_code: number | null;
  page_text: string | null;
  created_at: string;
}

export interface DNSRecord {
  id: number;
  domain: string;
  record_type: string;
  value: string;
  ttl: number | null;
  created_at: string;
  updated_at: string | null;
}

export interface OutOfScopeHost {
  id: number;
  scan_id: number;
  ip_address: string;
  hostname: string | null;
  // Backend stores this as a loose JSON column (out_of_scope_hosts.ports);
  // no frontend consumer reads it, so keep it `unknown` rather than `any`
  // — narrows must be explicit if it's ever used.
  ports: unknown;
  tool_source: string | null;
  reason: string | null;
  created_at: string;
}

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

export interface HostFilterView {
  id: number;
  name: string;
  filter_json: Record<string, any>;
  created_at: string;
  updated_at: string | null;
}

export const listHostFilterViews = async (): Promise<HostFilterView[]> => {
  const response = await api.get(`${p()}/hosts/views`);
  return response.data;
};

export const createHostFilterView = async (
  name: string,
  filterJson: Record<string, any>,
): Promise<HostFilterView> => {
  const response = await api.post(`${p()}/hosts/views`, {
    name,
    filter_json: filterJson,
  });
  return response.data;
};

export const deleteHostFilterView = async (viewId: number): Promise<void> => {
  await api.delete(`${p()}/hosts/views/${viewId}`);
};

export interface MyAttentionHost {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  follow_status: 'in_review' | 'watching';
  open_port_count: number;
  critical_vulns: number;
  high_vulns: number;
  last_viewed_at: string | null;
  follow_updated_at: string | null;
}

export interface MyAttentionResponse {
  items: MyAttentionHost[];
  in_review_count: number;
  watching_count: number;
}

export const getMyAttentionQueue = async (limit = 10): Promise<MyAttentionResponse> => {
  const response = await api.get(`${p()}/dashboard/my-attention`, { params: { limit } });
  return response.data;
};

// Team Review — the project-wide review roster, grouped by reviewer.
export interface TeamReviewHostRow {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  follow_updated_at: string | null;
}

export interface TeamReviewerGroup {
  user_id: number;
  username: string;
  full_name: string | null;
  host_count: number;
  hosts: TeamReviewHostRow[];
}

export interface TeamReviewResponse {
  reviewers: TeamReviewerGroup[];
  total_hosts_in_review: number;
}

export const getTeamReview = async (): Promise<TeamReviewResponse> => {
  const response = await api.get(`${p()}/dashboard/team-review`);
  return response.data;
};

// --- My Tasks (test plan entries on hosts I'm reviewing) ---

export interface MyTaskItem {
  entry_id: number;
  plan_id: number;
  plan_title: string;
  plan_status: string;
  host_id: number;
  host_ip: string;
  host_hostname: string | null;
  priority: string;
  test_phase: string;
  entry_status: string;
  proposed_test_count: number;
  rationale: string | null;
  updated_at: string | null;
}

export interface MyTasksResponse {
  items: MyTaskItem[];
  total_open: number;
}

export const getMyTasks = async (limit = 15): Promise<MyTasksResponse> => {
  const response = await api.get(`${p()}/dashboard/my-tasks`, { params: { limit } });
  return response.data;
};

// --- New scans since last dashboard visit ---

export interface NewScansSinceResponse {
  count: number;
  latest_scan_id: number | null;
  latest_scan_filename: string | null;
  latest_scan_created_at: string | null;
}

export const getNewScansSince = async (
  since: string | null,
): Promise<NewScansSinceResponse> => {
  const params = since ? { since } : undefined;
  const response = await api.get(`${p()}/dashboard/new-scans-since`, { params });
  return response.data;
};

// --- Host followers (who else is reviewing this host) ---

export interface HostFollowerEntry {
  user_id: number;
  username: string;
  full_name: string | null;
  status: 'watching' | 'in_review' | 'reviewed';
  since: string;
}

export interface HostFollowersResponse {
  followers: HostFollowerEntry[];
}

export const getHostFollowers = async (hostId: number): Promise<HostFollowersResponse> => {
  const response = await api.get(`${p()}/hosts/${hostId}/followers`);
  return response.data;
};

export const followHost = async (hostId: number, status: FollowStatus): Promise<HostFollowInfo> => {
  const response = await api.post(`${p()}/hosts/${hostId}/follow`, { status });
  return response.data;
};

export const unfollowHost = async (hostId: number): Promise<void> => {
  await api.delete(`${p()}/hosts/${hostId}/follow`);
};

export const recordHostView = async (hostId: number): Promise<void> => {
  await api.post(`${p()}/hosts/${hostId}/view`);
};

export const createHostNote = async (
  hostId: number,
  payload: { body: string; status?: NoteStatus; parent_id?: number },
): Promise<HostNote> => {
  const response = await api.post(`${p()}/hosts/${hostId}/notes`, payload);
  return response.data;
};

export const updateHostNote = async (
  hostId: number,
  noteId: number,
  payload: { body?: string; status?: NoteStatus },
): Promise<HostNote> => {
  const response = await api.patch(`${p()}/hosts/${hostId}/notes/${noteId}`, payload);
  return response.data;
};

export const deleteHostNote = async (hostId: number, noteId: number): Promise<void> => {
  await api.delete(`${p()}/hosts/${hostId}/notes/${noteId}`);
};

export interface NoteActivityItem {
  note_id: number;
  host_id: number;
  ip_address: string | null;
  hostname: string | null;
  body: string;
  status: NoteStatus;
  author_name: string | null;
  author_id: number;
  parent_id?: number | null;
  thread_root_id?: number | null;
  thread_note_count?: number;
  created_at: string;
  updated_at: string | null;
  host_note_count: number;
}

export interface NoteActivityAuthor {
  id: number;
  name: string;
}

export interface NoteActivityResponse {
  notes: NoteActivityItem[];
  total_notes: number;
  status_counts: { open: number; in_progress: number; resolved: number };
  authors: NoteActivityAuthor[];
}

export const getNoteActivity = async (params?: {
  status?: string;
  author_id?: number;
  search?: string;
  skip?: number;
  limit?: number;
}): Promise<NoteActivityResponse> => {
  const queryParams = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) queryParams.append(key, value.toString());
    });
  }
  const qs = queryParams.toString();
  const response = await api.get(`${p()}/hosts/notes/activity${qs ? `?${qs}` : ''}`);
  return response.data;
};

export const markActivitySeen = async (): Promise<void> => {
  await api.post(`${p()}/hosts/notes/mark-seen`);
};

// Scans API
//
// v2.43.0 — added optional `search` + `signal` so the CommandPalette can
// pass a server-side substring match instead of fetching the whole list
// and filtering on the client.  Existing callers (Scans page, etc.) keep
// the positional-args signature because they don't need the new params.
export const getScans = async (
  skip = 0,
  limit = 100,
  options?: {
    search?: string;
    tool?: string;
    createdAfter?: string;
    sortBy?: 'created_at' | 'filename' | 'tool_name' | 'file_size' | 'duration_seconds' | 'total_hosts';
    sortOrder?: 'asc' | 'desc';
    signal?: AbortSignal;
  },
): Promise<Scan[]> => {
  const { search, tool, createdAfter, sortBy, sortOrder, signal } = options ?? {};
  const params: Record<string, string | number> = { skip, limit };
  if (search) params.search = search;
  if (tool) params.tool = tool;
  if (createdAfter) params.created_after = createdAfter;
  if (sortBy) params.sort_by = sortBy;
  if (sortOrder) params.sort_order = sortOrder;
  const response = await api.get(`${p()}/scans/`, { params, signal });
  return response.data;
};

export const getScan = async (scanId: number) => {
  const response = await api.get(`${p()}/scans/${scanId}`);
  return response.data;
};

export const deleteScan = async (scanId: number) => {
  const response = await api.delete(`${p()}/scans/${scanId}`);
  return response.data;
};

// --- Scan-diff (attack-surface delta between two scans) ---

export interface ScanDiffSide {
  scan_id: number;
  filename: string;
  tool_name?: string | null;
  scan_type?: string | null;
  created_at?: string | null;
  total_hosts: number;
  up_hosts: number;
  total_ports: number;
  open_ports: number;
}

export interface ScanDiffHostRow {
  host_id: number;
  ip_address: string;
  hostname?: string | null;
}

export interface ScanDiffHostStateChange {
  host_id: number;
  ip_address: string;
  hostname?: string | null;
  state_a?: string | null;
  state_b?: string | null;
}

export interface ScanDiffPortChange {
  host_id: number;
  ip_address: string;
  port_number: number;
  protocol?: string | null;
  service_name?: string | null;
  state_a?: string | null;
  state_b?: string | null;
}

export interface ScanDiffCounts {
  new_hosts: number;
  dropped_hosts: number;
  host_state_changes: number;
  newly_open_ports: number;
  closed_ports: number;
}

export interface ScanDiffResponse {
  scan_a: ScanDiffSide;
  scan_b: ScanDiffSide;
  counts: ScanDiffCounts;
  row_cap: number;
  new_hosts: ScanDiffHostRow[];
  dropped_hosts: ScanDiffHostRow[];
  host_state_changes: ScanDiffHostStateChange[];
  newly_open_ports: ScanDiffPortChange[];
  closed_ports: ScanDiffPortChange[];
}

export const compareScans = async (a: number, b: number): Promise<ScanDiffResponse> => {
  const response = await api.get(`${p()}/scans/compare`, { params: { a, b } });
  return response.data;
};

// --- Agent-activity analytics (project-level aggregate of agent_api_calls) ---

export interface AgentActivityStatusBreakdown {
  success: number;
  client_error: number;
  server_error: number;
  other: number;
}

export interface AgentActivityWorkflowCount {
  workflow: string;
  calls: number;
}

export interface AgentActivityDayBucket {
  day: string;
  calls: number;
  errors: number;
}

export interface AgentActivitySessionRow {
  workflow: string;
  session_id: number;
  calls: number;
  last_activity?: string | null;
}

export interface AgentActivitySummary {
  window_days: number;
  total_calls: number;
  distinct_agents: number;
  first_call_at?: string | null;
  last_call_at?: string | null;
  status_breakdown: AgentActivityStatusBreakdown;
  by_workflow: AgentActivityWorkflowCount[];
  daily: AgentActivityDayBucket[];
  busiest_sessions: AgentActivitySessionRow[];
}

export const getAgentActivitySummary = async (
  windowDays = 14,
): Promise<AgentActivitySummary> => {
  const response = await api.get(`${p()}/agent-activity/summary`, {
    params: { window_days: windowDays },
  });
  return response.data;
};

// Hosts API
export const getHosts = async (params: {
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
  has_medium_vulns?: boolean;
  has_low_vulns?: boolean;
  has_exploit_available?: boolean;
  has_test_execution?: boolean;
  min_risk_score?: number;
  out_of_scope_only?: boolean;
  follow_status?: string;
  scan_ids?: string;
  first_seen_in_scan?: boolean;
  with_notes_only?: boolean;
  has_web_interface?: boolean;
  tech?: string;
  tags?: string;
  // v2.86.0 — comma-separated subnet-label IDs; OR semantics within the
  // group, AND against tags / other filter categories.
  subnet_labels?: string;
  assigned_to?: string;
  // v5.0.0 — boolean query DSL; ANDs with the discrete filters above.
  q?: string;
  skip?: number;
  limit?: number;
  include_total?: boolean;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}, signal?: AbortSignal): Promise<HostListResponse> => {
  const queryParams = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined) {
      queryParams.append(key, value.toString());
    }
  });
  
  const response = await api.get(`${p()}/hosts/?${queryParams}`, { signal });
  return response.data;
};

export const getHost = async (hostId: number): Promise<Host> => {
  const response = await api.get(`${p()}/hosts/${hostId}`);
  return response.data;
};

// ---------------------------------------------------------------------------
// Hosts boolean query DSL UX (v5.0.0) — schema / validate / history
// ---------------------------------------------------------------------------

export interface HostQueryField {
  name: string;
  aliases: string[];
  value_source: string;
  trgm: boolean;
  enum_values: string[];
}

export interface HostQueryExample {
  label: string;
  q: string;
}

export interface HostQuerySchema {
  fields: HostQueryField[];
  examples: HostQueryExample[];
}

export interface HostQueryValidation {
  valid: boolean;
  error?: { message: string; position: number | null } | null;
  leaf_count?: number | null;
  match_count?: number | null;
}

export interface HostQueryHistoryEntry {
  id: number;
  q: string;
  result_count: number | null;
  created_at: string;
}

export const getHostQuerySchema = async (signal?: AbortSignal): Promise<HostQuerySchema> => {
  const response = await api.get(`${p()}/hosts/query/schema`, { signal });
  return response.data;
};

export const validateHostQuery = async (q: string, signal?: AbortSignal): Promise<HostQueryValidation> => {
  const response = await api.post(`${p()}/hosts/query/validate`, { q }, { signal });
  return response.data;
};

export const listHostQueryHistory = async (limit = 20): Promise<HostQueryHistoryEntry[]> => {
  const response = await api.get(`${p()}/hosts/query/history`, { params: { limit } });
  return response.data;
};

export const recordHostQuery = async (
  q: string,
  resultCount?: number | null,
): Promise<HostQueryHistoryEntry> => {
  const response = await api.post(`${p()}/hosts/query/history`, {
    q,
    result_count: resultCount ?? null,
  });
  return response.data;
};

export const deleteHostQuery = async (entryId: number): Promise<void> => {
  await api.delete(`${p()}/hosts/query/history/${entryId}`);
};

export const clearHostQueryHistory = async (): Promise<void> => {
  await api.delete(`${p()}/hosts/query/history`);
};

// ---------------------------------------------------------------------------
// Host tags (v2.71.0)
// ---------------------------------------------------------------------------

export interface HostTagWithCount {
  id: number;
  name: string;
  color?: string | null;
  host_count: number;
}

export const listHostTags = async (): Promise<HostTagWithCount[]> => {
  const response = await api.get(`${p()}/hosts/tags`);
  return response.data;
};

export const createHostTag = async (name: string, color?: string | null): Promise<HostTagWithCount> => {
  const response = await api.post(`${p()}/hosts/tags`, { name, color: color ?? null });
  return response.data;
};

export const updateHostTag = async (
  tagId: number,
  body: { name?: string; color?: string | null },
): Promise<HostTagWithCount> => {
  const response = await api.patch(`${p()}/hosts/tags/${tagId}`, body);
  return response.data;
};

export const deleteHostTag = async (tagId: number): Promise<void> => {
  await api.delete(`${p()}/hosts/tags/${tagId}`);
};

export const assignHostTags = async (
  hostId: number,
  body: { tag_ids?: number[]; names?: string[] },
): Promise<HostTagInfo[]> => {
  const response = await api.post(`${p()}/hosts/${hostId}/tags`, {
    tag_ids: body.tag_ids ?? [],
    names: body.names ?? [],
  });
  return response.data;
};

export const removeHostTag = async (hostId: number, tagId: number): Promise<void> => {
  await api.delete(`${p()}/hosts/${hostId}/tags/${tagId}`);
};

// ---------------------------------------------------------------------------
// Subnet labels (v2.86.0)
//
// Project-scoped labels attached to one or more subnets, used by the Hosts
// inventory page to filter by infrastructure boundary.  Parallel to host
// tags but separate vocabulary (per design decision A).  All routes are
// mounted under /projects/{pid}/scopes/...
// ---------------------------------------------------------------------------

export interface SubnetLabelInfo {
  id: number;
  name: string;
  color?: string | null;
}

export interface SubnetLabelWithCounts {
  id: number;
  project_id: number;
  name: string;
  color?: string | null;
  created_at: string;
  subnet_count: number;
  // COUNT DISTINCT of hosts reachable via subnets carrying this label.
  // Smaller than naive (assignment_count × hosts_per_subnet) because
  // overlapping CIDRs are deduplicated server-side.
  host_count: number;
}

export const listSubnetLabels = async (): Promise<SubnetLabelWithCounts[]> => {
  const response = await api.get(`${p()}/scopes/subnet-labels`);
  return response.data;
};

export const createSubnetLabel = async (
  name: string,
  color?: string | null,
): Promise<SubnetLabelWithCounts> => {
  const response = await api.post(`${p()}/scopes/subnet-labels`, { name, color: color ?? null });
  return response.data;
};

export const updateSubnetLabel = async (
  labelId: number,
  body: { name?: string; color?: string | null },
): Promise<SubnetLabelWithCounts> => {
  const response = await api.patch(`${p()}/scopes/subnet-labels/${labelId}`, body);
  return response.data;
};

export const deleteSubnetLabel = async (labelId: number): Promise<void> => {
  await api.delete(`${p()}/scopes/subnet-labels/${labelId}`);
};

// Idempotent: PUT the desired full label set on the subnet.  Anything
// not in `labelIds` is detached; anything missing is attached.
export const replaceSubnetLabels = async (
  subnetId: number,
  labelIds: number[],
): Promise<SubnetLabelInfo[]> => {
  const response = await api.put(`${p()}/scopes/subnets/${subnetId}/labels`, { label_ids: labelIds });
  return response.data;
};

export const attachSubnetLabel = async (
  subnetId: number,
  labelId: number,
): Promise<SubnetLabelInfo> => {
  const response = await api.post(`${p()}/scopes/subnets/${subnetId}/labels/${labelId}`);
  return response.data;
};

export const detachSubnetLabel = async (subnetId: number, labelId: number): Promise<void> => {
  await api.delete(`${p()}/scopes/subnets/${subnetId}/labels/${labelId}`);
};

// Bulk-apply one label across many subnets in a single request (idempotent
// per-subnet).  Drives the "select N subnets → apply label X" affordance
// on the Scope detail page.
export const bulkApplySubnetLabel = async (
  labelId: number,
  subnetIds: number[],
): Promise<SubnetLabelWithCounts> => {
  const response = await api.post(`${p()}/scopes/subnet-labels/${labelId}/subnets`, {
    subnet_ids: subnetIds,
  });
  return response.data;
};

// ---------------------------------------------------------------------------
// Host assignment (v2.71.0)
// ---------------------------------------------------------------------------

export interface HostAssignmentInfo {
  host_id: number;
  user_id: number;
  assigned_by_id?: number | null;
  assigned_at?: string | null;
  status: string;
}

export const assignHost = async (
  hostId: number,
  assigneeUserId: number,
): Promise<HostAssignmentInfo> => {
  const response = await api.post(`${p()}/hosts/${hostId}/assign`, {
    assignee_user_id: assigneeUserId,
  });
  return response.data;
};

export const unassignHost = async (hostId: number, userId: number): Promise<void> => {
  await api.delete(`${p()}/hosts/${hostId}/assign`, { params: { user_id: userId } });
};

// ---------------------------------------------------------------------------
// Bulk host operations + select-all helper (v2.71.0)
// ---------------------------------------------------------------------------

export interface BulkResult {
  affected: number;
  requested: number;
}

export const bulkTagHosts = async (
  hostIds: number[],
  body: { tag_ids?: number[]; names?: string[]; action: 'add' | 'remove' },
): Promise<BulkResult> => {
  const response = await api.post(`${p()}/hosts/bulk/tags`, {
    host_ids: hostIds,
    tag_ids: body.tag_ids ?? [],
    names: body.names ?? [],
    action: body.action,
  });
  return response.data;
};

export const bulkAssignHosts = async (
  hostIds: number[],
  assigneeUserId: number,
): Promise<BulkResult> => {
  const response = await api.post(`${p()}/hosts/bulk/assign`, {
    host_ids: hostIds,
    assignee_user_id: assigneeUserId,
  });
  return response.data;
};

export const bulkFollowHosts = async (
  hostIds: number[],
  status: FollowStatus,
): Promise<BulkResult> => {
  const response = await api.post(`${p()}/hosts/bulk/follow`, { host_ids: hostIds, status });
  return response.data;
};

export interface MatchingHostIds {
  ids: number[];
  total: number;
  capped: boolean;
}

export const getMatchingHostIds = async (
  params: Record<string, string | boolean | number | undefined>,
): Promise<MatchingHostIds> => {
  const clean: Record<string, string> = {};
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== '') clean[k] = String(v);
  });
  const response = await api.get(`${p()}/hosts/ids`, { params: clean });
  return response.data;
};

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

export interface HostLineageReconRow {
  session_id: number;
  scope_id: number;
  scope_name?: string | null;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  generated_by_model?: string | null;
  generated_by_tool?: string | null;
  started_by_username?: string | null;
}

export interface HostLineagePlanRow {
  plan_id: number;
  title: string;
  status: string;
  version: number;
  entry_id: number;
  entry_status: string;
  created_at: string;
  generated_by_model?: string | null;
  source_kind?: string | null;
}

export interface HostLineageExecutionRow {
  execution_session_id: number;
  plan_id: number;
  plan_title: string;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  generated_by_model?: string | null;
  started_by_username?: string | null;
  test_count: number;
  finding_count: number;
}

export interface HostLineageResponse {
  host_id: number;
  ip_address: string;
  recon_sessions: HostLineageReconRow[];
  plan_entries: HostLineagePlanRow[];
  execution_sessions: HostLineageExecutionRow[];
}

export const getHostLineage = async (hostId: number): Promise<HostLineageResponse> => {
  const response = await api.get<HostLineageResponse>(`${p()}/hosts/${hostId}/lineage`);
  return response.data;
};

// ---------------------------------------------------------------------------
// Web interfaces (v2.12.0) — unified view of httpx / eyewitness / nikto
// output, per host.  See backend/app/db/models.py::WebInterface.
// ---------------------------------------------------------------------------

export interface WebInterface {
  id: number;
  source: string;                 // httpx | eyewitness | nikto
  url: string;
  protocol?: string | null;
  port?: number | null;
  status_code?: number | null;
  title?: string | null;
  server_header?: string | null;
  content_length?: number | null;
  technologies?: string[] | null;  // flattened ["Nginx 1.18.0", "React", ...]
  favicon_hash?: string | null;
  tls_info?: Record<string, unknown> | null;
  has_screenshot: boolean;
  first_seen?: string | null;
  last_seen?: string | null;
  scan_id: number;
  port_id?: number | null;
}

export const getHostWebInterfaces = async (hostId: number): Promise<WebInterface[]> => {
  const response = await api.get(`${p()}/hosts/${hostId}/web-interfaces`);
  return response.data;
};

// NetExec (credentialed-enumeration) result for one protocol probe of
// a host. `shares` is parser-shaped JSON — rendered defensively.
export interface NetexecResult {
  id: number;
  scan_id: number;
  protocol: string;
  port?: number | null;
  auth_success?: boolean | null;
  username?: string | null;
  domain?: string | null;
  hostname?: string | null;
  domain_name?: string | null;
  os_version?: string | null;
  shares?: unknown;
  first_seen?: string | null;
}

export const getHostNetexecResults = async (hostId: number): Promise<NetexecResult[]> => {
  const response = await api.get(`${p()}/hosts/${hostId}/netexec`);
  return response.data;
};

/**
 * Fetch a web interface's screenshot PNG and return a blob:// URL
 * suitable for use in an ``<img src>``.
 *
 * The frontend authenticates via a Bearer token injected by the
 * axios request interceptor.  A plain ``<img src>`` tag would bypass
 * that interceptor and hit the backend without auth, so we fetch
 * the bytes through axios, wrap them in a Blob, and produce a local
 * object URL instead.  Callers must revoke the URL when the image
 * unmounts to avoid memory leaks — see the ``useEffect`` cleanup
 * pattern in ``ScreenshotLightbox.tsx``.
 *
 * Returns ``null`` on 404 (no screenshot present / path guard
 * rejected) so callers can render a fallback without a try/catch.
 */
export const fetchWebInterfaceScreenshot = async (
  interfaceId: number,
): Promise<string | null> => {
  try {
    const response = await api.get(
      `${p()}/hosts/web-interfaces/${interfaceId}/screenshot`,
      { responseType: 'blob' },
    );
    return URL.createObjectURL(response.data as Blob);
  } catch (err: unknown) {
    if (asAxiosError(err).response?.status === 404) return null;
    throw err;
  }
};

// v4.55.0 (#44.1 + UX phase 3) — per-host DNS records.  Each row
// carries ``resolver_name`` (NULL for pre-v2.89.0 ingests from the
// CSV / amass paths; populated by the dnsx parser for fresh ingests).
export interface HostDnsRecordRow {
  id: number;
  domain: string;
  record_type: string;
  value: string;
  ttl: number | null;
  resolver_name: string | null;
  created_at: string;
}

export interface HostDnsRecordsResponse {
  items: HostDnsRecordRow[];
  total: number;
  resolvers: string[];
  record_types: string[];
}

export const getHostDnsRecords = async (
  hostId: number,
): Promise<HostDnsRecordsResponse> => {
  try {
    const response = await api.get(`${p()}/hosts/${hostId}/dns-records`);
    return response.data;
  } catch (error: any) {
    // Older deployments don't expose this endpoint — fail closed to
    // an empty result so HostInspector can still render the rest of
    // the page.
    if (error?.response?.status === 404) {
      return { items: [], total: 0, resolvers: [], record_types: [] };
    }
    throw error;
  }
};

export const getHostConflicts = async (hostId: number): Promise<HostConflictsResponse> => {
  try {
    const response = await api.get(`${p()}/hosts/${hostId}/conflicts`);
    return response.data;
  } catch (error: any) {
    // Only swallow 404 (endpoint not available in older deployments);
    // let auth errors and server failures propagate.
    if (error?.response?.status === 404) {
      return { confidence: [], conflict_history: [] };
    }
    throw error;
  }
};

export interface ScanHostsQuery {
  state?: string;
  search?: string;
  port?: number;
  skip?: number;
  limit?: number;
}

export const getHostsByScan = async (
  scanId: number,
  query: string | ScanHostsQuery = {},
): Promise<Host[]> => {
  // v2.86.9 — second arg accepts either a legacy bare state string
  // (back-compat) or a query object with search / port / skip /
  // limit knobs that the backend gained at the same time.
  const q: ScanHostsQuery = typeof query === 'string' ? { state: query } : query;
  const params = new URLSearchParams();
  if (q.state) params.set('state', q.state);
  if (q.search) params.set('search', q.search);
  if (q.port !== undefined) params.set('port', String(q.port));
  if (q.skip !== undefined) params.set('skip', String(q.skip));
  if (q.limit !== undefined) params.set('limit', String(q.limit));
  const qs = params.toString();
  const response = await api.get(`${p()}/hosts/scan/${scanId}${qs ? `?${qs}` : ''}`);
  return response.data;
};

// Facet data backing the Hosts-page filter comboboxes.  Typed (not `any`)
// so a backend shape change surfaces at compile time rather than as a
// silently-empty dropdown or an `undefined.find` at runtime.
export interface HostFilterData {
  common_ports: Array<{ port: number; service: string; state: string; count: number }>;
  services: Array<{ name: string; count: number }>;
  operating_systems: Array<{ name: string; count: number }>;
  subnets: Array<{ cidr: string; scope_name: string; host_count: number }>;
  scans?: Array<{ id: number; filename: string; tool_name?: string | null; created_at?: string | null }>;
  technologies?: Array<{ name: string; host_count: number }>;
  tags?: Array<{ id: number; name: string; color?: string | null; host_count: number }>;
  subnet_labels?: Array<{ id: number; name: string; color?: string | null; host_count: number }>;
}

export const getHostFilterData = async (params?: Record<string, string | boolean | number | undefined>, signal?: AbortSignal): Promise<HostFilterData> => {
  const queryParams = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        queryParams.append(key, value.toString());
      }
    });
  }
  const qs = queryParams.toString();
  const response = await api.get(`${p()}/hosts/filters/data${qs ? `?${qs}` : ''}`, { signal });
  return response.data;
};

// Dashboard API
export const getDashboardStats = async (): Promise<DashboardStats> => {
  const response = await api.get(`${p()}/dashboard/stats`);
  return response.data;
};

// Scopes API
export const getScopes = async (): Promise<ScopeSummary[]> => {
  const response = await api.get(`${p()}/scopes/`);
  return response.data;
};

export const getScope = async (scopeId: number, withFindingsOnly: boolean = false): Promise<Scope> => {
  const response = await api.get(`${p()}/scopes/${scopeId}?with_findings_only=${withFindingsOnly}`);
  return response.data;
};

/**
 * Fetch the project's single scope (v2.9.4+).  A project now has
 * exactly one conceptual scope; this endpoint creates it on the fly
 * if it doesn't exist yet so the flat subnet editor page has a
 * guaranteed target to append entries to.
 */
export const getDefaultScope = async (
  opts: { subnetsSkip?: number; subnetsLimit?: number; withFindingsOnly?: boolean } = {},
): Promise<Scope> => {
  const params = new URLSearchParams();
  if (opts.subnetsSkip !== undefined) params.set('subnets_skip', String(opts.subnetsSkip));
  if (opts.subnetsLimit !== undefined) params.set('subnets_limit', String(opts.subnetsLimit));
  if (opts.withFindingsOnly !== undefined) params.set('with_findings_only', String(opts.withFindingsOnly));
  const qs = params.toString();
  const response = await api.get<Scope>(`${p()}/scopes/default${qs ? `?${qs}` : ''}`);
  return response.data;
};

export const deleteScope = async (scopeId: number) => {
  const response = await api.delete(`${p()}/scopes/${scopeId}`);
  return response.data;
};

export const updateScope = async (
  scopeId: number,
  body: { name?: string; description?: string },
): Promise<Scope> => {
  const response = await api.patch<Scope>(`${p()}/scopes/${scopeId}`, body);
  return response.data;
};

export const createScope = async (body: { name: string; description?: string }): Promise<Scope> => {
  const response = await api.post<Scope>(`${p()}/scopes/`, body);
  return response.data;
};

export interface SubnetEntry {
  id: number;
  scope_id: number;
  cidr: string;
  description: string | null;
  created_at: string;
}

export const addScopeSubnets = async (
  scopeId: number,
  subnets: Array<{ cidr: string; description?: string }>,
): Promise<SubnetEntry[]> => {
  const response = await api.post<SubnetEntry[]>(`${p()}/scopes/${scopeId}/subnets`, { subnets });
  return response.data;
};

export const updateSubnet = async (
  scopeId: number,
  subnetId: number,
  body: { cidr?: string; description?: string },
): Promise<SubnetEntry> => {
  const response = await api.patch<SubnetEntry>(`${p()}/scopes/${scopeId}/subnets/${subnetId}`, body);
  return response.data;
};

export const deleteSubnet = async (scopeId: number, subnetId: number): Promise<void> => {
  await api.delete(`${p()}/scopes/${scopeId}/subnets/${subnetId}`);
};

export const uploadSubnetFile = async (
  file: File,
): Promise<SubnetFileUploadResponse> => {
  // Scopes no longer carry a user-supplied name or description.  The
  // backend auto-generates a fallback name from the upload filename
  // so the underlying NOT NULL column is satisfied without requiring
  // the user to fill in metadata.  See backend/app/api/v1/endpoints/
  // scopes.py:upload_subnet_file for the fallback logic.
  const formData = new FormData();
  formData.append('file', file);

  const response = await api.post(`${p()}/scopes/upload-subnets`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  
  return response.data;
};

export interface ScopeHostMappingsQuery {
  subnetId?: number;
  skip?: number;
  limit?: number;
}

export interface ScopeHostMappingsResult {
  items: HostSubnetMapping[];
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}

export const getScopeHostMappings = async (
  scopeId: number,
  query: ScopeHostMappingsQuery = {},
): Promise<ScopeHostMappingsResult> => {
  // v2.86.8 — query params: subnet_id (filter to one subnet's mappings),
  // skip + limit (pagination, le=2000 server-side).
  // v2.86.13 — return shape standardised on the ``Paginated[T]``
  // envelope ({items, total, skip, limit, has_more}); callers that
  // only need the items array can ``.items`` off the result.
  const params = new URLSearchParams();
  if (query.subnetId !== undefined) params.set('subnet_id', String(query.subnetId));
  if (query.skip !== undefined) params.set('skip', String(query.skip));
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  const qs = params.toString();
  const response = await api.get<ScopeHostMappingsResult>(
    `${p()}/scopes/${scopeId}/host-mappings${qs ? `?${qs}` : ''}`,
  );
  return response.data;
};

export const getScopeCoverage = async (limit: number = 25): Promise<ScopeCoverageSummary> => {
  const response = await api.get(`${p()}/scopes/coverage?limit=${limit}`);
  return response.data;
};

export const correlateAllHosts = async () => {
  const response = await api.post(`${p()}/scopes/correlate-all`);
  return response.data;
};

// Export API


export const getScopeHostList = async (scopeId: number, format: 'txt' | 'csv' | 'json' = 'txt'): Promise<string> => {
  const response = await api.get(`${p()}/export/scope/${scopeId}?format_type=${format}`, { responseType: 'text' });
  return response.data;
};

export const getOutOfScopeHostList = async (format: 'txt' | 'csv' | 'json' = 'txt'): Promise<string> => {
  const response = await api.get(`${p()}/export/out-of-scope?format_type=${format}`, { responseType: 'text' });
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

export const getScanCommandExplanation = async (scanId: number): Promise<CommandExplanation> => {
  const response = await api.get(`${p()}/scans/${scanId}/command-explanation`);
  return response.data;
};

// Parse Error API functions — only the singular fetch is wired up to
// the UI today; the list/stats/update/delete wrappers were removed in
// the cleanup pass after months of zero consumers.  Re-add when a
export const generateHostsReport = async (
  format: 'csv' | 'html' | 'json' | 'agent-package' | 'markdown-bundle',
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
    min_risk_score?: number;
    follow_status?: string;
    out_of_scope_only?: boolean;
    scan_ids?: string;
    first_seen_in_scan?: boolean;
    with_notes_only?: boolean;
    q?: string;
  }
) => {
  const queryParams = new URLSearchParams();

  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined) {
      queryParams.append(key, value.toString());
    }
  });

  const response = await api.get(`${p()}/reports/hosts/${format}?${queryParams}`, {
    responseType: 'blob'
  });
  
  // Create download
  const blob = new Blob([response.data]);
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const contentDisposition = response.headers['content-disposition'] as string | undefined;
  const filenameMatch = contentDisposition?.match(/filename="?([^"]+)"?/i);
  const fallbackExtension = format === 'agent-package' || format === 'markdown-bundle' ? 'zip' : format;
  a.download = filenameMatch?.[1] || `hosts_report_${new Date().toISOString().split('T')[0]}.${fallbackExtension}`;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
  
  return response.data;
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
    min_risk_score?: number;
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
