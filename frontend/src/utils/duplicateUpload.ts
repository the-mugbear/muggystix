/**
 * The 409 `duplicate_scan` refusal an upload can come back with.  Pure, so
 * the upload review hook can read it without loading the HTTP client
 * (v5.229.0 — moved out of services/api/uploads.ts, which re-exports it).
 */
export interface DuplicateUpload {
  scanId: number | null;
  jobId: number | null;
  /** The existing job's status; "staged" = that copy waits for its format
   *  review, which the upload dialog can take over (v5.271.0). */
  jobStatus: string | null;
  message: string;
}

/** The duplicate the server refused this upload as, or null for any other error. */
export function duplicateUploadOf(err: unknown): DuplicateUpload | null {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } } } | null;
  const detail = e?.response?.data?.detail as
    | { code?: unknown; scan_id?: unknown; job_id?: unknown; job_status?: unknown; message?: unknown }
    | undefined;
  if (e?.response?.status !== 409 || !detail || detail.code !== 'duplicate_scan') return null;
  return {
    scanId: typeof detail.scan_id === 'number' ? detail.scan_id : null,
    jobId: typeof detail.job_id === 'number' ? detail.job_id : null,
    jobStatus: typeof detail.job_status === 'string' ? detail.job_status : null,
    message: typeof detail.message === 'string' ? detail.message : 'This exact file is already imported.',
  };
}
