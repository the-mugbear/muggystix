/**
 * Deployment-level operational metrics (admin only).
 *
 * Global rather than project-scoped — queue health is a property of the
 * deployment. The backend has exposed GET /system/queue-metrics since the
 * durable-queue work, but no client module existed, so nothing in the app or
 * in scripts/ ever read it: an operator could only learn a worker had stalled
 * by noticing their uploads never finished.
 */
import { api } from './client';

export interface QueueSnapshot {
  queued: number;
  processing: number;
  failed: number;
  /** In-flight jobs past the reaper's cutoff — a worker died holding them. */
  stale_processing: number;
  oldest_queued_age_seconds: number;
  completed_last_hour: number;
  /** Mean seconds from start to completion over the last hour; null if idle. */
  avg_processing_seconds: number | null;
  stale_cutoff_seconds: number;
}

/** Undismissed failed ingestion jobs in one project (largest first). */
export interface FailedJobsInProject {
  project_id: number;
  project_name: string;
  count: number;
}

export interface QueueMetrics {
  generated_at: string;
  /** `failed` counts undismissed jobs; `failed_by_project` says where they are
   *  (the list that shows them — Ingestion Results — is per project). */
  ingestion: QueueSnapshot & { failed_by_project?: FailedJobsInProject[] };
  report: QueueSnapshot;
  /** v5.302.0 — free space on the uploads filesystem (on one host, the disk
   *  Postgres and Docker share); null when it could not be read. */
  disk?: DiskSnapshot | null;
}

export interface DiskSnapshot {
  total_bytes: number;
  free_bytes: number;
  /** Below 10 GB or 10% free. */
  low: boolean;
}

export const getQueueMetrics = async (): Promise<QueueMetrics> => {
  const res = await api.get<QueueMetrics>('/system/queue-metrics');
  return res.data;
};
