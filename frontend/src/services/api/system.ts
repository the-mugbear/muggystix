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

export interface QueueMetrics {
  generated_at: string;
  ingestion: QueueSnapshot;
  report: QueueSnapshot;
}

export const getQueueMetrics = async (): Promise<QueueMetrics> => {
  const res = await api.get<QueueMetrics>('/system/queue-metrics');
  return res.data;
};
