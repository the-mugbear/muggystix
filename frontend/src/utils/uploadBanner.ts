/**
 * The Scans page's upload banner, as a derivation (UI_STYLE_GUIDE §48).
 *
 * The page keeps only what the browser itself knows — the files this tab
 * started and which of their rows the reader dismissed (`BannerEntry`).  What
 * the SERVER knows is read by two queries and laid over the entries while
 * rendering: a job's status and message (`FollowedJobs`), a finished file's
 * import result (the scan rows).  Nothing here is state, and nothing here
 * talks to the server.
 *
 *   received → processing → imported | partial | failed
 */
import type { IngestionJob, Scan } from '../services/api';

/** A file this tab started: what the review handed over, and the reader's dismissal. */
export interface BannerEntry {
  /** The review row's key: one banner row per started file. */
  key: string;
  filename: string;
  /** The ingestion job the server gave the file. */
  jobId: number;
  /** The reader closed the row.  The entry stays, so the jobs that are
   *  followed — and with them the read's key — do not change by dismissing. */
  dismissed?: boolean;
}

/** What the server has said about the jobs this tab started: each job as it
 *  was last read, and the ids it was asked about and did not return — a job
 *  that no longer exists, or is not this user's. */
export interface FollowedJobs {
  jobs: IngestionJob[];
  gone: number[];
}

export type BannerStatus = 'received' | 'processing' | 'imported' | 'partial' | 'failed';

/** What one row of the banner shows. */
export interface BannerRow {
  key: string;
  filename: string;
  jobId: number;
  status: BannerStatus;
  /** Why a failed import failed, in the server's words. */
  error?: string;
  /** The worker's latest message (while processing; a completed job's summary). */
  jobMessage: string | null;
  /** The scan row's summary once the job completed and its result was read. */
  result: Scan | null;
  parseErrorId: number | null;
}

/** Nothing more will happen to the job: it is not asked about by the interval again. */
export const jobIsFinished = (job: Pick<IngestionJob, 'status'>): boolean =>
  job.status === 'completed' || job.status === 'failed';

/** Whether the poll goes on.  With no answer yet it does: nothing is known. */
export const anyStillRunning = (followed: FollowedJobs | undefined): boolean =>
  !followed || followed.jobs.some((job) => !jobIsFinished(job));

/** Which of the started jobs the next read names: those not yet known to be
 *  finished or gone.  A finished job is never asked about again — the old
 *  per-file poll's rule, kept: one request a tick, naming only what can
 *  still change. */
export function jobIdsToAsk(started: readonly number[], known: FollowedJobs | undefined): number[] {
  if (!known) return [...started];
  const over = new Set([...known.jobs.filter(jobIsFinished).map((job) => job.id), ...known.gone]);
  return started.filter((id) => !over.has(id));
}

/** The reading after an answer: the finished jobs as they were last read
 *  (they were not asked about), the jobs just returned, and — as gone — the
 *  ids that were asked about and not returned. */
export function mergeFollowed(
  known: FollowedJobs | undefined, asked: readonly number[], answer: readonly IngestionJob[],
): FollowedJobs {
  const returned = new Set(answer.map((job) => job.id));
  const kept = (known?.jobs ?? []).filter((job) => jobIsFinished(job) && !returned.has(job.id));
  const gone = new Set([...(known?.gone ?? []), ...asked.filter((id) => !returned.has(id))]);
  return { jobs: [...kept, ...answer], gone: [...gone] };
}

/** The jobs nothing more is expected of — finished, or no longer returned —
 *  and, among them, those that completed.  Sorted, so the same set is the
 *  same list whatever order the server answered in. */
export function settledJobs(followed: FollowedJobs | undefined): { settled: number[]; completed: number[] } {
  if (!followed) return { settled: [], completed: [] };
  const ascending = (a: number, b: number) => a - b;
  const finished = followed.jobs.filter(jobIsFinished).map((job) => job.id);
  const completed = followed.jobs.filter((job) => job.status === 'completed').map((job) => job.id);
  return {
    settled: [...new Set([...finished, ...followed.gone])].sort(ascending),
    completed: [...new Set(completed)].sort(ascending),
  };
}

/** The scans whose import result the banner shows: one per completed job that
 *  made a scan.  Sorted and without repeats — it is a query key. */
export function completedScanIds(followed: FollowedJobs | undefined): number[] {
  if (!followed) return [];
  const ids = followed.jobs
    .filter((job) => job.status === 'completed' && job.scan_id != null)
    .map((job) => job.scan_id as number);
  return [...new Set(ids)].sort((a, b) => a - b);
}

/** One entry with what the server said about its job and its result. */
export function bannerRow(entry: BannerEntry, job: IngestionJob | undefined, result: Scan | undefined): BannerRow {
  const row: BannerRow = {
    key: entry.key, filename: entry.filename, jobId: entry.jobId,
    status: 'received', jobMessage: null, result: null, parseErrorId: null,
  };
  // Not answered about yet, no longer returned, or in a state the banner has
  // no word for: the file is stored and that is all that is known.
  if (!job) return row;
  if (job.status === 'queued' || job.status === 'processing') {
    return { ...row, status: 'processing', jobMessage: job.message ?? null };
  }
  if (job.status === 'failed') {
    return {
      ...row,
      status: 'failed',
      error: job.failure_reason || job.error_message || job.last_error || job.message || 'Import failed',
      parseErrorId: job.parse_error_id ?? null,
    };
  }
  if (job.status === 'completed') {
    const gaps = (job.skipped_count ?? 0) > 0 || !!job.partial;
    return {
      ...row,
      status: gaps ? 'partial' : 'imported',
      jobMessage: job.message ?? null,
      result: result ?? null,
    };
  }
  return row;
}

/** The banner: one row per started file the reader has not dismissed, in the
 *  order the files were started. */
export function bannerRows(
  entries: readonly BannerEntry[],
  followed: FollowedJobs | undefined,
  results: readonly Scan[] | undefined,
): BannerRow[] {
  const jobs = new Map((followed?.jobs ?? []).map((job) => [job.id, job]));
  const scans = new Map((results ?? []).map((scan) => [scan.id, scan]));
  return entries
    .filter((entry) => !entry.dismissed)
    .map((entry) => {
      const job = jobs.get(entry.jobId);
      return bannerRow(entry, job, job?.scan_id != null ? scans.get(job.scan_id) : undefined);
    });
}
