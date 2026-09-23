/**
 * Findings API client — the unified finding spine (promote-from-note,
 * triage, cross-host). Project-scoped via p().
 */
import { api, p } from './client';
import type { Annotation, NoteAttachment } from './hosts';

export type FindingSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type FindingStatus =
  | 'open'
  | 'confirmed'
  | 'false_positive'
  | 'accepted_risk'
  | 'remediated'
  | 'retest';
export type FindingSource = 'note' | 'scanner' | 'execution' | 'manual';

export interface FindingHostInfo {
  /** The affected-endpoint ROW id — a host may carry one row per named endpoint. */
  id: number;
  host_id: number;
  ip_address: string | null;
  hostname: string | null;
  // v5.194.0 — the named endpoint on this host the finding applies to
  // (inherited from the scanner row / plan entry); null = host-level.
  name_id?: number | null;
  fqdn?: string | null;
  /** This endpoint's own state: open | remediated | retest | false_positive.
   *  The finding's status is the issue's; this one is the host's (v5.225.0). */
  host_status: FindingHostStatus;
}

/** `false_positive` (v5.238.0): the issue does not apply to THIS endpoint —
 *  it says nothing about the finding's other hosts. */
export type FindingHostStatus = 'open' | 'remediated' | 'retest' | 'false_positive';

export interface Finding {
  id: number;
  project_id: number;
  title: string;
  severity: FindingSeverity;
  status: FindingStatus;
  source: FindingSource;
  owner_id: number | null;
  owner_name: string | null;
  evidence_annotation_id: number | null;
  vuln_id: number | null;
  exec_result_id: number | null;
  host_count: number;
  hosts: FindingHostInfo[];
  /** v5.225.0 — {open, remediated, retest} over the endpoint rows. */
  endpoint_status_counts?: Partial<Record<FindingHostStatus, number>>;
  /** v5.256.0 — who recorded it, and whether the caller may rename or delete
   *  it (its author or a project admin). Triage is not gated by this. */
  created_by_id?: number | null;
  created_by_name?: string | null;
  can_modify?: boolean;
  /** v5.260.0 — what the client report says (Markdown). Single-finding
   *  responses only; the list sends null. Edited under `can_modify`. */
  report_text?: FindingReportText | null;
  /** v5.260.0 — the caller is a project admin: may mark any evidence image
   *  for the report, not only their own uploads. */
  viewer_is_project_admin?: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface FindingReportText {
  description: string | null;
  impact: string | null;
  recommendation: string | null;
  references: string | null;
  steps_to_reproduce: string | null;
  cvss_vector: string | null;
  cvss_score: number | null;
  /** A 3.x / 2.0 vector decides the score; the editor shows it read-only. */
  cvss_score_from_vector: boolean;
}

export type FindingReportTextField =
  'description' | 'impact' | 'recommendation' | 'references' | 'steps_to_reproduce';

export type FindingReportTextUpdate = Partial<
  Record<FindingReportTextField | 'cvss_vector', string | null> & { cvss_score: number | null }
>;

/** AI suggestions for a finding's report text (backend 2.394.0).  Nothing is
 *  saved: the author reviews them in the editor and saves as usual.
 *  Errors: 400 (no provider / nothing empty), 403 (not the author or a
 *  project admin), 502 (provider failed or answered unreadably). */
export interface FindingTextDraft {
  suggestions: Partial<Record<FindingReportTextField, string>>;
  provider_id: number;
  provider_type: string;
  model_id: string | null;
}

export const draftFindingText = async (
  findingId: number,
  fields?: FindingReportTextField[],
  opts?: { signal?: AbortSignal },
): Promise<FindingTextDraft> => {
  const response = await api.post<FindingTextDraft>(
    `${p()}/reports/draft/finding-text`,
    { finding_id: findingId, ...(fields ? { fields } : {}) },
    { signal: opts?.signal },
  );
  return response.data;
};

export interface FindingListResponse {
  items: Finding[];
  total: number;
  // Per-severity counts for the rollup header (all filters except severity).
  severity_counts?: Partial<Record<FindingSeverity, number>>;
}

export type FindingSortField = 'severity' | 'status' | 'title' | 'host_count' | 'source' | 'created_at';

/** A real status, or a server-side group: 'active' / 'resolved'. */
export type FindingStatusQuery = FindingStatus | 'active' | 'resolved';

export interface FindingFilters {
  status?: FindingStatusQuery;
  severity?: FindingSeverity;
  owner_id?: number;
  /** Only findings with no owner (overrides owner_id server-side). */
  unowned?: boolean;
  source?: FindingSource;
  host_id?: number;
  /** Case-insensitive substring match on the finding title. */
  search?: string;
  sort?: FindingSortField;
  dir?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}

export const listFindings = async (
  filters: FindingFilters = {},
  // Lets the caller abort a superseded request (Findings page: a newer filter
  // set cancels the in-flight one so a slow response can't overwrite it).
  signal?: AbortSignal,
): Promise<FindingListResponse> => {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null) params.set(k, String(v));
  });
  const qs = params.toString();
  const response = await api.get<FindingListResponse>(`${p()}/findings${qs ? `?${qs}` : ''}`, { signal });
  return response.data;
};

export const getFinding = async (findingId: number): Promise<Finding> => {
  const response = await api.get<Finding>(`${p()}/findings/${findingId}`);
  return response.data;
};

export interface PromoteAnnotationPayload {
  severity: FindingSeverity;
  title?: string;
  status?: FindingStatus;
  owner_id?: number | null;
  extra_host_ids?: number[];
}

export const promoteAnnotation = async (
  annotationId: number,
  payload: PromoteAnnotationPayload,
): Promise<Finding> => {
  const response = await api.post<Finding>(`${p()}/annotations/${annotationId}/promote`, payload);
  return response.data;
};

export interface FindingCreatePayload {
  title: string;
  severity: FindingSeverity;
  status?: FindingStatus;
  owner_id?: number | null;
  host_ids?: number[];
}
export const updateFinding = async (
  findingId: number,
  payload: { title?: string; severity?: FindingSeverity; owner_id?: number | null } & FindingReportTextUpdate,
): Promise<Finding> => {
  const response = await api.patch<Finding>(`${p()}/findings/${findingId}`, payload);
  return response.data;
};

/** v5.256.0 — delete a finding recorded in error (its author or a project
 *  admin). Its comments and history go with it; the evidence it pointed at
 *  (source note, scanner rows) stays. */
export const deleteFinding = async (findingId: number): Promise<void> => {
  await api.delete(`${p()}/findings/${findingId}`);
};

export const setFindingStatus = async (
  findingId: number,
  status: FindingStatus,
  summary?: string,
): Promise<Finding> => {
  const response = await api.post<Finding>(`${p()}/findings/${findingId}/status`, { status, summary });
  return response.data;
};

/** v5.225.0 — set ONE endpoint row's state (open / remediated / retest)
 *  without touching the finding's own status. */
export const setFindingEndpointStatus = async (
  findingId: number,
  findingHostId: number,
  hostStatus: FindingHostStatus,
): Promise<Finding> => {
  const response = await api.patch<Finding>(
    `${p()}/findings/${findingId}/endpoints/${findingHostId}`,
    { host_status: hostStatus },
  );
  return response.data;
};

export interface FindingEndpointRef {
  host_id: number;
  name_id?: number | null;
  host_status?: string | null;
}

export const addFindingHosts = async (
  findingId: number,
  hostIds: number[],
  endpoints: FindingEndpointRef[] = [],
): Promise<Finding> => {
  const response = await api.post<Finding>(`${p()}/findings/${findingId}/hosts`, {
    host_ids: hostIds,
    endpoints,
  });
  return response.data;
};

/** Detach EVERY endpoint row on a host. Prefer removeFindingEndpoint for one row. */
export const removeFindingHost = async (findingId: number, hostId: number): Promise<Finding> => {
  const response = await api.delete<Finding>(`${p()}/findings/${findingId}/hosts/${hostId}`);
  return response.data;
};

/** v5.195.0 — detach exactly one affected endpoint (a FindingHost row). */
export const removeFindingEndpoint = async (findingId: number, findingHostId: number): Promise<Finding> => {
  const response = await api.delete<Finding>(`${p()}/findings/${findingId}/endpoints/${findingHostId}`);
  return response.data;
};

export interface FindingStatusHistoryEntry {
  id: number;
  from_status: string | null;
  to_status: string;
  changed_by_id: number | null;
  changed_by_name: string | null;
  summary: string | null;
  created_at: string;
}

export const getFindingHistory = async (
  findingId: number,
): Promise<FindingStatusHistoryEntry[]> => {
  const response = await api.get<FindingStatusHistoryEntry[]>(`${p()}/findings/${findingId}/history`);
  return response.data;
};

// --- Finding comment / evidence thread ---
// A finding hosts its own annotation thread (the notes→findings→reports flow):
// discussion + repro/rationale + screenshots, refined here before reports.

export const getFindingNotes = async (findingId: number): Promise<Annotation[]> => {
  const response = await api.get<Annotation[]>(`${p()}/findings/${findingId}/notes`);
  return response.data;
};

export const createFindingNote = async (
  findingId: number,
  body: string,
  parentId?: number | null,
): Promise<Annotation> => {
  const response = await api.post<Annotation>(`${p()}/findings/${findingId}/notes`, {
    body,
    parent_id: parentId ?? null,
  });
  return response.data;
};

/** v5.256.0 — the comment's author only. */
export const updateFindingNote = async (
  findingId: number,
  noteId: number,
  body: string,
): Promise<Annotation> => {
  const response = await api.patch<Annotation>(`${p()}/findings/${findingId}/notes/${noteId}`, { body });
  return response.data;
};

/** v5.256.0 — the comment's author only; 409 while it has replies. */
export const deleteFindingNote = async (findingId: number, noteId: number): Promise<void> => {
  await api.delete(`${p()}/findings/${findingId}/notes/${noteId}`);
};

export const uploadFindingNoteAttachment = async (
  findingId: number,
  noteId: number,
  file: File,
): Promise<NoteAttachment> => {
  const form = new FormData();
  form.append('file', file);
  const response = await api.post(
    `${p()}/findings/${findingId}/notes/${noteId}/attachments`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return response.data;
};

export interface PromoteVulnerabilityPreview {
  plugin_id: string | null;
  /** Scanner-agnostic issue identity the fan-out keys on. */
  issue_key: string | null;
  affected_host_count: number;
  affected_host_sample: string[];
  /** Hosts not already attached — what this action would actually change.
   *  Equals affected_host_count for a fresh promote. */
  new_host_count: number;
  already_promoted: boolean;
  finding_id: number | null;
  finding_status: string | null;
  /** v5.238.0 — the inspected host, for the "this host only" choice, and its
   *  endpoint state on the existing finding (null = not on it / no finding). */
  host_ip?: string | null;
  host_endpoint_status?: string | null;
}

// Blast radius of promoting a vuln (read-only) — how many project hosts carry
// the same ISSUE and would be attached to the one finding (§11). Keyed on the
// issue, not the plugin, so it matches what promote actually does: a
// plugin-keyed preview under-reported whenever two scanners saw one problem.
export const previewPromoteVulnerability = async (
  vulnId: number,
): Promise<PromoteVulnerabilityPreview> => {
  const response = await api.get<PromoteVulnerabilityPreview>(
    `${p()}/vulnerabilities/${vulnId}/promote-preview`,
  );
  return response.data;
};

// v5.272.0 — scanner observations grouped by ISSUE across the project's hosts,
// and their bulk promotion (the Findings page's "Scanner observations" view).
export interface ObservationIssue {
  issue_key: string;
  title: string;
  severity: string;
  cve_id: string | null;
  sources: string[];
  host_count: number;
  /** Hosts a finding already covers for this issue. */
  judged_host_count: number;
  finding_id: number | null;
  finding_status: string | null;
}

export interface ObservationIssueHost {
  host_id: number;
  ip_address: string;
  hostname: string | null;
  severity: string;
  ports: number[];
  judged: boolean;
  endpoint_status: string | null;
}

export interface ObservationIssueFilters {
  search?: string;
  severity?: string;
  includeJudged?: boolean;
  minHosts?: number;
  skip?: number;
  limit?: number;
}

export const getObservationIssues = async (
  filters: ObservationIssueFilters = {},
): Promise<{ items: ObservationIssue[]; total: number }> => {
  const response = await api.get(`${p()}/scanner-observations`, {
    params: {
      search: filters.search || undefined,
      severity: filters.severity || undefined,
      include_judged: filters.includeJudged || undefined,
      min_hosts: filters.minHosts && filters.minHosts > 1 ? filters.minHosts : undefined,
      skip: filters.skip || undefined,
      limit: filters.limit ?? 50,
    },
  });
  return response.data;
};

/** The first `limit` hosts by address (omitted = all). */
export const getObservationIssueHosts = async (issueKey: string, limit?: number): Promise<ObservationIssueHost[]> => {
  const response = await api.get(`${p()}/scanner-observations/hosts`, { params: { issue_key: issueKey, limit } });
  return response.data;
};

/** Each issue becomes (or joins) its finding; `host_ids` omitted = every host carrying it. */
export const promoteObservationIssues = async (
  items: { issue_key: string; host_ids?: number[] }[],
): Promise<{ results: { issue_key: string; finding_id: number; created: boolean; host_count: number }[] }> => {
  const response = await api.post(`${p()}/scanner-observations/promote`, { items });
  return response.data;
};

// Promote (or dismiss) a scanner vulnerability as a finding. Severity defaults
// to the vuln's own; a terminal status (false_positive/accepted_risk)
// dismisses it. Idempotent per vuln.
export const promoteVulnerability = async (
  vulnId: number,
  payload: {
    severity?: string;
    status?: FindingStatus;
    owner_id?: number;
    summary?: string;
    /** How far a false-positive dismissal reaches: `host` (the server's
     *  default) = this host's endpoint only; `issue` = every host carrying
     *  it. Promotion and accepted risk are always about the issue. */
    scope?: 'host' | 'issue';
  } = {},
): Promise<Finding> => {
  const response = await api.post<Finding>(
    `${p()}/vulnerabilities/${vulnId}/promote`,
    { vuln_id: vulnId, ...payload },
  );
  return response.data;
};

// --------------------------------------------------------------------------
// Bulk operations (v5.135.0)
// --------------------------------------------------------------------------
// The page previously looped `setFindingStatus` per id from the browser —
// unbounded, partially failable, and with no single audit moment. These route
// the whole selection through one request that validates project scope,
// enforces the terminal-justification rule across the batch, and emits one
// assignment notification instead of N.

export interface BulkFindingResult {
  affected: number;
  requested: number;
  /** Ids the server refused (not in this project / already gone). */
  skipped_ids: number[];
}

export const bulkSetFindingStatus = async (
  findingIds: number[],
  status: FindingStatus,
  summary?: string,
): Promise<BulkFindingResult> => {
  const res = await api.post<BulkFindingResult>(`${p()}/findings/bulk/status`, {
    finding_ids: findingIds,
    status,
    summary,
  });
  return res.data;
};

/** `assigneeUserId: null` unassigns — the single-finding PATCH can't express
 *  that, since it skips owner_id when null. */
export const bulkAssignFindings = async (
  findingIds: number[],
  assigneeUserId: number | null,
): Promise<BulkFindingResult> => {
  const res = await api.post<BulkFindingResult>(`${p()}/findings/bulk/assign`, {
    finding_ids: findingIds,
    assignee_user_id: assigneeUserId,
  });
  return res.data;
};
