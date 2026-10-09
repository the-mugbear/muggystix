import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, Download, FileDown, Loader2 } from 'lucide-react';
import {
  downloadInventoryCsv,
  enqueueInventoryJson,
  downloadReportJob,
  listReportJobs,
  dismissReportJob,
  retryReportJob,
  cancelReportJob,
  type InventoryFilters,
  type ReportJob,
} from '../services/api';
import { Alert, AlertDescription } from './ui/alert';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { pollEvery, queryErrorText } from '../lib/query';
import { useProjectRole } from '../hooks/useProjectRole';
import { useAuth } from '../contexts/AuthContext';
import { formatApiError } from '../utils/apiErrors';
import { describeInventoryFilters } from '../utils/inventoryFilters';
import { formatTimestamp } from '../utils/relativeTime';

/**
 * Hosts → "Download inventory": the filtered host list as a file.
 *
 * Two downloads, both of every matching host:
 *  - CSV — one row per host; streamed by the API and saved at once;
 *  - JSON — each host's full record; written by the report worker, so it is a
 *    job: queued → preparing → ready, then downloaded.  The job is kept on the
 *    server, so the dialog can be closed and reopened while it runs.
 *
 * This replaced "Export hosts" (owner, 2026-10-07), which also offered an HTML
 * host report, a Markdown bundle and an agent dataset; those were retired.
 * The client report is the Reports page's.
 */
interface InventoryDownloadDialogProps {
  open: boolean;
  onClose: () => void;
  filters: InventoryFilters;
  totalHosts: number;
}

const STATUS_LABEL: Record<ReportJob['status'], string> = {
  queued: 'Queued',
  processing: 'Preparing',
  completed: 'Ready',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const STATUS_VARIANT: Record<ReportJob['status'], 'success' | 'destructive' | 'secondary' | 'info'> = {
  queued: 'info',
  processing: 'info',
  completed: 'success',
  failed: 'destructive',
  cancelled: 'secondary',
};

const isRunning = (job: ReportJob) => job.status === 'queued' || job.status === 'processing';
const holdsRunningJob = (jobs: ReportJob[] | undefined): boolean => !!jobs?.some(isRunning);
/** How many recent jobs the dialog lists. */
const RECENT_JOBS = 10;
const NO_JOBS: ReportJob[] = [];
/** How often a running job is looked at again (`pollEvery`: half as often
 *  while the server is failing; it stops when none is running). */
const JOB_POLL_MS = 2500;
const jobError = (job: ReportJob) => job.error_message || job.last_error || null;

const InventoryDownloadDialog: React.FC<InventoryDownloadDialogProps> = ({ open, onClose, filters, totalHosts }) => {
  const queryClient = useQueryClient();
  // The JSON job started from this dialog.  Its row in the list below is the
  // source of truth for status; this id only decides which job gets the
  // "preparing / ready / failed" panel.
  const [trackedJobId, setTrackedJobId] = useState<number | null>(null);
  // The one line for an action that was refused; the next action clears it.
  const [error, setError] = useState<string | null>(null);
  // Retry / cancel / dismiss are a project analyst's, or the person's who
  // asked for that job (R32).  A job that does not say who asked for it keeps
  // its controls — the server decides.
  const { canWrite } = useProjectRole();
  const { user } = useAuth();
  const mayManageJob = (job: ReportJob) =>
    canWrite || job.requested_by_id == null || job.requested_by_id === user?.id;

  // Recent JSON jobs, read as the dialog opens — closing it or leaving the
  // page does not strand one that is still being prepared, or one that is
  // ready.  While a job is still running the list is re-read (visible tab
  // only, slower while failing) so it advances queued → preparing → ready
  // without a manual refresh; with none running nothing polls.
  const jobsQuery = useQuery({
    queryKey: ['listReportJobs', RECENT_JOBS],
    queryFn: ({ signal }) => listReportJobs(RECENT_JOBS, signal),
    enabled: open,
    ...pollEvery((query) => (holdsRunningJob(query.state.data) ? JOB_POLL_MS : null)),
  });
  const recentJobs = jobsQuery.data ?? NO_JOBS;
  // A failed refresh keeps the list as it is (stale beats blank) and says
  // that the status shown may be stale.
  const listStale = queryErrorText(jobsQuery.error, 'Could not refresh the status.');
  const refreshRecentJobs = () => queryClient.invalidateQueries({ queryKey: ['listReportJobs'] });

  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  const activeFilters = useMemo(() => describeInventoryFilters(filters), [filters]);

  const csv = useMutation({
    mutationFn: () => downloadInventoryCsv(filters),
    onMutate: () => setError(null),
    onSuccess: () => onClose(),
    onError: (err) => setError(formatApiError(err, 'The CSV could not be downloaded.')),
  });

  const json = useMutation({
    mutationFn: () => enqueueInventoryJson(filters),
    onMutate: () => setError(null),
    onSuccess: (job) => {
      setTrackedJobId(job.id);
      // Listed at once; the re-read then says what the server holds.
      queryClient.setQueryData<ReportJob[]>(
        ['listReportJobs', RECENT_JOBS], (prev) => [job, ...(prev ?? []).filter((j) => j.id !== job.id)],
      );
      void refreshRecentJobs();
    },
    onError: (err) => setError(formatApiError(err, 'The JSON could not be queued.')),
  });

  // One way to fetch a finished file, for the panel and for a row: a refusal
  // (the file expired, the role changed) is said, never an unhandled rejection.
  const download = useMutation({
    mutationFn: ({ job }: { job: ReportJob; closeAfter: boolean }) => downloadReportJob(job.id),
    onMutate: () => setError(null),
    onSuccess: (_file, { closeAfter }) => {
      if (closeAfter) {
        setTrackedJobId(null);
        onClose();
      }
    },
    onError: (err) => {
      setError(formatApiError(err, 'The file could not be downloaded.'));
      void refreshRecentJobs();
    },
  });
  const downloadJob = (job: ReportJob, { closeAfter }: { closeAfter: boolean }) => download.mutate({ job, closeAfter });

  // Retry / cancel a job.  A 409 means its state changed under us (the worker
  // just claimed a queued job) — say so and refresh, so the row is true.
  const jobAction = useMutation({
    mutationFn: ({ action, jobId }: { action: 'retry' | 'cancel'; jobId: number }) =>
      (action === 'retry' ? retryReportJob(jobId) : cancelReportJob(jobId)),
    onError: (err) => setError(formatApiError(err, 'That could not be done — the job may have changed state.')),
    onSettled: () => { void refreshRecentJobs(); },
  });

  const dismiss = useMutation({
    mutationFn: (jobId: number) => dismissReportJob(jobId),
    onSuccess: () => { void refreshRecentJobs(); },
    // Said (R34): the ✕ used to do nothing on a refusal.
    onError: (err) => setError(formatApiError(err, 'That job could not be dismissed.')),
  });

  // Which action is in flight ('csv', 'json', 'download-<id>') so only that
  // button spins and the rest disable.  For the CSV this covers the whole
  // streamed download; for the JSON only the call that queues it — the job
  // itself runs server-side and the dialog may be closed while it does.
  const busy = csv.isPending ? 'csv'
    : json.isPending ? 'json'
      : download.isPending ? `download-${download.variables.job.id}`
        : null;
  const isBusy = busy !== null;

  // The job started from this dialog, as the list currently knows it.
  const trackedJob = trackedJobId != null ? recentJobs.find((j) => j.id === trackedJobId) ?? null : null;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && !isBusy && onClose()}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <FileDown className="size-5" aria-hidden />
            Download inventory
          </DialogTitle>
        </DialogHeader>

        {error && (
          <Alert variant="destructive">
            <AlertDescription className="break-words">{error}</AlertDescription>
          </Alert>
        )}

        <div className="space-y-xs">
          <p className="text-metadata text-muted-foreground">
            <strong className="text-foreground">{totalHosts.toLocaleString()}</strong>{' '}
            {totalHosts === 1 ? 'host matches' : 'hosts match'}
            {activeFilters.length > 0 ? ' these filters; both files hold exactly those:' : '; both files hold all of them.'}
          </p>
          {activeFilters.length > 0 && (
            <ul className="flex flex-wrap gap-xxs" aria-label="Active filters">
              {activeFilters.map((filter) => (
                <li key={filter} className="min-w-0 max-w-full">
                  <Badge variant="outline" className="block max-w-full truncate" title={filter}>
                    {filter}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="divide-y divide-border border-y border-border">
          <div className="flex items-start gap-sm py-sm">
            <div className="min-w-0 flex-1">
              <p className="text-metadata font-semibold text-foreground">CSV</p>
              <p className="text-caption text-muted-foreground">
                One row per host: address, name, site, OS, open ports, severity counts, findings, tags.
              </p>
            </div>
            <Button className="shrink-0" variant="outline" onClick={() => csv.mutate()} disabled={isBusy}>
              {busy === 'csv' ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Download className="size-4" aria-hidden />
              )}
              {busy === 'csv' ? 'Downloading…' : 'Download CSV'}
            </Button>
          </div>
          <div className="flex items-start gap-sm py-sm">
            <div className="min-w-0 flex-1">
              <p className="text-metadata font-semibold text-foreground">JSON</p>
              <p className="text-caption text-muted-foreground">
                Everything recorded per host — ports, scanner observations, findings, tests, notes —
                then the project&rsquo;s findings and site, subnet and pattern roll-ups. Prepared in the
                background; you can close this dialog meanwhile.
              </p>
            </div>
            <Button className="shrink-0" variant="outline" onClick={() => json.mutate()} disabled={isBusy}>
              {busy === 'json' && <Loader2 className="size-4 animate-spin" aria-hidden />}
              {busy === 'json' ? 'Queuing…' : 'Prepare JSON'}
            </Button>
          </div>
        </div>

        {/* The JSON started from here: preparing → ready → download. */}
        {trackedJob && isRunning(trackedJob) && (
          <Alert variant="info" data-testid="tracked-job-running">
            <AlertDescription className="flex flex-wrap items-center gap-xs">
              <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
              <span className="min-w-0 flex-1">
                The JSON is {trackedJob.status === 'queued' ? 'queued' : 'being prepared'}. It stays under{' '}
                <strong>Recent JSON downloads</strong>, and you are notified when it is ready.
              </span>
            </AlertDescription>
          </Alert>
        )}
        {trackedJob && trackedJob.status === 'completed' && (
          <Alert variant="success" data-testid="tracked-job-ready">
            <AlertDescription className="flex flex-wrap items-center gap-xs">
              <span className="min-w-0 flex-1">The JSON is ready.</span>
              <Button
                size="sm"
                className="shrink-0"
                onClick={() => downloadJob(trackedJob, { closeAfter: true })}
                disabled={isBusy}
              >
                {busy === `download-${trackedJob.id}` ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <Download className="size-4" aria-hidden />
                )}
                Download JSON
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {trackedJob && trackedJob.status === 'failed' && (
          <Alert variant="destructive" data-testid="tracked-job-failed">
            <AlertDescription className="break-words">
              The JSON could not be prepared: {jobError(trackedJob) || 'the report worker gave no reason'}.
            </AlertDescription>
          </Alert>
        )}

        {/* Jobs are kept on the server, so a JSON prepared earlier (or still
            being prepared) is here when the dialog is reopened. */}
        {recentJobs.length > 0 && (
          <div className="space-y-xxs">
            <p className="text-caption font-semibold text-muted-foreground">Recent JSON downloads</p>
            {listStale && (
              <p role="status" className="break-words text-caption text-warning">
                Status may be stale — {listStale}
              </p>
            )}
            <ul className="space-y-xxs">
              {recentJobs.map((job) => {
                const mayManage = mayManageJob(job);
                const failure = job.status === 'failed' ? jobError(job) : null;
                return (
                  <li
                    key={job.id}
                    data-testid={`inventory-job-${job.id}`}
                    className="flex items-center gap-xs rounded-control border border-border px-xs py-xxs text-caption"
                  >
                    {isRunning(job) ? (
                      <Loader2 className="size-3.5 shrink-0 animate-spin text-info" aria-hidden />
                    ) : job.status === 'completed' ? (
                      <Download className="size-3.5 shrink-0 text-success" aria-hidden />
                    ) : (
                      // Cancelled is neutral, not an error.
                      <AlertCircle
                        className={`size-3.5 shrink-0 ${job.status === 'failed' ? 'text-destructive' : 'text-muted-foreground'}`}
                        aria-hidden
                      />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">
                        <span className="font-medium uppercase">{job.format}</span>{' '}
                        <span className="text-muted-foreground">
                          · requested {formatTimestamp(job.created_at)}
                          {(job.retry_count ?? 0) > 0 ? ` · retried ${job.retry_count}×` : ''}
                        </span>
                      </span>
                      {failure && (
                        <span className="block truncate text-destructive" title={failure}>{failure}</span>
                      )}
                    </span>
                    <Badge variant={STATUS_VARIANT[job.status] ?? 'info'} className="shrink-0">
                      {STATUS_LABEL[job.status] ?? job.status}
                    </Badge>
                    {mayManage && job.status === 'failed' && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 shrink-0"
                        aria-label={`Retry report job ${job.id}`}
                        onClick={() => jobAction.mutate({ action: 'retry', jobId: job.id })}
                      >
                        Retry
                      </Button>
                    )}
                    {mayManage && job.status === 'queued' && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 shrink-0"
                        aria-label={`Cancel report job ${job.id}`}
                        onClick={() => jobAction.mutate({ action: 'cancel', jobId: job.id })}
                      >
                        Cancel
                      </Button>
                    )}
                    {job.status === 'completed' && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 shrink-0"
                        disabled={isBusy}
                        onClick={() => downloadJob(job, { closeAfter: false })}
                      >
                        Download
                      </Button>
                    )}
                    {mayManage && (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 shrink-0"
                        aria-label={`Dismiss report job ${job.id}`}
                        onClick={() => dismiss.mutate(job.id)}
                      >
                        ✕
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isBusy}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default InventoryDownloadDialog;
