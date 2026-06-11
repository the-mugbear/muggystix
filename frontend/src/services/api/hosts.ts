/**
 * Hosts API client — the host inventory graph and all host-scoped
 * operations.
 *
 * Types: Host + Port + NseScript + vulnerabilities + notes + follow +
 * discoveries + conflicts + tags + saved filter views + query-DSL schema
 * + lineage + web interfaces + netexec + DNS records + filter facets.
 *
 * Functions: list/detail, follow/unfollow/record-view, notes CRUD +
 * note-activity, tags CRUD + assignment, host assignment, bulk ops, saved
 * filter views, query-DSL validate/history, lineage, web interfaces,
 * netexec, DNS records, conflicts, by-scan, and filter facet data.
 *
 * The largest slice of the api.ts split.  Consumers still import these
 * from ``../services/api`` — the barrel re-exports this module.
 */
import { api, p } from './client';
import type { FollowStatus, NoteStatus, NoteType } from './shared';
import { asAxiosError } from '../../utils/apiErrors';

// --- BODY APPENDED BELOW (moved verbatim from api.ts) ---

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
  // Set when this vuln has been promoted to a finding — drives the "Promoted"
  // badge/link and guards a duplicate promote.
  finding_id?: number | null;
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
  state_reason?: string | null;
  os_name: string | null;
  // OS-detail fields the serializer returns but the UI previously dropped —
  // os_accuracy (nmap match confidence %) + os_vendor are what an analyst
  // needs to judge "Windows Server 2019" as a 95% match vs a 60% guess.
  os_family?: string | null;
  os_generation?: string | null;
  os_type?: string | null;
  os_vendor?: string | null;
  os_accuracy?: number | string | null;
  ports: Port[];
  vulnerability_summary?: HostVulnerabilitySummary;
  vulnerabilities?: HostVulnerability[];
  follow?: HostFollowInfo | null;
  notes?: Annotation[];
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
  // Count of recorded host-field data conflicts (scans disagreed on a value;
  // ConflictHistory rows) — drives the Hosts-list "conflict" data-quality badge.
  conflict_count?: number;
  // Foundation 6d — count of ACTIVE findings (open/confirmed/retest)
  // affecting this host; drives the Hosts-list finding badge.
  finding_count?: number;
  // True when the host's most-recent scan flipped its state or added a port
  // vs the prior scan — drives the "Changed" triage badge.
  changed_recently?: boolean;
  // OTHER users (not the caller) who have this host In Review — drives the
  // Hosts-list "In review · <name>" indicator (v4.9.1).  Teammates-only: the
  // caller's own status is on the Follow control, so including the caller
  // duplicated the badge.
  other_reviewers?: { user_id: number; name: string }[];
  // Teammates (not the caller) who have COMPLETED review of this host, plus
  // the team-wide review state (most-advanced across all users).  Review is
  // team-shared, so the row surfaces who reviewed a host the filter classifies
  // as "Reviewed" — not just who is currently in review.
  reviewed_by?: { user_id: number; name: string }[];
  team_review_status?: 'reviewed' | 'in_review' | null;
  // Host discovery timestamps + attention inputs surfaced for the redesigned
  // Hosts table (Host column "new"/last-seen/stale; Attention column
  // "exploit available"; Host column site/subnet).
  first_seen?: string | null;
  last_seen?: string | null;
  exploitable_count?: number;
  primary_subnet?: string | null;
  primary_site?: string | null;
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

export interface HostFollowInfo {
  status: FollowStatus;
  last_viewed_at?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface Annotation {
  id: number;
  body: string;
  status: NoteStatus;
  author_id: number;
  author_name: string | null;
  parent_id?: number | null;
  // Thread-level work fields (P3) — populated on the root note.
  assignee_id?: number | null;
  assignee_name?: string | null;
  due_at?: string | null;
  note_type?: NoteType | null;
  resolution_summary?: string | null;
  pinned?: boolean;
  // Set when this thread root has been promoted to a finding — drives the
  // "Promoted" badge/link and guards a duplicate promote.
  finding_id?: number | null;
  created_at: string;
  updated_at?: string | null;
}

export interface AnnotationStatusHistoryEntry {
  id: number;
  from_status: string | null;
  to_status: string;
  changed_by_id: number | null;
  changed_by_name: string | null;
  summary: string | null;
  created_at: string;
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
  // Returned by the serializer but previously dropped here: service_extrainfo
  // often carries the most useful banner detail ("Ubuntu; protocol 2.0");
  // service_conf/reason/method indicate detection confidence + how.
  service_extrainfo?: string | null;
  service_conf?: string | null;
  service_method?: string | null;
  reason?: string | null;
  scripts?: NseScript[];
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
  // Canonical host-level conflict count — the SAME number the Hosts-list badge
  // shows.  Use this for the "N conflicts" count; `confidence` is per-field
  // confidence records (host + port), not a conflict count.
  conflict_count: number;
  confidence: HostConflict[];
  conflict_history: ConflictHistoryEntry[];
}

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

export const createAnnotation = async (
  hostId: number,
  payload: { body: string; status?: NoteStatus; parent_id?: number },
): Promise<Annotation> => {
  const response = await api.post(`${p()}/hosts/${hostId}/notes`, payload);
  return response.data;
};

export interface AnnotationUpdatePayload {
  body?: string;
  status?: NoteStatus;
  // Thread-level work fields (P3). Sending `null` clears a nullable field;
  // omitting a field leaves it unchanged.
  assignee_id?: number | null;
  due_at?: string | null;
  note_type?: NoteType | null;
  resolution_summary?: string | null;
  pinned?: boolean;
}

export const updateAnnotation = async (
  hostId: number,
  noteId: number,
  payload: AnnotationUpdatePayload,
): Promise<Annotation> => {
  const response = await api.patch(`${p()}/hosts/${hostId}/notes/${noteId}`, payload);
  return response.data;
};

export const getAnnotationHistory = async (
  hostId: number,
  noteId: number,
): Promise<AnnotationStatusHistoryEntry[]> => {
  const response = await api.get(`${p()}/hosts/${hostId}/notes/${noteId}/history`);
  return response.data;
};

export const deleteAnnotation = async (hostId: number, noteId: number): Promise<void> => {
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
  // Status of the thread's ROOT note — use this for the thread-level badge,
  // not `status` (which is the per-message status; a reply is always "open").
  thread_root_status?: NoteStatus | null;
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
  // Comma-separated site names; OR within the group.
  sites?: string;
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
  hostname?: string | null;
  domain_name?: string | null;
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
  // Total DNS records ingested for the whole project — lets the card show
  // "N ingested, none match this host" instead of silently rendering nothing.
  project_total: number;
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
      return { items: [], total: 0, resolvers: [], record_types: [], project_total: 0 };
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
      return { conflict_count: 0, confidence: [], conflict_history: [] };
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
  sites?: Array<{ name: string; host_count: number }>;
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

