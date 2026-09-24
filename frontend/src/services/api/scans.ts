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
  /** Findings FIRST recorded by this scan. */
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  // v2.333.0
  hosts_affected?: number;
  hosts_critical_high?: number;
  exploitable?: number;
}

/**
 * Where a scan's start/end came from (backend models.SCAN_TIME_SOURCES).
 * tool_run / tool_records arrive with a UTC offset and are converted to the
 * viewer's zone; tool_clock is the scanner's zone-less wall clock and arrives
 * WITHOUT an offset — show it as written. null + start_time = legacy row
 * (assumed UTC); null start_time = the file carries no scan time.
 */
export type ScanTimeSource = 'tool_run' | 'tool_records' | 'tool_clock';

/** Web interfaces a scan wrote (v2.333.0). */
export interface ScanWebSummary {
  interfaces: number;
  new_urls: number;
  hosts: number;
  https: number;
  status_2xx: number;
  status_3xx: number;
  status_4xx: number;
  status_5xx: number;
  cert_expired: number;
  cert_self_signed: number;
  weak_tls: number;
  screenshots: number;
}

/** Name observations a scan wrote (v2.333.0). */
export interface ScanDnsSummary {
  records: number;
  names: number;
  new_names: number;
  /** record_type -> count; includes observation kinds (DISCOVERED, SCANNER, HTTP, CERT, IMPORT). */
  by_type: Record<string, number>;
}

/** netexec results a scan wrote (v2.333.0). */
export interface ScanAuthSummary {
  hosts: number;
  protocols: string[];
  valid_accounts: number;
}

export interface ScanPortBreakdown {
  unique_ports: number;
  open_tcp_ports: number;
  open_udp_ports: number;
  /** Open ports this scan introduced (v2.333.0). */
  new_open_ports?: number;
  /** Open ports this scan named a service for (v2.333.0). */
  open_with_service?: number;
}

export interface Scan {
  id: number;
  filename: string;
  scan_type: string | null;
  tool_name: string | null;
  start_time?: string | null;
  end_time?: string | null;
  time_source?: ScanTimeSource | null;
  created_at: string;
  total_hosts: number;
  up_hosts: number;
  // What this scan introduced vs re-observed. new + updated == total_hosts.
  new_hosts: number;
  updated_hosts: number;
  total_ports: number;
  open_ports: number;
  command_line?: string | null;
  version?: string | null;
  // Username of the analyst who uploaded this scan (null for agent/recon
  // ingests or deleted users) — multi-analyst attribution.
  uploaded_by?: string | null;
  /** v2.401.0 — their full name, else the username: what the page shows. */
  uploaded_by_name?: string | null;
  port_breakdown?: ScanPortBreakdown | null;
  vulnerability_summary?: ScanVulnerabilitySummary | null;
  // v2.333.0 — hosts it fingerprinted an OS for, and per-kind blocks
  // (present only when the scan wrote such rows).
  os_fingerprinted?: number;
  web?: ScanWebSummary | null;
  dns?: ScanDnsSummary | null;
  auth?: ScanAuthSummary | null;
  /** v5.207.0 — the upload batch this file arrived in, if any. */
  batch_id?: number | null;
  batch_label?: string | null;
  // v2.346.0 — the import result: conflicts this scan raised (scans disagreed
  // on a host/port value) and the ingestion job's quality trio.
  conflicts?: number;
  import_job_id?: number | null;
  import_skipped?: number;
  import_partial?: boolean;
  import_warnings?: string | null;
  // v2.358.0 — the format chain, as labels: what detection said, what the
  // operator chose (if anything), which parser read the file, the named tool.
  import_detected_format?: string | null;
  import_format_override?: string | null;
  import_final_format?: string | null;
  import_source_tool?: string | null;
}

export const getScans = async (
  skip = 0,
  limit = 100,
  options?: {
    search?: string;
    tool?: string;
    createdAfter?: string;
    /** Only files uploaded by this user id. */
    uploadedBy?: number;
    sortBy?: 'created_at' | 'start_time' | 'filename' | 'tool_name' | 'file_size' | 'duration_seconds' | 'total_hosts' | 'new_hosts';
    sortOrder?: 'asc' | 'desc';
    /** Only the files of this upload batch. */
    batchId?: number;
    /** Leave out files that belong to a batch (they're listed per batch). */
    unbatched?: boolean;
    /** Only these scans — how a completed job fetches its import result. */
    ids?: number[];
    signal?: AbortSignal;
  },
): Promise<Scan[]> => {
  const { search, tool, createdAfter, uploadedBy, sortBy, sortOrder, batchId, unbatched, ids, signal } = options ?? {};
  const params: Record<string, string | number | boolean> = { skip, limit };
  if (ids && ids.length) params.ids = ids.join(',');
  if (search) params.search = search;
  if (tool) params.tool = tool;
  if (createdAfter) params.created_after = createdAfter;
  if (uploadedBy != null) params.uploaded_by = uploadedBy;
  if (sortBy) params.sort_by = sortBy;
  if (sortOrder) params.sort_order = sortOrder;
  if (batchId != null) params.batch_id = batchId;
  else if (unbatched) params.unbatched = true;
  const response = await api.get(`${p()}/scans/`, { params, signal });
  return response.data;
};

/** One upload batch on /scans — files matching the page filters and what
 *  they added together (backend ScanBatchSummary, v2.335.0). */
export interface ScanBatchSummary {
  id: number;
  label: string;
  created_at?: string | null;
  created_by?: string | null;
  recon_session_id?: number | null;
  files: number;
  tools: string[];
  hosts: number;
  new_hosts: number;
  open_ports: number;
  first_uploaded?: string | null;
  last_uploaded?: string | null;
  pending_files: number;
  failed_files: number;
  // v2.350.0 — `files` is the MATCHING imported files; these are the rest of
  // the honest breakdown.  A refused duplicate never creates a job.
  total_files?: number;
  imported_files?: number;
  processing_files?: number;
  /** Uploaded and waiting for the operator's format review. */
  staged_files?: number;
  /** Staged files the operator discarded before import. */
  discarded_files?: number;
  // v2.401.0 — every other reason a file was not imported, so a batch with
  // nothing imported always says why.
  /** Staged files nobody started before the staged-upload expiry. */
  expired_files?: number;
  /** Failures already dismissed (not discards or expiries). */
  dismissed_failed_files?: number;
  /** Imported files that are re-imports of an earlier file of the batch. */
  reprocessed_files?: number;
  /** v2.402.0 — every file that reached the server for this batch (one
   *  ingestion job each, any state). A file refused at upload (duplicate)
   *  never becomes a job, so a generated label's count can be higher. */
  uploaded_files?: number;
  /** v2.402.0 — files whose import was cancelled. */
  cancelled_files?: number;
  /** v2.403.0 — failed here, not dismissed, but the same file was imported
   *  by a later job; not counted in `failed_files`. */
  superseded_files?: number;
  /** The creator's full name, else username. */
  created_by_name?: string | null;
}

/** v5.239.0 — the ORDER of the import history: upload batches and individually
 *  uploaded files in one chronological list. Rows are hydrated by id through
 *  getScans / getScanBatches, which already compute their summaries. */
export interface ImportHistoryEntry {
  kind: 'batch' | 'scan';
  id: number;
  at: string | null;
}

export interface ImportHistoryPage {
  items: ImportHistoryEntry[];
  total: number;
  batch_total: number;
  scan_total: number;
  has_more: boolean;
}

export const getImportHistory = async (
  options?: {
    search?: string; tool?: string; createdAfter?: string; uploadedBy?: number;
    skip?: number; limit?: number; signal?: AbortSignal;
  },
): Promise<ImportHistoryPage> => {
  const { search, tool, createdAfter, uploadedBy, skip, limit, signal } = options ?? {};
  const params: Record<string, string | number> = {};
  if (search) params.search = search;
  if (tool) params.tool = tool;
  if (createdAfter) params.created_after = createdAfter;
  if (uploadedBy != null) params.uploaded_by = uploadedBy;
  if (skip) params.skip = skip;
  if (limit) params.limit = limit;
  const response = await api.get(`${p()}/scans/history`, { params, signal });
  return response.data;
};

export const getScanBatches = async (
  options?: {
    search?: string; tool?: string; createdAfter?: string; uploadedBy?: number; skip?: number; limit?: number;
    /** Only these batches (a history page's). */
    ids?: number[];
    signal?: AbortSignal;
  },
): Promise<ScanBatchSummary[]> => {
  const { search, tool, createdAfter, uploadedBy, skip, limit, ids, signal } = options ?? {};
  const params: Record<string, string | number> = {};
  if (ids) params.ids = ids.join(',');
  if (search) params.search = search;
  if (tool) params.tool = tool;
  if (createdAfter) params.created_after = createdAfter;
  if (uploadedBy != null) params.uploaded_by = uploadedBy;
  if (skip) params.skip = skip;
  if (limit) params.limit = limit;
  const response = await api.get(`${p()}/scans/batches`, { params, signal });
  return response.data;
};

/** Start an upload batch for a multi-file upload; send its id with each file. */
export const createScanBatch = async (
  label: string,
): Promise<{ id: number; label: string; created_at?: string | null }> => {
  const response = await api.post(`${p()}/scans/batches`, { label });
  return response.data;
};

/** Name an operator's upload batch (it is created with a generated label the
 *  moment files are dropped). An agent's batch is refused with 409: it is
 *  keyed by its label within the recon session. */
export const renameScanBatch = async (
  batchId: number,
  label: string,
): Promise<{ id: number; label: string; created_at?: string | null }> => {
  const response = await api.patch(`${p()}/scans/batches/${batchId}`, { label });
  return response.data;
};

/** Cheap change detector for an open /scans page. */
export interface ScanInventoryMarker {
  count: number;
  latest_id: number | null;
}

export const getScanInventoryMarker = async (): Promise<ScanInventoryMarker> => {
  const response = await api.get(`${p()}/scans/inventory-marker`);
  return response.data;
};

/**
 * Filter-aware totals for the /scans headline cards. The list is paginated
 * ("Load more"), so summing the loaded page under-reports once a project has
 * more scans than one page holds — this carries the true totals across every
 * scan matching the active filters.
 */
export interface ScanInventorySummary {
  total_scans: number;
  total_hosts: number;
  up_hosts: number;
  open_services: number;
  // v2.350.0 — files per tool (keys upper-cased) over the search/date
  // filters, batched files included, ignoring the tool filter.
  tool_counts?: Record<string, number>;
  total_files?: number;
  /** v2.396.0 — who uploaded the matching files (not narrowed by the
   *  uploader filter itself), most files first. */
  uploaders?: ScanUploader[];
  /** v2.401.0 — the WHOLE project's imports that failed or finished partial
   *  and nobody dismissed (Ingestion Results' needs-attention view). */
  imports_need_attention?: number;
  /** v2.401.0 — failed jobs already dismissed: discarded, expired, acknowledged. */
  imports_not_imported?: number;
  /** v2.402.0 — `imports_not_imported` by reason (`discarded`, `expired`,
   *  `dismissed`), zero reasons left out; they sum to it. */
  imports_not_imported_by_reason?: Record<string, number>;
  /** v2.403.0 — failed or partial imports (not dismissed) whose file a later
   *  job imported: not in `imports_need_attention`. */
  imports_superseded?: number;
}

export interface ScanUploader {
  user_id: number;
  username: string;
  /** v2.401.0 — displayed; the id stays the filter value. */
  full_name?: string | null;
  files: number;
}

export const getScansSummary = async (
  options?: {
    search?: string;
    tool?: string;
    createdAfter?: string;
    uploadedBy?: number;
    signal?: AbortSignal;
  },
): Promise<ScanInventorySummary> => {
  const { search, tool, createdAfter, uploadedBy, signal } = options ?? {};
  const params: Record<string, string | number> = {};
  if (search) params.search = search;
  if (tool) params.tool = tool;
  if (createdAfter) params.created_after = createdAfter;
  if (uploadedBy != null) params.uploaded_by = uploadedBy;
  const response = await api.get(`${p()}/scans/summary`, { params, signal });
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
  /** Findings first recorded by this scan on surviving hosts: kept, lose
   *  that attribution (v5.204.0 — they used to be deleted with the scan). */
  vulnerabilities_detached: number;
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
  /** Open in A; B tested the port and found it not open. */
  closed_ports: number;
  /** Open in A; B has no observation for the port. Unknown, not closed. */
  not_observed_ports: number;
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
  not_observed_ports: ScanDiffPortChange[];
}

export const compareScans = async (a: number, b: number): Promise<ScanDiffResponse> => {
  const response = await api.get(`${p()}/scans/compare`, { params: { a, b } });
  return response.data;
};
