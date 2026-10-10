/**
 * Scopes API client — scopes, subnets, subnet labels, host-mappings,
 * coverage, and scope/out-of-scope host-list exports.
 *
 * Extracted from the api.ts monolith.  Consumers still import these from
 * ``../services/api`` — the barrel re-exports this module.
 */
import { api, projectPath } from './client';

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

export interface Subnet {
  id: number;
  scope_id: number;
  cidr: string;
  description: string | null;
  // Physical/logical site this subnet belongs to (e.g. "London DC").
  site?: string | null;
  created_at: string;
  // v2.86.0 — subnet labels attached to this row.  Optional in the
  // type because older API responses (pre-2.86.0) don't include the
  // field; the backend always sends an empty array once the field
  // is wired so callers can treat it as never-null at runtime.
  labels?: SubnetLabelInfo[];
  // Hosts mapped to this subnet — sent by the scope-detail endpoints only.
  host_count?: number | null;
  // Of those, the hosts no vulnerability scan has covered (Evidence's rule).
  not_vuln_assessed_count?: number | null;
}

export interface SubnetFileUploadResponse {
  message: string;
  scope_id: number;
  subnets_added: number;
  /** Domain rows in the same file land in scope_domains (backend 2.326.0). */
  domains_added?: number;
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

export interface ScopeCoverageSummary {
  total_scopes: number;
  total_subnets: number;
  /** Domain-scope entries (backend 2.322.0). */
  total_domains: number;
  /** Hosts with no subnet mapping that an in-scope name resolves to — the
   *  third coverage state; not in scoped_hosts, already subtracted from
   *  out_of_scope_hosts. */
  name_reachable_hosts: number;
  total_hosts: number;
  scoped_hosts: number;
  out_of_scope_hosts: number;
  coverage_percentage: number;
  has_scope_configuration: boolean;
  recent_out_of_scope_hosts: ScopeCoverageHost[];
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

// --- Subnet labels (v2.86.0) ---
// Project-scoped labels attached to one or more subnets, used by the Hosts
// inventory page to filter by infrastructure boundary.  All routes are
// mounted under /projects/{pid}/scopes/...

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

export interface SubnetEntry {
  id: number;
  scope_id: number;
  cidr: string;
  description: string | null;
  created_at: string;
}

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

// --- Scopes ---

/**
 * Fetch the project's single scope (v2.9.4+).  A project now has
 * exactly one conceptual scope; this endpoint creates it on the fly
 * if it doesn't exist yet so the flat subnet editor page has a
 * guaranteed target to append entries to.
 */
export const getDefaultScope = async (
  projectId: number,
  opts: {
    subnetsSkip?: number;
    subnetsLimit?: number;
    withFindingsOnly?: boolean;
    subnetsSearch?: string;
  } = {},
  signal?: AbortSignal,
): Promise<Scope> => {
  const params = new URLSearchParams();
  if (opts.subnetsSkip !== undefined) params.set('subnets_skip', String(opts.subnetsSkip));
  if (opts.subnetsLimit !== undefined) params.set('subnets_limit', String(opts.subnetsLimit));
  if (opts.withFindingsOnly !== undefined) params.set('with_findings_only', String(opts.withFindingsOnly));
  if (opts.subnetsSearch && opts.subnetsSearch.trim()) {
    params.set('subnets_search', opts.subnetsSearch.trim());
  }
  const qs = params.toString();
  const response = await api.get<Scope>(`${projectPath(projectId)}/scopes/default${qs ? `?${qs}` : ''}`, { signal });
  return response.data;
};

export const deleteScope = async (projectId: number, scopeId: number) => {
  const response = await api.delete(`${projectPath(projectId)}/scopes/${scopeId}`);
  return response.data;
};
export const addScopeSubnets = async (
  projectId: number,
  scopeId: number,
  subnets: Array<{ cidr: string; description?: string }>,
): Promise<SubnetEntry[]> => {
  const response = await api.post<SubnetEntry[]>(`${projectPath(projectId)}/scopes/${scopeId}/subnets`, { subnets });
  return response.data;
};

export const updateSubnet = async (
  projectId: number,
  scopeId: number,
  subnetId: number,
  body: { cidr?: string; description?: string; site?: string },
): Promise<SubnetEntry> => {
  const response = await api.patch<SubnetEntry>(`${projectPath(projectId)}/scopes/${scopeId}/subnets/${subnetId}`, body);
  return response.data;
};

export const deleteSubnet = async (projectId: number, scopeId: number, subnetId: number): Promise<void> => {
  await api.delete(`${projectPath(projectId)}/scopes/${scopeId}/subnets/${subnetId}`);
};

export const uploadSubnetFile = async (
  projectId: number,
  file: File,
): Promise<SubnetFileUploadResponse> => {
  // Scopes no longer carry a user-supplied name or description.  The
  // backend auto-generates a fallback name from the upload filename
  // so the underlying NOT NULL column is satisfied without requiring
  // the user to fill in metadata.  See backend/app/api/v1/endpoints/
  // scopes.py:upload_subnet_file for the fallback logic.
  const formData = new FormData();
  formData.append('file', file);

  const response = await api.post(`${projectPath(projectId)}/scopes/upload-subnets`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });

  return response.data;
};
export const getScopeCoverage = async (projectId: number, limit: number = 25, signal?: AbortSignal): Promise<ScopeCoverageSummary> => {
  const response = await api.get(`${projectPath(projectId)}/scopes/coverage?limit=${limit}`, { signal });
  return response.data;
};

export const getScopeHostList = async (
  projectId: number, scopeId: number, format: 'txt' | 'csv' | 'json' | 'web' = 'txt', signal?: AbortSignal,
): Promise<string> => {
  const response = await api.get(`${projectPath(projectId)}/export/scope/${scopeId}?format_type=${format}`, { responseType: 'text', signal });
  return response.data;
};

export const getOutOfScopeHostList = async (
  projectId: number, format: 'txt' | 'csv' | 'json' = 'txt', signal?: AbortSignal,
): Promise<string> => {
  const response = await api.get(`${projectPath(projectId)}/export/out-of-scope?format_type=${format}`, { responseType: 'text', signal });
  return response.data;
};

// --- Subnet labels ---

export const listSubnetLabels = async (projectId: number, signal?: AbortSignal): Promise<SubnetLabelWithCounts[]> => {
  const response = await api.get(`${projectPath(projectId)}/scopes/subnet-labels`, { signal });
  return response.data;
};

export const createSubnetLabel = async (
  projectId: number,
  name: string,
  color?: string | null,
): Promise<SubnetLabelWithCounts> => {
  const response = await api.post(`${projectPath(projectId)}/scopes/subnet-labels`, { name, color: color ?? null });
  return response.data;
};

export const updateSubnetLabel = async (
  projectId: number,
  labelId: number,
  body: { name?: string; color?: string | null },
): Promise<SubnetLabelWithCounts> => {
  const response = await api.patch(`${projectPath(projectId)}/scopes/subnet-labels/${labelId}`, body);
  return response.data;
};

export const deleteSubnetLabel = async (projectId: number, labelId: number): Promise<void> => {
  await api.delete(`${projectPath(projectId)}/scopes/subnet-labels/${labelId}`);
};

// Idempotent: PUT the desired full label set on the subnet.  Anything
// not in `labelIds` is detached; anything missing is attached.
export const replaceSubnetLabels = async (
  projectId: number,
  subnetId: number,
  labelIds: number[],
): Promise<SubnetLabelInfo[]> => {
  const response = await api.put(`${projectPath(projectId)}/scopes/subnets/${subnetId}/labels`, { label_ids: labelIds });
  return response.data;
};
export const bulkApplySubnetLabel = async (
  projectId: number,
  labelId: number,
  subnetIds: number[],
): Promise<SubnetLabelWithCounts> => {
  const response = await api.post(`${projectPath(projectId)}/scopes/subnet-labels/${labelId}/subnets`, {
    subnet_ids: subnetIds,
  });
  return response.data;
};
