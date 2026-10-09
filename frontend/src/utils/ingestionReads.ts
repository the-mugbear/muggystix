/**
 * The reads that list ingestion jobs or count them — the ONE list of their
 * names.  A change to a job (retry, start, re-process, cancel, dismiss,
 * discard) puts them out of date:
 *
 *   onSuccess: () => invalidateReads(queryClient, ...INGESTION_JOB_READS)
 *
 * A new read that lists or counts ingestion jobs is added here.
 */
export const INGESTION_JOB_READS = [
  'getRecentIngestionJobs',
  'getStagedIngestionJobs',
  'getScansSummary',
  'getBatchUnimportedJobs',
  'getIngestionResults',
] as const;
