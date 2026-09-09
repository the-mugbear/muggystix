/**
 * Named-asset (FQDN inventory) API client — v5.193.0.
 *
 * A name is an identity; a host is an address.  The two link only through
 * uploaded evidence, and everything address-shaped here ("currently resolves
 * to", "previously") is derived server-side per request — never a stored
 * pointer, so it cannot go stale when DNS moves.  BlueStick never resolves
 * anything itself.
 *
 * Consumers import from ``../services/api`` — the barrel re-exports this.
 */
import { api, p } from './client';
import type { Paginated } from './shared';

export type NameKind = 'fqdn' | 'wildcard';

export type NameStateFilter =
  | 'all'
  | 'unresolved'
  | 'resolved'
  | 'in_scope'
  | 'out_of_scope'
  | 'wildcard'
  | 'shared';

export interface NameAddress {
  ip_address: string;
  /** Host row at that address in this project, if one exists.  Null is normal. */
  host_id: number | null;
  record_type: string;
  first_observed: string | null;
  last_observed: string | null;
  observations: number;
  /** Other names observed resolving to the same address (load-balancer signal). */
  shared_with: number;
}

export interface NameRow {
  id: number;
  fqdn: string;
  kind: NameKind;
  in_scope: boolean;
  first_seen: string | null;
  last_seen: string | null;
  current_addresses: NameAddress[];
  previous_address_count: number;
  /** Observation counts by kind: A, AAAA, CNAME, PTR, IMPORT, HTTP, CERT, ... */
  evidence: Record<string, number>;
  imported: boolean;
  resolved: boolean;
}

export interface NameObservation {
  id: number;
  record_type: string;
  value: string;
  domain: string;
  ttl: number | null;
  resolver_name: string | null;
  scan_id: number | null;
  scan_tool: string | null;
  scan_filename: string | null;
  observed_at: string | null;
  host_id: number | null;
}

export interface SiblingName {
  id: number;
  fqdn: string;
  ip_address: string;
}

export interface NameDetail extends NameRow {
  previous_addresses: NameAddress[];
  observations: NameObservation[];
  observations_total: number;
  sibling_names: SiblingName[];
}

export interface NamesSummary {
  total: number;
  unresolved: number;
  resolved: number;
  in_scope: number;
  wildcards: number;
  shared_addresses: number;
}

export interface NameImportRequest {
  names: string[];
  declare_scope: boolean;
  include_subdomains: boolean;
}

export interface NameImportResponse {
  names_created: number;
  names_existing: number;
  wildcards: number;
  observations_recorded: number;
  invalid_count: number;
  invalid: string[];
  scope_domains_added: number;
  scope_domains_updated: number;
  scope_invalid: string[];
}

export interface HostNameBinding {
  name_id: number;
  fqdn: string;
  kind: NameKind;
  in_scope: boolean;
  record_types: string[];
  last_observed: string | null;
}

export interface HostNamesResponse {
  host_id: number;
  /** Names whose CURRENT A/AAAA batch includes this address. */
  current: HostNameBinding[];
  /** Names that resolved here in an earlier scan but no longer do. */
  previous: HostNameBinding[];
  /** Names seen here only by non-resolving evidence (HTTP, CERT, SCANNER, PTR). */
  other: HostNameBinding[];
  in_scope_via_names: boolean;
}

export interface ScopeDomainRow {
  id: number;
  scope_id: number;
  domain: string;
  include_subdomains: boolean;
  description: string | null;
  created_at: string | null;
  /** Concrete names in the project this entry currently covers. */
  name_count: number;
}

/** A page of entries plus the deduplicated number of names they cover
 *  between them (per-row `name_count` overlaps when entries nest). */
export interface ScopeDomainPage extends Paginated<ScopeDomainRow> {
  names_in_scope_total: number;
}

export interface ScopeDomainBatchResponse {
  added: number;
  updated: number;
  invalid: string[];
  /** First page of the scope's domains after the write. */
  domains: ScopeDomainRow[];
  total: number;
  names_in_scope_total: number;
}

export const listNames = async (
  opts: {
    skip?: number;
    limit?: number;
    search?: string;
    state?: NameStateFilter;
    sort?: 'fqdn' | 'last_seen' | 'first_seen';
    order?: 'asc' | 'desc';
  } = {},
  signal?: AbortSignal,
): Promise<Paginated<NameRow>> => {
  const params = new URLSearchParams();
  if (opts.skip !== undefined) params.set('skip', String(opts.skip));
  if (opts.limit !== undefined) params.set('limit', String(opts.limit));
  if (opts.search && opts.search.trim()) params.set('search', opts.search.trim());
  if (opts.state && opts.state !== 'all') params.set('state', opts.state);
  if (opts.sort) params.set('sort', opts.sort);
  if (opts.order) params.set('order', opts.order);
  const qs = params.toString();
  const r = await api.get<Paginated<NameRow>>(`${p()}/names/${qs ? `?${qs}` : ''}`, { signal });
  return r.data;
};

export const getNamesSummary = async (): Promise<NamesSummary> => {
  const r = await api.get<NamesSummary>(`${p()}/names/summary`);
  return r.data;
};

export const getName = async (nameId: number): Promise<NameDetail> => {
  const r = await api.get<NameDetail>(`${p()}/names/${nameId}`);
  return r.data;
};

export const importNames = async (body: NameImportRequest): Promise<NameImportResponse> => {
  const r = await api.post<NameImportResponse>(`${p()}/names/import`, body);
  return r.data;
};

export const deleteName = async (nameId: number): Promise<void> => {
  await api.delete(`${p()}/names/${nameId}`);
};

export const getHostNames = async (hostId: number): Promise<HostNamesResponse> => {
  const r = await api.get<HostNamesResponse>(`${p()}/names/by-host/${hostId}`);
  return r.data;
};

// --- scope domains ---------------------------------------------------------

export const listScopeDomains = async (
  scopeId: number,
  opts: { skip?: number; limit?: number } = {},
): Promise<ScopeDomainPage> => {
  const params = new URLSearchParams();
  if (opts.skip !== undefined) params.set('skip', String(opts.skip));
  if (opts.limit !== undefined) params.set('limit', String(opts.limit));
  const qs = params.toString();
  const r = await api.get<ScopeDomainPage>(`${p()}/scopes/${scopeId}/domains${qs ? `?${qs}` : ''}`);
  return r.data;
};

export const addScopeDomains = async (
  scopeId: number,
  domains: Array<{ domain: string; include_subdomains?: boolean; description?: string }>,
): Promise<ScopeDomainBatchResponse> => {
  const r = await api.post<ScopeDomainBatchResponse>(`${p()}/scopes/${scopeId}/domains`, { domains });
  return r.data;
};

export const deleteScopeDomain = async (scopeId: number, domainId: number): Promise<void> => {
  await api.delete(`${p()}/scopes/${scopeId}/domains/${domainId}`);
};
