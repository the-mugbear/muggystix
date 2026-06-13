/**
 * File uploads, ingestion jobs, ingestion results — the async upload pipeline.
 *
 * v2.29.0 — extracted from services/api.ts.  api.ts re-exports
 * everything from here so consumers can keep importing from
 * ``../services/api`` unchanged.
 */
import { api, p } from './client';


interface DnsConfig {
  enabled: boolean;
  server?: string;
}



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
}


export const uploadFile = async (
  file: File,
  dnsConfig: DnsConfig = { enabled: false },
  onProgress?: (percent: number) => void,
): Promise<FileUploadResponse> => {
  const formData = new FormData();
  formData.append('file', file);
  if (dnsConfig.enabled) {
    formData.append('enrich_dns', 'true');
    if (dnsConfig.server) {
      formData.append('dns_server', dnsConfig.server);
    }
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
        const err: any = new Error(
          body?.detail || body?.message || `Upload failed (HTTP ${xhr.status})`,
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
  id: number;
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
