/**
 * The import history's status filter (5.418.0; owner decision 2026-10-22,
 * G15): the counts ARE the filter, as they were on Ingestion results.  Each
 * count is the server's (`GET /scans/history` → `status_counts`, under the
 * history's other filters) and opens exactly the list it counts — the same
 * request with `status=`.  "Needs attention" is `blocked_import_condition`,
 * the one definition of imports that need someone (Operations, Evidence).
 *
 * Analysts and above only: below analyst the server sends no counts and no
 * upload rows, so nothing here is rendered — a count a reader cannot open is
 * not shown to them.
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';

import {
  dismissIngestionJob,
  dismissSupersededJobs,
  type ImportHistoryPage,
  type ImportHistoryStatus,
  type IngestionResultItem,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useConfirm } from '../../hooks/useConfirm';
import { useProjectId } from '../../hooks/useProjectId';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import { Button } from '../ui/button';
import { isDismissable, useJobReadsChanged } from './UploadHistoryRow';

/** Every status filter the history understands — the server's list. */
export const IMPORT_HISTORY_STATUSES: readonly ImportHistoryStatus[] = [
  'needs_attention', 'superseded', 'expired', 'discarded', 'failed', 'failed_dismissed',
  'staged', 'queued', 'processing', 'completed',
];

const LABEL: Record<ImportHistoryStatus, string> = {
  needs_attention: 'Needs attention',
  failed: 'Failed',
  failed_dismissed: 'of them dismissed',
  expired: 'Expired',
  discarded: 'Discarded',
  superseded: 'Superseded',
  staged: 'Awaiting format review',
  queued: 'Queued',
  processing: 'Processing',
  completed: 'Completed uploads',
};

const HINT: Record<ImportHistoryStatus, string> = {
  needs_attention:
    'Failed, or finished partial, not dismissed, and not imported since by a later upload of the same file — what Operations lists as blocked',
  failed: 'The import went wrong: nothing from these files is in the inventory (dismissed ones included)',
  failed_dismissed: 'Failed imports someone has dismissed: counted in Failed, not in Needs attention',
  expired: 'Uploaded but never started: nobody reviewed the format within 24 hours, so the files were removed',
  discarded: 'Removed at the format review, before import',
  superseded: 'Failed or partial, not dismissed, but the same file was imported by a later upload — nothing needs doing',
  staged: 'Uploaded, not started',
  queued: 'Waiting for a worker',
  processing: 'Being imported now',
  completed: 'Every upload that was imported, one row each',
};

const TONE: Partial<Record<ImportHistoryStatus, string>> = {
  needs_attention: 'text-warning',
  failed: 'text-destructive',
};

/** Shown whatever their count ("zero needs attention" is an answer); the
 *  others only while they hold something, or are the active filter. */
const ALWAYS: ReadonlySet<ImportHistoryStatus> = new Set(['needs_attention', 'failed']);

/** The chips in their order.  G7: the dismissed part of Failed is stated
 *  right after it and opens exactly those rows. */
export const statusChips = (
  counts: NonNullable<ImportHistoryPage['status_counts']>,
  active: ImportHistoryStatus | null,
): Array<{ value: ImportHistoryStatus; label: string; count: number; hint: string; tone?: string }> => {
  const order: ImportHistoryStatus[] = [
    'needs_attention', 'failed', 'failed_dismissed', 'expired', 'discarded', 'superseded',
    'staged', 'queued', 'processing', 'completed',
  ];
  return order
    .filter((value) => ALWAYS.has(value) || (counts[value] ?? 0) > 0 || active === value)
    .filter((value) => value !== 'completed' || active === 'completed')
    .map((value) => ({ value, label: LABEL[value], count: counts[value] ?? 0, hint: HINT[value], tone: TONE[value] }));
};

interface UploadStatusFilterProps {
  counts: NonNullable<ImportHistoryPage['status_counts']>;
  active: ImportHistoryStatus | null;
  onChange: (status: ImportHistoryStatus | null) => void;
  /** The upload rows on screen — what the bulk dismissals act on. */
  shownUploads: IngestionResultItem[];
  canWrite: boolean;
  busy: boolean;
}

const UploadStatusFilter: React.FC<UploadStatusFilterProps> = ({
  counts, active, onChange, shownUploads, canWrite, busy,
}) => {
  const toast = useToast();
  const projectId = useProjectId();
  const jobReadsChanged = useJobReadsChanged();
  const [confirmEl, askConfirm] = useConfirm();

  // v5.242.0 — dismiss what is on screen in one action. Exactly the ids SHOWN
  // are sent — never "everything matching".
  const dismissable = shownUploads.filter(isDismissable);
  const dismissRows = useMutation({
    mutationFn: async (rows: IngestionResultItem[]) => {
      let failed = 0;
      for (const item of rows) {
        try {
          // Sequential on purpose: small, and no burst of parallel writes.
          await dismissIngestionJob(projectId, item.id);
        } catch {
          failed += 1;
        }
      }
      return failed;
    },
    onSuccess: (failed, rows) => {
      if (failed > 0) {
        toast.error(`${failed} of ${rows.length} could not be dismissed — you can only dismiss your own uploads unless you are an admin.`);
      } else {
        toast.success(`Dismissed ${rows.length} import${rows.length === 1 ? '' : 's'}`);
      }
      jobReadsChanged();
    },
  });
  // v5.289.0 — failures whose file a later upload imported: clear the ones SHOWN.
  const supersededShown = shownUploads.filter((i) => !i.dismissed_at && i.superseded_by_job_id != null);
  const dismissSuperseded = useMutation({
    mutationFn: (ids: number[]) => dismissSupersededJobs(projectId, ids),
    onSuccess: (res, ids) => {
      const n = ids.length;
      if (res.dismissed === n) toast.success(`Dismissed ${n} superseded import${n === 1 ? '' : 's'}`);
      else toast.info(`Dismissed ${res.dismissed} of ${n} — the rest were no longer superseded, or not yours to dismiss`);
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not dismiss the superseded imports.')),
    onSettled: () => jobReadsChanged(),
  });
  const working = dismissRows.isPending || dismissSuperseded.isPending;

  const askDismissShown = async () => {
    const n = dismissable.length;
    const partialCount = dismissable.filter((i) => i.status !== 'failed').length;
    const ok = await askConfirm({
      title: `Dismiss ${n} import${n === 1 ? '' : 's'}?`,
      body: (
        <>
          The {n} failed or partial import{n === 1 ? '' : 's'} shown stop being listed as needing
          attention. They stay in the history, still failed or partial
          {partialCount > 0 ? ` — ${partialCount} of them imported only part of their file` : ''}.
        </>
      ),
      confirmLabel: 'Dismiss',
    });
    if (ok) dismissRows.mutate(dismissable);
  };
  const askDismissSuperseded = async () => {
    const n = supersededShown.length;
    const ok = await askConfirm({
      title: `Dismiss ${n} superseded import${n === 1 ? '' : 's'}?`,
      body: (
        <>
          Each of these {n} failed or partial import{n === 1 ? '' : 's'} was imported again, from the same
          file, by a later upload. Dismissing takes {n === 1 ? 'it' : 'them'} off the failure lists; the
          rows stay in the history.
        </>
      ),
      confirmLabel: `Dismiss ${n}`,
    });
    if (ok) dismissSuperseded.mutate(supersededShown.map((i) => i.id));
  };

  const chip = (value: ImportHistoryStatus | null, label: string, count: number | null, hint: string, tone?: string) => {
    const pressed = active === value;
    return (
      <button
        key={value ?? 'all'}
        type="button"
        aria-pressed={pressed}
        title={hint}
        onClick={() => onChange(pressed && value !== null ? null : value)}
        className={cn(
          'inline-flex items-center gap-xs rounded-chip border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          pressed
            ? 'border-primary bg-primary/10 text-foreground'
            : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
        )}
      >
        <span>{label}</span>
        {count != null && (
          <strong className={cn('tabular-nums', tone && count > 0 ? tone : 'text-foreground')}>
            {count.toLocaleString()}
          </strong>
        )}
      </button>
    );
  };

  const supersededTotal = counts.superseded ?? 0;
  return (
    <div className="mb-sm flex flex-wrap items-center gap-xs" role="group" aria-label="Filter the import history by upload status">
      {confirmEl}
      {chip(null, 'Whole history', null, 'Every import, and every upload that was not imported')}
      {statusChips(counts, active).map((c) => chip(c.value, c.label, c.count, c.hint, c.tone))}
      {supersededTotal > supersededShown.length && active !== 'superseded' ? (
        <Button size="sm" variant="outline" className="ml-auto" disabled={busy} onClick={() => onChange('superseded')}>
          Review {supersededTotal.toLocaleString()} superseded
        </Button>
      ) : canWrite && supersededShown.length > 0 && (
        <Button size="sm" variant="outline" className="ml-auto" disabled={working || busy}
          onClick={() => void askDismissSuperseded()}>
          {working && <Loader2 className="size-3 animate-spin" aria-hidden />}
          Dismiss {supersededShown.length} superseded
        </Button>
      )}
      {canWrite && dismissable.length > 0 && (active === 'needs_attention' || active === 'failed') && (
        <Button size="sm" variant="outline"
          className={supersededShown.length > 0 || supersededTotal > 0 ? undefined : 'ml-auto'}
          disabled={working || busy}
          onClick={() => void askDismissShown()}>
          {working && <Loader2 className="size-3 animate-spin" aria-hidden />}
          Dismiss the {dismissable.length} shown
        </Button>
      )}
    </div>
  );
};

export default UploadStatusFilter;
