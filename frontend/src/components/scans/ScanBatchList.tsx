import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Layers, Loader2 } from 'lucide-react';
import { getScans } from '../../services/api';
import type { IngestionJob, Scan, ScanBatchSummary } from '../../services/api';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { TableCell, TableRow } from '../ui/table';
import { toolFamily } from './ScanContribution';

// A batch's files load when it is expanded. A sweep of a few hundred files
// fits one request; past that the operator narrows with the page's search.
const FILES_PER_BATCH = 500;

interface ScanBatchRowProps {
  batch: ScanBatchSummary;
  /** The page's filters — an expanded batch lists only its matching files. */
  filters: { search?: string; tool?: string; createdAfter?: string; uploadedBy?: number };
  onViewScan: (scanId: number) => void;
  /** Columns of the table this row sits in. */
  colSpan: number;
  /** This batch's files still waiting for their format review (v5.271.0):
   *  listed when the batch is expanded, which used to say only "No files in
   *  this batch match the current filters" for a batch of staged files. */
  stagedJobs?: IngestionJob[];
  /** Brings staged files back into the upload review. */
  onReviewStaged?: (jobs: IngestionJob[]) => void;
}

const formatWhen = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : '—');

const count = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** A file row's port figure.  Every figure names its unit: the old
 *  "1 hosts · +0 · 0 open" read the +0 as ports, and "0 open" on a web or
 *  vulnerability tool as "found nothing" when the tool reports no ports. */
const portsLabel = (s: Scan): string | null => {
  if (s.open_ports > 0) return count(s.open_ports, 'open port');
  return toolFamily(s.tool_name) === 'port' ? '0 open ports' : null;
};

/**
 * One upload batch as a group row of the import history (v5.239.0).
 *
 * An agent splitting a large scope into hundreds of chunks, or an operator
 * dropping many files at once, is ONE row, expandable to its files (v5.207.0).
 * It used to live in a card of its own above the individual uploads, each
 * paginated separately, so the order things were imported in could not be read
 * off the page.  It now sits in the same chronological table as single files:
 * a full-width row whose figures carry their own labels, because a batch has
 * no single "ran" time or contribution line to put under those headers.
 */
export const ScanBatchRow: React.FC<ScanBatchRowProps> = ({
  batch: b, filters, onViewScan, colSpan, stagedJobs = [], onReviewStaged,
}) => {
  const [state, setState] = useState<Scan[] | 'loading' | 'error' | null>(null);

  const toggle = async () => {
    if (state) {
      setState(null);
      return;
    }
    setState('loading');
    try {
      const files = await getScans(0, FILES_PER_BATCH, {
        ...filters,
        batchId: b.id,
        sortBy: 'filename',
        sortOrder: 'asc',
      });
      // Collapsed while loading → stay collapsed.
      setState((prev) => (prev ? files : prev));
    } catch (err) {
      console.error('Error loading batch files:', err);
      setState((prev) => (prev ? 'error' : prev));
    }
  };

  const source =
    b.recon_session_id != null
      ? `Recon session #${b.recon_session_id}`
      : b.created_by
        ? `Uploaded by ${b.created_by}`
        : 'Upload';
  const total = b.total_files ?? b.files;
  const processing = b.processing_files ?? b.pending_files;
  // Staged = uploaded, waiting for the format review; neither processing nor
  // failed, so a fresh batch used to read "nothing imported" with no reason.
  const staged = b.staged_files ?? 0;
  const discarded = b.discarded_files ?? 0;

  return (
    <>
      <TableRow className="bg-muted/20 align-top" data-batch-id={b.id}>
        <TableCell colSpan={colSpan}>
          <div className="flex flex-wrap items-start gap-x-lg gap-y-xs">
            <div className="flex min-w-0 flex-[2_1_16rem] items-start gap-xs">
              <Layers className="mt-[3px] size-4 shrink-0 text-muted-foreground" aria-hidden />
              <div className="min-w-0">
                <p className="truncate font-semibold" title={b.label}>
                  {b.label}
                </p>
                <p className="truncate text-caption text-muted-foreground">Upload batch · {source}</p>
                {b.tools.length > 0 && (
                  <div className="mt-xxs flex flex-wrap gap-xxs">
                    {b.tools.map((t) => (
                      <Badge key={t} variant="outline" className="max-w-full truncate">
                        {t}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div
              className="min-w-0 flex-[1_1_8rem]"
              title="Imported files matching the page filters, of every file the batch imported; then what is still landing or failed"
            >
              <p className="tabular-nums">
                <span>{b.files.toLocaleString()}</span>
                <span className="text-caption text-muted-foreground"> file{b.files === 1 ? '' : 's'}</span>
                {total > b.files && (
                  <span className="text-caption text-muted-foreground"> matching of {total.toLocaleString()}</span>
                )}
              </p>
              {processing > 0 && <p className="text-caption text-muted-foreground">{processing} processing</p>}
              {staged > 0 && (
                <p className="text-caption text-warning">
                  {staged} waiting for review
                  {onReviewStaged && stagedJobs.length > 0 && (
                    <>
                      {' · '}
                      <button
                        type="button"
                        onClick={() => onReviewStaged(stagedJobs)}
                        className="rounded text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label={`Review the ${stagedJobs.length} waiting file${stagedJobs.length === 1 ? '' : 's'} of ${b.label}`}
                      >
                        Review
                      </button>
                    </>
                  )}
                </p>
              )}
              {b.failed_files > 0 && <p className="text-caption text-destructive">{b.failed_files} failed</p>}
              {discarded > 0 && <p className="text-caption text-muted-foreground">{discarded} discarded</p>}
              {total === 0 && processing === 0 && b.failed_files === 0 && staged === 0 && discarded === 0 && (
                <p className="text-caption text-muted-foreground">nothing imported</p>
              )}
            </div>

            <div
              className="min-w-0 flex-[1_1_8rem]"
              title="Unique hosts the matching files observed, and how many they first discovered"
            >
              <p className="tabular-nums">
                <span>{b.hosts.toLocaleString()}</span>
                <span className="text-caption text-muted-foreground"> unique host{b.hosts === 1 ? '' : 's'}</span>
              </p>
              <p className="text-caption text-muted-foreground">+{b.new_hosts.toLocaleString()} new</p>
            </div>

            <div
              className="min-w-0 flex-[1_1_8rem]"
              title="Open-port observations across the matching files. Observations, not distinct ports: a port seen by two files counts twice."
            >
              <p className="tabular-nums">
                <span>{b.open_ports.toLocaleString()}</span>
                <span className="text-caption text-muted-foreground"> port observations</span>
              </p>
            </div>

            <div className="min-w-0 flex-[1_1_10rem] text-caption">
              <p>{formatWhen(b.last_uploaded)}</p>
              {b.first_uploaded && b.first_uploaded !== b.last_uploaded && (
                <p className="text-muted-foreground">from {formatWhen(b.first_uploaded)}</p>
              )}
            </div>

            <Button
              variant="ghost"
              size="sm"
              className="shrink-0"
              onClick={() => void toggle()}
              aria-expanded={!!state}
              aria-label={`${state ? 'Hide' : 'Show'} the files of ${b.label}`}
            >
              {state === 'loading' ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : state ? (
                <ChevronDown className="size-4" aria-hidden />
              ) : (
                <ChevronRight className="size-4" aria-hidden />
              )}
              Files
            </Button>
          </div>
        </TableCell>
      </TableRow>
      {state && state !== 'loading' && (
        <TableRow>
          <TableCell colSpan={colSpan} className="bg-muted/30 p-sm">
            {state === 'error' ? (
              <p className="text-metadata text-destructive">
                Couldn&apos;t load this batch&apos;s files. Collapse it and try again.
              </p>
            ) : state.length === 0 && stagedJobs.length === 0 ? (
              <p className="text-metadata text-muted-foreground">
                No files in this batch match the current filters.
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {stagedJobs.map((job) => (
                  <li key={`staged-${job.id}`} className="flex min-w-0 items-center gap-sm py-xxs text-metadata">
                    <span className="min-w-0 flex-1 truncate font-mono" title={job.original_filename}>
                      {job.original_filename}
                    </span>
                    <span className="w-60 shrink-0 truncate text-caption text-warning">Waiting for review — not imported</span>
                    {onReviewStaged && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 shrink-0"
                        onClick={() => onReviewStaged([job])}
                        aria-label={`Review format and import ${job.original_filename}`}
                      >
                        Review
                      </Button>
                    )}
                  </li>
                ))}
                {state.map((s) => (
                  <li key={s.id} className="flex min-w-0 items-center gap-sm py-xxs text-metadata">
                    <button
                      type="button"
                      onClick={() => onViewScan(s.id)}
                      className="min-w-0 flex-1 truncate text-left font-mono text-primary hover:underline focus:outline-none focus-visible:underline"
                      title={s.filename}
                    >
                      {s.filename}
                    </button>
                    <span className="w-24 shrink-0 truncate text-muted-foreground">
                      {s.tool_name || s.scan_type || '—'}
                    </span>
                    <span
                      className="w-36 shrink-0 truncate text-right tabular-nums"
                      title="Hosts this file observed, and how many of them were new to the project"
                    >
                      {count(s.total_hosts, 'host')} · {s.new_hosts.toLocaleString()} new
                    </span>
                    {portsLabel(s) ? (
                      <span className="w-28 shrink-0 truncate text-right tabular-nums">{portsLabel(s)}</span>
                    ) : (
                      <span
                        className="w-28 shrink-0 truncate text-right text-muted-foreground"
                        title="This tool does not report open ports"
                      >
                        —
                      </span>
                    )}
                  </li>
                ))}
                {state.length === FILES_PER_BATCH && (
                  <li className="py-xxs text-caption text-muted-foreground">
                    Showing the first {FILES_PER_BATCH} files by name — use the search above to find
                    a specific one.
                  </li>
                )}
              </ul>
            )}
          </TableCell>
        </TableRow>
      )}
    </>
  );
};

export default ScanBatchRow;
