import React, { useMemo, useState } from 'react';
import { useMutation, useMutationState, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronUp, Hourglass, Loader2, PauseCircle } from 'lucide-react';

import {
  cancelIngestionJob,
  discardIngestionJob,
  discardStagedJobs,
  getRecentIngestionJobs,
  getStagedIngestionJobs,
} from '../../services/api';
import type { IngestionJob } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useConfirm } from '../../hooks/useConfirm';
import { invalidateReads, pollEvery } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import { INGESTION_JOB_READS } from '../../utils/ingestionReads';
import { formatTimestamp } from '../../utils/relativeTime';
import { formatDuration } from '../../utils/scanTime';
import LastUpdated from '../LastUpdated';
import { Badge } from '../ui/badge';
import { BreakableName } from '../ui/breakable-name';
import { Button } from '../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

/** A queue row's status, in words beside its icon. */
const JOB_STATUS_LABEL: Record<string, string> = {
  staged: 'Needs review',
  queued: 'Queued',
  processing: 'Processing',
};

/** What the ingestion queue strip reads. */
export const QUEUE_READS = ['getRecentIngestionJobs', 'getStagedIngestionJobs'] as const;
/** The key of the writes to ONE job of the queue (cancel, discard): what is
 *  in flight is read back per job. */
const QUEUE_JOB_ACTION = ['scansQueueJobAction'] as const;

/** How often the queue is read again while a job is queued or processing. */
const ACTIVE_QUEUE_POLL_MS = 5000;
/** How often the queue's "Auto" switch reads it again when nothing is running. */
const AUTO_QUEUE_POLL_MS = 15_000;
const holdsActiveJob = (jobs: IngestionJob[] | undefined): boolean =>
  !!jobs?.some((j) => j.status === 'queued' || j.status === 'processing');
/** No worker progress for this long: a processing job may be wedged. */
const STALL_MS = 90_000;

export interface IngestionQueueState {
  recentJobs: IngestionJob[];
  stagedJobs: IngestionJob[];
  /** What the queue lists: what is still moving — queued, processing, staged. */
  pendingJobs: IngestionJob[];
  stagedByBatch: Map<number, IngestionJob[]>;
  queueCounts: { processing: number; staged: number };
  recentJobsFetched: Date | null;
  recentJobsLoading: boolean;
  /** The queue could not be read: said, never shown as "nothing failed". */
  recentJobsError: boolean;
  autoRefresh: boolean;
  setAutoRefresh: (on: boolean) => void;
}

/**
 * The ingestion queue: the 25 most recent jobs, and EVERY staged job — the
 * 26th file of a drop was missing from the queue, its Review and its
 * Discard.  Re-read every 5 s while a job is queued or processing; otherwise
 * every 15 s when the reader turned the queue's "Auto" switch on (off when the
 * page opens, and not remembered), and not at all without it.
 */
export function useIngestionQueue(projectId: number): IngestionQueueState {
  const [autoRefresh, setAutoRefresh] = useState(false);
  const queuePollMs = (jobs: IngestionJob[] | undefined): number | null => {
    if (holdsActiveJob(jobs)) return ACTIVE_QUEUE_POLL_MS;
    return autoRefresh ? AUTO_QUEUE_POLL_MS : null;
  };
  const recentQuery = useQuery({
    queryKey: ['getRecentIngestionJobs', projectId, 25],
    queryFn: ({ signal }) => getRecentIngestionJobs(projectId, 25, signal),
    ...pollEvery((query) => queuePollMs(query.state.data)),
  });
  const stagedQuery = useQuery({
    queryKey: ['getStagedIngestionJobs', projectId],
    queryFn: ({ signal }) => getStagedIngestionJobs(projectId, signal),
    ...pollEvery(queuePollMs(recentQuery.data)),
  });
  // The two are shown together, so the queue never appears first without the
  // staged files the recent jobs do not reach.
  const queueRead = !recentQuery.isPending && !stagedQuery.isPending;
  const recentJobs = useMemo(() => (queueRead ? recentQuery.data ?? [] : []), [queueRead, recentQuery.data]);
  // The staged list is a completion of the recent one; if it cannot be read,
  // the queue still shows the staged jobs among the recent.
  const stagedJobs = useMemo(() => {
    if (!queueRead) return [];
    return stagedQuery.isError || !stagedQuery.data
      ? recentJobs.filter((j) => j.status === 'staged')
      : stagedQuery.data;
  }, [queueRead, recentJobs, stagedQuery.isError, stagedQuery.data]);
  const recentJobsFetched = useMemo(
    () => (recentQuery.dataUpdatedAt ? new Date(recentQuery.dataUpdatedAt) : null),
    [recentQuery.dataUpdatedAt],
  );

  // Only what is still moving: queued, processing, and every staged job
  // (which the 25 recent may not reach).  A finished job is the import
  // history's — a completed one is its scan row, and (owner decision
  // 2026-10-22) a failed one is a row of the history too, with its Retry,
  // Dismiss and "Import what the file holds"; the strip only says how many
  // failed, with a link to them (`IngestionQueue`'s `failed` prop).
  const pendingJobs = useMemo(() => {
    const recent = recentJobs.filter((j) => j.status === 'queued' || j.status === 'processing');
    return [...recent, ...stagedJobs].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime() || b.id - a.id,
    );
  }, [recentJobs, stagedJobs]);
  const stagedByBatch = useMemo(() => {
    const byBatch = new Map<number, IngestionJob[]>();
    for (const job of stagedJobs) {
      if (job.batch_id == null) continue;
      byBatch.set(job.batch_id, [...(byBatch.get(job.batch_id) ?? []), job]);
    }
    return byBatch;
  }, [stagedJobs]);
  const queueCounts = useMemo(() => ({
    processing: pendingJobs.filter((j) => j.status === 'queued' || j.status === 'processing').length,
    staged: pendingJobs.filter((j) => j.status === 'staged').length,
  }), [pendingJobs]);

  return {
    recentJobs,
    stagedJobs,
    pendingJobs,
    stagedByBatch,
    queueCounts,
    recentJobsFetched,
    recentJobsLoading: recentQuery.isFetching || stagedQuery.isFetching,
    recentJobsError: recentQuery.isError,
    autoRefresh,
    setAutoRefresh,
  };
}

interface IngestionQueueProps {
  projectId: number;
  queue: IngestionQueueState;
  /** The import history's Failed count under its current filters
   *  (`status_counts.failed`), and the address of that exact list — the one
   *  the history's own Failed chip writes.  Null below analyst, or while not
   *  known: then the strip says nothing about failures. */
  failed: { count: number; href: string } | null;
  canWrite: boolean;
  onRefresh: () => void;
  /** Hands staged files back to the upload review. */
  onReview: (jobs: IngestionJob[]) => void;
}

/**
 * The queue strip: a left-rule callout that exists only while something is
 * still moving (in flight, or waiting for a format review) or uploads failed —
 * its counts always shown, the table one click away — and what an operator
 * does to one job of it.  Failed uploads are not rows here (5.418.0, owner
 * decision 2026-10-22): one line says how many, linking to the history's
 * Failed filter, whose count it is.
 */
export const IngestionQueue: React.FC<IngestionQueueProps> = ({
  projectId, queue, failed, canWrite, onRefresh, onReview,
}) => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [confirmDialog, confirm] = useConfirm();
  const { pendingJobs, stagedJobs, queueCounts } = queue;
  const failedCount = failed?.count ?? 0;
  // Whether the table is open is a per-browser convenience; the page works
  // the same if storage is unavailable.
  const [queueOpen, setQueueOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem('nm.scans.queueOpen') === '1';
    } catch {
      return false;
    }
  });
  const toggleQueue = () => setQueueOpen((open) => {
    try {
      localStorage.setItem('nm.scans.queueOpen', open ? '0' : '1');
    } catch {
      // Private window / blocked storage: the toggle still works for this visit.
    }
    return !open;
  });

  // What an operator does to one job of the queue.  Each says what happened
  // and puts the queue, and the counts and lists of imports that failed, out
  // of date (`INGESTION_JOB_READS`).  The re-read is
  // RETURNED from `onSuccess`, so a write stays pending until the queue on
  // screen is the new one: its control is disabled for that long, and a
  // second click cannot send the request again for a row that is about to
  // change or go.
  const jobsReRead = () => invalidateReads(queryClient, ...INGESTION_JOB_READS);
  const discardStaged = useMutation({
    mutationFn: (ids: number[]) => discardStagedJobs(projectId, ids),
    onSuccess: (res, ids) => {
      const n = ids.length;
      toast.info(
        res.discarded === n
          ? `Discarded ${res.discarded} staged upload${res.discarded === 1 ? '' : 's'}`
          : `Discarded ${res.discarded} of ${n}; the rest were no longer staged`,
      );
      return jobsReRead();
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not discard the staged uploads')),
  });
  const discardJob = useMutation({
    mutationKey: QUEUE_JOB_ACTION,
    mutationFn: (job: IngestionJob) => discardIngestionJob(projectId, job.id),
    onSuccess: () => {
      toast.info('Staged upload discarded');
      return jobsReRead();
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not discard the staged upload')),
  });
  const cancelJob = useMutation({
    mutationKey: QUEUE_JOB_ACTION,
    mutationFn: (job: IngestionJob) => cancelIngestionJob(projectId, job.id),
    onSuccess: () => {
      toast.info('Ingestion cancelled');
      return jobsReRead();
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not cancel ingestion')),
  });
  // The jobs with one of those writes in flight — every one of them, not only
  // the last started (a mutation's own `variables` name just that one), so a
  // second row can be acted on while the first is still being answered.  A
  // staged job is busy as well while "Discard N staged" is discarding it.
  const jobsWithActionInFlight = useMutationState({
    filters: { mutationKey: QUEUE_JOB_ACTION, status: 'pending' },
    select: (mutation) => (mutation.state.variables as IngestionJob | undefined)?.id,
  });
  const jobBusy = (job: IngestionJob): boolean =>
    jobsWithActionInFlight.includes(job.id)
    || (discardStaged.isPending && discardStaged.variables.includes(job.id));
  const onCancel = async (job: IngestionJob) => {
    const ok = await confirm({
      title: 'Cancel ingestion',
      body:
        'Stop parsing this file? Any rows already imported are kept; '
        + 'the job is marked failed. You can re-upload to retry.',
      resourceName: job.original_filename,
      severity: 'warning',
      confirmLabel: 'Cancel job',
    });
    if (!ok) return;
    cancelJob.mutate(job);
  };

  return (
    <>
      {confirmDialog}
      {queue.recentJobsError && pendingJobs.length === 0 && (
        <p role="status" className="mb-md text-caption text-muted-foreground">
          The ingestion queue could not be checked — this is not a confirmation that nothing is
          queued or waiting for review.
        </p>
      )}
      {(pendingJobs.length > 0 || failedCount > 0) && (
        <div
          data-testid="ingestion-queue"
          className={cn(
            'mb-md border-l-4 py-xs pl-md',
            failedCount > 0 ? 'border-l-destructive' : queueCounts.staged > 0 ? 'border-l-warning' : 'border-l-info',
          )}
        >
          <div>
            <div className="mb-xs flex flex-wrap items-center justify-between gap-xs">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-xs">
                  <h2 className="text-metadata font-semibold text-foreground">Ingestion queue</h2>
                  <span className="text-metadata text-muted-foreground">
                    {[
                      queueCounts.processing > 0 ? `${queueCounts.processing} processing` : null,
                      queueCounts.staged > 0 ? `${queueCounts.staged} waiting for review` : null,
                    ].filter(Boolean).join(' · ')}
                  </span>
                  {pendingJobs.length > 0 && (
                  <button
                    type="button"
                    aria-expanded={queueOpen}
                    aria-controls="ingestion-queue-table"
                    onClick={toggleQueue}
                    className="inline-flex items-center gap-xxs rounded text-caption text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {queueOpen ? <ChevronUp className="size-3.5" aria-hidden /> : <ChevronDown className="size-3.5" aria-hidden />}
                    {queueOpen ? 'Hide recent jobs' : `Show ${pendingJobs.length} recent job${pendingJobs.length === 1 ? '' : 's'}`}
                  </button>
                  )}
                </div>
                {/* Failed uploads are rows of the import history, with their
                    actions; the strip says how many — the history's own
                    Failed count under its filters — and opens that list. */}
                {failed && failedCount > 0 && (
                  <p className="text-metadata" data-testid="queue-failed-line">
                    <Link to={failed.href} className="text-destructive underline-offset-2 hover:underline">
                      {failedCount.toLocaleString()} upload{failedCount === 1 ? '' : 's'} failed — review {failedCount === 1 ? 'it' : 'them'} below
                    </Link>
                  </p>
                )}
                {queueOpen && pendingJobs.length > 0 && (
                  <p className="text-metadata text-muted-foreground">
                    In-flight uploads and staged files waiting for a format review. Every finished
                    upload — imported or not — is in the import history below.
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-xs">
                {/* Files left waiting (a closed review dialog, a failed
                    inspection) go back into the review, all at once. */}
                {canWrite && stagedJobs.length > 0 && (
                  <Button size="sm" onClick={() => onReview(stagedJobs)}>
                    Review {stagedJobs.length} waiting
                  </Button>
                )}
                {/* …or are cleared in one go. */}
                {canWrite && stagedJobs.length > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={discardStaged.isPending}
                    onClick={async () => {
                      // The ids are captured BEFORE the confirmation and are
                      // exactly what is sent: the endpoint used to discard
                      // every staged job the caller could see, whether or
                      // not this page had listed it (for an admin, other
                      // people's files too).
                      const ids = stagedJobs.map((j) => j.id);
                      const n = ids.length;
                      const ok = await confirm({
                        title: 'Discard staged uploads',
                        body:
                          `Remove the ${n} staged file${n === 1 ? '' : 's'} listed in this queue? Nothing was imported from them. `
                          + 'They stay listed in the import history as discarded.',
                        resourceName: `${n} staged upload${n === 1 ? '' : 's'}`,
                        severity: 'warning',
                        confirmLabel: `Discard ${n}`,
                      });
                      if (!ok) return;
                      discardStaged.mutate(ids);
                    }}
                  >
                    Discard {stagedJobs.length} staged
                  </Button>
                )}
                <LastUpdated
                  lastFetched={queue.recentJobsFetched}
                  onRefresh={onRefresh}
                  isLoading={queue.recentJobsLoading}
                  label="ingestion jobs"
                  intervalMs={AUTO_QUEUE_POLL_MS}
                  autoRefresh={queue.autoRefresh}
                  onAutoRefreshChange={queue.setAutoRefresh}
                />
              </div>
            </div>
            {queueOpen && pendingJobs.length > 0 && (
            <div id="ingestion-queue-table" className="overflow-x-auto">
              {/* A fixed layout with an Actions column wide enough for its
                  buttons; Message is the only column without a width, so it
                  gets what the others leave (Tool and Duration are second
                  lines of File and Submitted). */}
              <Table className="min-w-[64rem] table-fixed">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-36">Status</TableHead>
                    <TableHead className="w-1/5">File</TableHead>
                    <TableHead className="w-20 text-right">Size</TableHead>
                    <TableHead>Message</TableHead>
                    <TableHead className="w-44">Submitted</TableHead>
                    <TableHead className="w-44 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pendingJobs.map((job) => (
                    <IngestionQueueRow
                      key={job.id}
                      job={job}
                      canWrite={canWrite}
                      busy={jobBusy(job)}
                      onReview={() => onReview([job])}
                      onDiscard={() => discardJob.mutate(job)}
                      onCancel={() => onCancel(job)}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
            )}
          </div>
        </div>
      )}
    </>
  );
};

interface IngestionQueueRowProps {
  job: IngestionJob;
  canWrite: boolean;
  /** One of the job's actions is in flight. */
  busy: boolean;
  onReview: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}

/** One job of the queue — still moving: queued, processing, or staged. */
const IngestionQueueRow: React.FC<IngestionQueueRowProps> = ({
  job, canWrite, busy, onReview, onDiscard, onCancel,
}) => {
  const elapsed = (() => {
    if (job.started_at && job.status === 'processing') {
      return `${formatDuration(Date.now() - new Date(job.started_at).getTime())}…`;
    }
    return '-';
  })();

  // Under 1 KB in bytes: "0 KB" read as an empty file.
  const fileSize = job.file_size
    ? job.file_size > 1048576
      ? `${(job.file_size / 1048576).toFixed(1)} MB`
      : job.file_size < 1024
        ? `${job.file_size} B`
        : `${(job.file_size / 1024).toFixed(0)} KB`
    : '-';

  const displayMessage = job.status === 'staged' ? 'Not imported yet' : job.progress || job.message || '-';

  // Dead-letter / liveness signals. A job that bounced before settling
  // carries a retry_count; a 'processing' job whose worker heartbeat has gone
  // stale is wedged, not working.
  const retried = (job.retry_count ?? 0) > 0;
  const isStalled =
    job.status === 'processing' &&
    !!job.last_heartbeat &&
    Date.now() - new Date(job.last_heartbeat).getTime() > STALL_MS;

  // (5.418.0: a finished job — completed, partial or failed — is never a
  // queue row; the import history lists it. The expander, the "imported with
  // warnings" marks and the failure detail went with them.)
  return (
    <>
      <TableRow>
        <TableCell>
          {/* The icon plus the word: icon and colour alone left a staged row
              as a grey circle nobody could read. */}
          <span className="inline-flex min-w-0 items-center gap-xxs">
            {job.status === 'processing' && (
              <Loader2 className="size-4 animate-spin text-info" aria-hidden />
            )}
            {job.status === 'queued' && (
              <Hourglass className="size-4 text-muted-foreground" aria-hidden />
            )}
            {/* Stored, not imported: nothing runs until the operator starts it. */}
            {job.status === 'staged' && (
              <PauseCircle className="size-4 text-muted-foreground" aria-hidden />
            )}
            <span className="truncate text-caption">
              {JOB_STATUS_LABEL[job.status] ?? job.status}
            </span>
          </span>
          {isStalled && (
            <Badge
              variant="outline"
              className="ml-xxs border-warning/40 text-warning"
              title="No worker progress in over 90s — the job may be stalled."
            >
              stalled?
            </Badge>
          )}
        </TableCell>
        <TableCell className="min-w-0">
          <BreakableName as="p" name={job.original_filename} title={job.original_filename} />
          {job.tool_name && (
            <p className="truncate text-caption text-muted-foreground">{job.tool_name}</p>
          )}
        </TableCell>
        <TableCell className="text-right font-mono">{fileSize}</TableCell>
        <TableCell>
          <div>{displayMessage}</div>
          {retried && (
            <Badge
              variant="outline"
              className="mt-xxs border-warning/40 text-warning"
              title={job.last_error ? `Most recent failure: ${job.last_error}` : undefined}
            >
              retried {job.retry_count}×
            </Badge>
          )}
        </TableCell>
        <TableCell className="text-caption">
          <p>{formatTimestamp(job.created_at)}</p>
          {elapsed !== '-' && (
            <p className="font-mono text-muted-foreground" title="How long the parse ran">ran {elapsed}</p>
          )}
        </TableCell>
        <TableCell className="text-right">
          {/* A staged upload is imported after a format review, or
              discarded; a queued or processing one can be cancelled.  A
              failed one is not here: it is a row of the import history. */}
          {canWrite && job.status === 'staged' && (
            <div className="flex flex-wrap justify-end gap-xxs">
              <Button
                size="sm"
                variant="outline"
                onClick={onReview}
                aria-label={`Review format and import ${job.original_filename}`}
              >
                Review
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive"
                disabled={busy}
                onClick={onDiscard}
                aria-label={`Discard staged upload ${job.original_filename}`}
              >
                Discard
              </Button>
            </div>
          )}
          {canWrite && (job.status === 'queued' || job.status === 'processing') && (
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive"
              disabled={busy}
              onClick={onCancel}
              aria-label={`Cancel ingestion for ${job.original_filename}`}
            >
              Cancel
            </Button>
          )}
        </TableCell>
      </TableRow>
    </>
  );
};
