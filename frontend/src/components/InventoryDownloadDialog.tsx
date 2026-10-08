import React, { useEffect, useMemo, useState } from 'react';
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
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
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
const jobError = (job: ReportJob) => job.error_message || job.last_error || null;

const InventoryDownloadDialog: React.FC<InventoryDownloadDialogProps> = ({ open, onClose, filters, totalHosts }) => {
  // Which action is in flight ('csv', 'json', 'download-<id>') so only that
  // button spins and the rest disable.  For the CSV this covers the whole
  // streamed download; for the JSON only the call that queues it — the job
  // itself runs server-side and the dialog may be closed while it does.
  const [busy, setBusy] = useState<string | null>(null);
  // The JSON job started from this dialog.  Its row in the list below is the
  // source of truth for status; this id only decides which job gets the
  // "preparing / ready / failed" panel.
  const [trackedJobId, setTrackedJobId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Recent JSON jobs — closing the dialog or leaving the page does not strand
  // one that is still being prepared, or one that is ready.
  const [recentJobs, setRecentJobs] = useState<ReportJob[]>([]);
  // Retry / cancel / dismiss are a project analyst's, or the person's who
  // asked for that job (R32).  A job that does not say who asked for it keeps
  // its controls — the server decides.
  const { canWrite } = useProjectRole();
  const { user } = useAuth();
  const mayManageJob = (job: ReportJob) =>
    canWrite || job.requested_by_id == null || job.requested_by_id === user?.id;

  // A failed refresh keeps the list as it is (stale beats blank) but is NOT
  // swallowed: the poller needs the rejection to back off, and the reader
  // needs to know the status shown may be stale.
  const [listStale, setListStale] = useState<string | null>(null);
  const refreshRecentJobs = React.useCallback(async (): Promise<boolean> => {
    try {
      setRecentJobs(await listReportJobs(10));
      setListStale(null);
      return true;
    } catch (err) {
      setListStale(formatApiError(err, 'Could not refresh the status.'));
      return false;
    }
  }, []);
  // Poll callback: rejects on failure so useVisibilityPoll backs off; the
  // fire-and-forget callers keep the boolean form.
  const pollRecentJobs = React.useCallback(async () => {
    if (!(await refreshRecentJobs())) throw new Error('inventory job refresh failed');
  }, [refreshRecentJobs]);

  // Retry / cancel a job.  A 409 means its state changed under us (the worker
  // just claimed a queued job) — say so and refresh, so the row is true.
  const runJobAction = React.useCallback(
    async (action: (id: number) => Promise<ReportJob>, jobId: number) => {
      try {
        await action(jobId);
      } catch (e) {
        setError(formatApiError(e, 'That could not be done — the job may have changed state.'));
      } finally {
        refreshRecentJobs();
      }
    },
    [refreshRecentJobs],
  );

  useEffect(() => {
    if (open) {
      setError(null);
      refreshRecentJobs();
    }
  }, [open, refreshRecentJobs]);

  // While the dialog is open and a job is still running, poll so it advances
  // queued → preparing → ready without a manual refresh.  useVisibilityPoll
  // never overlaps requests and backs off on failure.
  useVisibilityPoll(pollRecentJobs, open && recentJobs.some(isRunning) ? 2500 : null);

  const activeFilters = useMemo(() => describeInventoryFilters(filters), [filters]);
  const isBusy = busy !== null;

  const downloadCsv = async () => {
    setBusy('csv');
    setError(null);
    try {
      await downloadInventoryCsv(filters);
      onClose();
    } catch (err) {
      setError(formatApiError(err, 'The CSV could not be downloaded.'));
    } finally {
      setBusy(null);
    }
  };

  const prepareJson = async () => {
    setBusy('json');
    setError(null);
    try {
      const job = await enqueueInventoryJson(filters);
      setTrackedJobId(job.id);
      setRecentJobs((prev) => [job, ...prev.filter((j) => j.id !== job.id)]);
      refreshRecentJobs();
    } catch (err) {
      setError(formatApiError(err, 'The JSON could not be queued.'));
    } finally {
      setBusy(null);
    }
  };

  // One way to fetch a finished file, for the panel and for a row: a refusal
  // (the file expired, the role changed) is said, never an unhandled rejection.
  const downloadJob = async (job: ReportJob, { closeAfter }: { closeAfter: boolean }) => {
    setBusy(`download-${job.id}`);
    setError(null);
    try {
      await downloadReportJob(job.id);
      if (closeAfter) {
        setTrackedJobId(null);
        onClose();
      }
    } catch (err) {
      setError(formatApiError(err, 'The file could not be downloaded.'));
      refreshRecentJobs();
    } finally {
      setBusy(null);
    }
  };

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
            <Button className="shrink-0" variant="outline" onClick={downloadCsv} disabled={isBusy}>
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
            <Button className="shrink-0" variant="outline" onClick={prepareJson} disabled={isBusy}>
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
                        onClick={() => runJobAction(retryReportJob, job.id)}
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
                        onClick={() => runJobAction(cancelReportJob, job.id)}
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
                        onClick={async () => {
                          try {
                            await dismissReportJob(job.id);
                            refreshRecentJobs();
                          } catch (e) {
                            // Said (R34): the ✕ used to do nothing on a refusal.
                            setError(formatApiError(e, 'That job could not be dismissed.'));
                          }
                        }}
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
