/**
 * Scopes API client — scopes, subnets, subnet labels, host-mappings,
 * coverage, and scope/out-of-scope host-list exports.
 *
 * Extracted from the api.ts monolith.  Consumers still import these from
 * ``../services/api`` — the barrel re-exports this module.
 */
import { api, p } from './client';

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

export const getScopeHostList = async (scopeId: number, format: 'txt' | 'csv' | 'json' = 'txt'): Promise<string> => {
  const response = await api.get(`${p()}/export/scope/${scopeId}?format_type=${format}`, { responseType: 'text' });
  return response.data;
};

export const getOutOfScopeHostList = async (format: 'txt' | 'csv' | 'json' = 'txt'): Promise<string> => {
  const response = await api.get(`${p()}/export/out-of-scope?format_type=${format}`, { responseType: 'text' });
  return response.data;
};

// --- Subnet labels ---

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
