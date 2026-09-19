/**
 * File uploads, ingestion jobs, ingestion results — the async upload pipeline.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api, p } from './client';



export interface FileUploadResponse {
  job_id: number;
  filename: string;
  status: string;
  message: string;
  scan_id: number | null;
  parse_error_id?: number | null;
}

export interface IngestionJob {
  id: number;
  filename: string;
  original_filename: string;
  status: string;
  message?: string;
  error_message?: string;
  tool_name?: string;
  file_size?: number;
  scan_id?: number | null;
  parse_error_id?: number | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  // Final import-count summary set at completion (e.g. "6 DNS records").
  // Fast JSON parsers (dnsx/httpx/…) don't stream progress, so this is where
  // their record count surfaces in the recent-jobs list.
  progress?: string | null;
  // v2.86.2 — operator-set dismissal marker; non-null means the user
  // acknowledged this failed row and it should drop out of the queue.
  dismissed_at?: string | null;
  // Dead-letter / liveness columns (backend already returns these). retry_count
  // shows how many times the job bounced before succeeding/failing; last_error
  // is the most recent failure reason; last_heartbeat is the worker's last
  // progress tick (stale while 'processing' ⇒ a stalled job).
  retry_count?: number | null;
  last_error?: string | null;
  last_heartbeat?: string | null;
  // Ingestion quality. A job can complete successfully and still have lost
  // data — malformed rows skipped, or a truncated file that stopped the parse
  // early. Without these a degraded import is indistinguishable from a clean
  // one, which for scan data means missing hosts look like absent hosts.
  skipped_count?: number | null;
  parser_warnings?: string | null;
  /** v5.204.0 — the parser stopped early (truncated file). Distinct from
   *  skipped_count: a truncated file loses an unknown number of records. */
  partial?: boolean;
}


export interface UploadOptions {
  /** Upload batch (createScanBatch) this file belongs to. */
  batchId?: number;
  /** Import even though this exact file is already a scan — a deliberate
   *  re-import, e.g. to re-parse after a parser fix. */
  allowDuplicate?: boolean;
  /** v5.215.0 — Nessus only: drop severity-0 (informational) report items
   *  instead of storing a vulnerability row each (ports still derived from
   *  them). Omit to use the project's setting. */
  skipInformational?: boolean;
  /** v5.228.0 — store the file as a STAGED job instead of queuing it; review
   *  getJobDetection, then startIngestionJob. Expires after 24h unstarted. */
  stage?: boolean;
}

/** v5.228.0 — what the worker would make of a staged file, and why. */
export interface DetectionCandidate {
  file_type: string;
  label: string;
  /** structure = the content selects it; filename = only the name does. */
  basis: 'structure' | 'filename';
  rank: number;
}

export interface FormatOption {
  file_type: string;
  label: string;
  family: string;
}

export interface DetectionResponse {
  job_id: number;
  filename: string;
  candidates: DetectionCandidate[];
  primary: string | null;
  needs_choice: boolean;
  reason: string | null;
  preview: { raw: string; sample: string[] };
  /** Every format the dispatcher knows, for the chooser. */
  formats: FormatOption[];
}

export const getJobDetection = async (jobId: number): Promise<DetectionResponse> => {
  const response = await api.get(`${p()}/upload/jobs/${jobId}/detection`);
  return response.data;
};

/** v5.231.0 — re-process a finished job's retained file as a NEW job. A new
 *  scan record is created; the prior scan stays; the duplicate guard is
 *  bypassed on purpose. */
export const reprocessIngestionJob = async (
  jobId: number,
  options: { formatOverride?: string | null; sourceTool?: string | null } = {},
): Promise<IngestionJob> => {
  const response = await api.post(`${p()}/upload/jobs/${jobId}/reprocess`, {
    format_override: options.formatOverride ?? null,
    source_tool: options.sourceTool ?? null,
  });
  return response.data;
};

/** Start a staged job, or retry a failed one on its retained file, optionally
 *  as a chosen format (the worker then runs exactly that parser). */
export const startIngestionJob = async (
  jobId: number,
  options: { formatOverride?: string | null; sourceTool?: string | null } = {},
): Promise<IngestionJob> => {
  const response = await api.post(`${p()}/upload/jobs/${jobId}/start`, {
    format_override: options.formatOverride ?? null,
    source_tool: options.sourceTool ?? null,
  });
  return response.data;
};

/** A refused identical upload (409 duplicate_scan): what it already is. */
// v5.229.0 — pure helper, lives in utils so the upload review hook can read
// a refusal without loading the HTTP client; re-exported here unchanged.
export { duplicateUploadOf } from '../../utils/duplicateUpload';
export type { DuplicateUpload } from '../../utils/duplicateUpload';

export const uploadFile = async (
  file: File,
  onProgress?: (percent: number) => void,
  options: UploadOptions = {},
): Promise<FileUploadResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  if (options.batchId != null) formData.append('batch_id', String(options.batchId));
  if (options.allowDuplicate) formData.append('allow_duplicate', 'true');
  if (options.stage) formData.append('stage', 'true');
  if (options.skipInformational != null) {
    formData.append('skip_informational', options.skipInformational ? 'true' : 'false');
  }

  // Bypass axios for the upload and use a raw XMLHttpRequest.  We
  // tried the axios path twice (with explicit Content-Type and with
  // Content-Type: null) and `onUploadProgress` still didn't fire on
  // the deployed build — likely because axios 1.7's adapter
  // selection or its FormData handling drops the progress hook in
  // this configuration.  Raw XHR is the lowest-level path and
  // gives us guaranteed access to xhr.upload.onprogress, which is
  // what the browser fires on every TCP write.
  //
  // We still mirror axios's response-shape conventions and the
  // request interceptor's Bearer-token injection so the rest of the
  // app is unaffected.
  return new Promise<FileUploadResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    // Build the URL the same way axios would.  ``p()`` already
    // includes the project prefix; we prepend the api base URL.
    const baseUrl = (api.defaults.baseURL ?? '').replace(/\/$/, '');
    xhr.open('POST', `${baseUrl}${p()}/upload/`, true);

    // Match the request interceptor's auth header.
    const token = localStorage.getItem('auth_token');
    if (token) {
      xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    }
    // Critically: DO NOT set Content-Type.  The browser will set it
    // to `multipart/form-data; boundary=…` automatically when it
    // serialises the FormData body.

    if (onProgress) {
      xhr.upload.onprogress = (evt) => {
        if (evt.lengthComputable && evt.total > 0) {
          onProgress(Math.round((evt.loaded / evt.total) * 100));
        }
      };
    }

    xhr.onload = () => {
      // Mirror axios's response-shape conventions for the auth
      // interceptor (401 -> redirect to login, 403 with
      // password_change_required detail -> /force-change-password).
      if (xhr.status === 401) {
        localStorage.removeItem('auth_token');
        localStorage.removeItem('auth_user');
        if (window.location.pathname !== '/login') {
          window.location.href = '/login';
        }
        reject(new Error('Unauthorized'));
        return;
      }
      // Parse JSON body once; fall back to text for non-JSON
      // responses (rare on this endpoint but safer than throwing).
      let body: any = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = xhr.responseText;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as FileUploadResponse);
      } else {
        // Mirror the AxiosError shape so callers' `formatApiError`
        // helpers keep working (they look at `err.response.data`).
        // detail is a string for most errors and { code, message, … } for
        // structured ones (409 duplicate_scan).
        const detailText = typeof body?.detail === 'string' ? body.detail : body?.detail?.message;
        const err: any = new Error(
          detailText || body?.message || `Upload failed (HTTP ${xhr.status})`,
        );
        err.response = { status: xhr.status, data: body };
        reject(err);
      }
    };

    xhr.onerror = () => {
      reject(new Error('Network error during upload'));
    };
    xhr.onabort = () => {
      reject(new Error('Upload aborted'));
    };

    xhr.send(formData);
  });
};

export const getIngestionJob = async (jobId: number): Promise<IngestionJob> => {
  const response = await api.get(`${p()}/upload/jobs/${jobId}`);
  return response.data;
};

export const getRecentIngestionJobs = async (limit = 5): Promise<IngestionJob[]> => {
  const response = await api.get(`${p()}/upload/jobs?limit=${limit}`);
  return response.data;
};

// v2.86.2 — dismiss a failed ingestion job so it drops out of the
// live queue.  Backend rejects non-failed status with 400 and other
// users' jobs with 403 (admins can dismiss anyone's).
export const dismissIngestionJob = async (jobId: number): Promise<IngestionJob> => {
  const response = await api.post(`${p()}/upload/jobs/${jobId}/dismiss`);
  return response.data;
};

// Cancel a queued/processing ingestion job. Backend (POST /upload/jobs/{id}/cancel)
// marks it failed and the worker's atomic completion guard won't resurrect it;
// rejects already-terminal jobs (409) and non-owner/non-admin (403).
export const cancelIngestionJob = async (
  jobId: number,
): Promise<{ job_id: number; status: string; message: string }> => {
  const response = await api.post(`${p()}/upload/jobs/${jobId}/cancel`);
  return response.data;
};

// Retry a FAILED ingestion job whose uploaded file is still on disk
// (POST /upload/jobs/{id}/retry) — re-queues without re-uploading. Backend
// 409s if the job isn't failed or the file was already cleaned up.
export const retryIngestionJob = async (
  jobId: number,
): Promise<{ job_id: number; status: string; message: string }> => {
  const response = await api.post(`${p()}/upload/jobs/${jobId}/retry`);
  return response.data;
};


// Ingestion Results API
export interface IngestionResultItem {
  /** The INGESTION JOB id — not a ParseError id. The two are independent
   *  sequences that overlap, so passing this where a parse-error id is
   *  expected silently returns a different file's error rather than 404ing.
   *  Use `parse_error_id` to address the ParseError. */
  id: number;
  /** The ParseError this job produced, when it produced one. */
  parse_error_id?: number | null;
  original_filename: string;
  status: string;
  file_size: number | null;
  tool_name: string | null;
  scan_type: string | null;
  scan_id: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  progress: string | null;
  /** v2.351.0 — the format chain: detected first, operator override (if
   *  any), what actually parsed the file, and the tool they named. */
  detected_file_type?: string | null;
  detected_format_label?: string | null;
  format_override?: string | null;
  format_override_label?: string | null;
  final_file_type?: string | null;
  final_format_label?: string | null;
  source_tool?: string | null;
  /** v2.354.0 — the uploaded bytes are still on disk, until when. */
  file_retained?: boolean;
  retained_until?: string | null;
  stats: {
    hosts_parsed: number;
    hosts_up: number;
    ports_found: number;
    open_ports: number;
    services_detected: number;
  } | null;
  error: {
    error_type: string | null;
    error_message: string | null;
    user_message: string | null;
  } | null;
}

export interface IngestionResultsResponse {
  items: IngestionResultItem[];
  total: number;
  summary: {
    total_completed: number;
    total_failed: number;
    total_queued: number;
    total_processing: number;
    total_hosts: number;
    total_hosts_up: number;
    total_ports: number;
    total_open_ports: number;
  };
}

export type IngestionResultsSortBy =
  | 'created_at' | 'original_filename' | 'status' | 'tool_name' | 'file_size';

export interface IngestionResultsQuery {
  skip?: number;
  limit?: number;
  // v2.86.2 — server-side filter + sort knobs.  When unset the
  // backend default (created_at desc, no filter) applies.
  status?: string;
  tool?: string;
  search?: string;
  sortBy?: IngestionResultsSortBy;
  sortOrder?: 'asc' | 'desc';
}

export const getIngestionResults = async (
  query: IngestionResultsQuery = {},
): Promise<IngestionResultsResponse> => {
  const params = new URLSearchParams();
  if (query.skip !== undefined) params.set('skip', String(query.skip));
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  if (query.status) params.set('status', query.status);
  if (query.tool) params.set('tool', query.tool);
  if (query.search) params.set('search', query.search);
  if (query.sortBy) params.set('sort_by', query.sortBy);
  if (query.sortOrder) params.set('sort_order', query.sortOrder);
  const qs = params.toString();
  const response = await api.get(`${p()}/parse-errors/ingestion-results${qs ? `?${qs}` : ''}`);
  return response.data;
};
