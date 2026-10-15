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
  ingestion: QueueSnapshot & { failed_by_project: FailedJobsInProject[] };
  report: QueueSnapshot;
  /** v5.302.0 — free space on the uploads filesystem (on one host, the disk
   *  Postgres and Docker share); null when it could not be read. */
  disk: DiskSnapshot | null;
  /** v5.389.0 — what the host and each process are using.  Each is null /
   *  empty where the server could not read it.  (Required: backend and
   *  frontend ship together — third review 2026-10-15, R5.) */
  host: HostSnapshot | null;
  /** The API process's own memory against its container's limit. */
  backend_memory: ProcessMemory | null;
  /** Each worker's last report: only where the kernel keeps a peak. */
  workers: WorkerMemory[];
  database: DatabaseSnapshot | null;
}

export interface HostSnapshot {
  memory_total_bytes: number | null;
  memory_available_bytes: number | null;
  /** Under a tenth of the host's memory available. */
  memory_low: boolean;
  load_1: number | null;
  load_5: number | null;
  load_15: number | null;
  cpu_count: number | null;
  /** The 5-minute load is above the number of CPUs. */
  load_high: boolean;
}

export interface ProcessMemory {
  current_bytes: number;
  /** The processes' own memory (file cache left out); null where the kernel
   *  does not say (2.505.0). */
  own_bytes: number | null;
  /** The most it has used since it started, file cache included —
   *  information; null where the kernel keeps none. */
  peak_bytes: number | null;
  /** Null: the container has no limit. */
  limit_bytes: number | null;
  /** Processes the kernel killed at the limit since the container started
   *  (2.505.0). */
  oom_kills: number;
  /** Its OWN memory (else its current use) reached 90% of its limit — not
   *  the peak, which counts cache the kernel frees by itself (third review
   *  2026-10-15, S2). */
  near_limit: boolean;
}

export interface WorkerMemory extends ProcessMemory {
  /** The queue the worker serves: `ingestion_jobs` or `report_jobs`. */
  name: string;
  peak_bytes: number;
  age_seconds: number;
  /** No report for two minutes: killed, stopped or wedged. */
  stale: boolean;
}

export interface DatabaseSnapshot {
  size_bytes: number;
  connections: number;
  max_connections: number;
  /** At 85% of the connections the database accepts. */
  connections_high: boolean;
}

export interface DiskSnapshot {
  total_bytes: number;
  free_bytes: number;
  /** Below 10 GB or 10% free. */
  low: boolean;
}

export const getQueueMetrics = async (signal?: AbortSignal): Promise<QueueMetrics> => {
  const res = await api.get<QueueMetrics>('/system/queue-metrics', { signal });
  return res.data;
};
