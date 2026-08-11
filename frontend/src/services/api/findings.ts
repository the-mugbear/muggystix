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
  host_id: number;
  ip_address: string | null;
  hostname: string | null;
  host_status: string;
}

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
  created_at: string;
  updated_at: string | null;
}

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

export const listFindings = async (filters: FindingFilters = {}): Promise<FindingListResponse> => {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== null) params.set(k, String(v));
  });
  const qs = params.toString();
  const response = await api.get<FindingListResponse>(`${p()}/findings${qs ? `?${qs}` : ''}`);
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
  payload: { title?: string; severity?: FindingSeverity; owner_id?: number | null },
): Promise<Finding> => {
  const response = await api.patch<Finding>(`${p()}/findings/${findingId}`, payload);
  return response.data;
};

export const setFindingStatus = async (
  findingId: number,
  status: FindingStatus,
  summary?: string,
): Promise<Finding> => {
  const response = await api.post<Finding>(`${p()}/findings/${findingId}/status`, { status, summary });
  return response.data;
};

export const addFindingHosts = async (findingId: number, hostIds: number[]): Promise<Finding> => {
  const response = await api.post<Finding>(`${p()}/findings/${findingId}/hosts`, { host_ids: hostIds });
  return response.data;
};

export const removeFindingHost = async (findingId: number, hostId: number): Promise<Finding> => {
  const response = await api.delete<Finding>(`${p()}/findings/${findingId}/hosts/${hostId}`);
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

// Promote (or dismiss) a scanner vulnerability as a finding. Severity defaults
// to the vuln's own; a terminal status (false_positive/accepted_risk)
// dismisses it. Idempotent per vuln.
export const promoteVulnerability = async (
  vulnId: number,
  payload: { severity?: string; status?: FindingStatus; owner_id?: number; summary?: string } = {},
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
