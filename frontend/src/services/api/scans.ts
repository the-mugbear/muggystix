/**
 * Scans API client — scan list/detail/delete + scan-diff (attack-surface
 * delta between two scans).
 *
 * Extracted from the api.ts monolith (the v2.29.0 domain split left
 * hosts/scans/scopes/dashboard behind).  Consumers still import these
 * from ``../services/api`` — the barrel re-exports this module.
 */
import { api, p } from './client';

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
  // Username of the analyst who uploaded this scan (null for agent/recon
  // ingests or deleted users) — multi-analyst attribution.
  uploaded_by?: string | null;
  port_breakdown?: ScanPortBreakdown | null;
  vulnerability_summary?: ScanVulnerabilitySummary | null;
}

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

/**
 * What a scan delete actually removes. Hosts are deduplicated per-IP-per-
 * project, so deleting a scan only removes hosts seen by NO other scan
 * ("removed"); hosts shared with other scans are kept and re-pointed.
 */
export interface ScanDeletionImpact {
  scan_id: number;
  filename: string;
  hosts_removed: number;
  hosts_kept: number;
  sample_removed_ips: string[];
  ports_removed: number;
  vulnerabilities_removed: number;
  web_interfaces_removed: number;
}

export const getScanDeletionImpact = async (
  scanId: number,
): Promise<ScanDeletionImpact> => {
  const response = await api.get(`${p()}/scans/${scanId}/deletion-impact`);
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
