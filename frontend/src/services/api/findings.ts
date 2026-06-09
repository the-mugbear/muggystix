/**
 * Findings API client — the unified finding spine (promote-from-note,
 * triage, cross-host). Project-scoped via p().
 */
import { api, p } from './client';

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
}

export interface FindingFilters {
  status?: FindingStatus;
  severity?: FindingSeverity;
  owner_id?: number;
  source?: FindingSource;
  host_id?: number;
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

export const createFinding = async (payload: FindingCreatePayload): Promise<Finding> => {
  const response = await api.post<Finding>(`${p()}/findings`, payload);
  return response.data;
};

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

// Promote (or dismiss) a scanner vulnerability as a finding. Severity defaults
// to the vuln's own; a terminal status (false_positive/accepted_risk)
// dismisses it. Idempotent per vuln.
export const promoteVulnerability = async (
  vulnId: number,
  payload: { severity?: string; status?: FindingStatus; owner_id?: number } = {},
): Promise<Finding> => {
  const response = await api.post<Finding>(
    `${p()}/vulnerabilities/${vulnId}/promote`,
    { vuln_id: vulnId, ...payload },
  );
  return response.data;
};
