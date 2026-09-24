import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, Layers, Loader2 } from 'lucide-react';
import { getScans } from '../../services/api';
import type { IngestionJob, Scan, ScanBatchSummary } from '../../services/api';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { TableCell, TableRow } from '../ui/table';
import { formatInstant, type TimeFormatOptions } from '../../utils/scanTime';
import { toolFamily } from './ScanContribution';

// A batch's files load when it is expanded. A sweep of a few hundred files
// fits one request; past that the operator narrows with the page's search.
const FILES_PER_BATCH = 500;

/** Tool chips shown on a batch row before "+N more" (v5.285.0): a batch of
 *  every sample format listed 22 chips in a tall block. */
export const BATCH_TOOL_CHIPS = 4;

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
  /** Test seam: pin the zone/locale of the times. */
  timeFormat?: TimeFormatOptions;
}

const count = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** A file row's port figure.  Every figure names its unit: the old
 *  "1 hosts · +0 · 0 open" read the +0 as ports, and "0 open" on a web or
 *  vulnerability tool as "found nothing" when the tool reports no ports. */
const portsLabel = (s: Scan): string | null => {
  if (s.open_ports > 0) return count(s.open_ports, 'open port');
  return toolFamily(s.tool_name) === 'port' ? '0 open ports' : null;
};

const reasonLink = 'rounded underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

/**
 * One upload batch as a group row of the import history (v5.239.0).
 *
 * An agent splitting a large scope into hundreds of chunks, or an operator
 * dropping many files at once, is ONE row, expandable to its files (v5.207.0).
 * It sits in the same chronological table as single files and, since
 * v5.285.0, in the SAME columns: Scan (name, source, tools) · When (upload
 * times) · New hosts · What it contributed (files, hosts, port observations,
 * and why any file was not imported) · the Files toggle.  It used to be one
 * full-width cell whose blocks landed under the wrong headers.
 */
export const ScanBatchRow: React.FC<ScanBatchRowProps> = ({
  batch: b, filters, onViewScan, colSpan, stagedJobs = [], onReviewStaged, timeFormat,
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

  const uploader = b.created_by_name || b.created_by;
  const source =
    b.recon_session_id != null
      ? `Recon session #${b.recon_session_id}`
      : uploader
        ? `Uploaded by ${uploader}`
        : 'Upload';
  const total = b.total_files ?? b.files;
  const processing = b.processing_files ?? b.pending_files;
  // Staged = uploaded, waiting for the format review; neither processing nor
  // failed, so a fresh batch used to read "nothing imported" with no reason.
  const staged = b.staged_files ?? 0;
  const discarded = b.discarded_files ?? 0;
  const expired = b.expired_files ?? 0;
  const dismissedFailed = b.dismissed_failed_files ?? 0;
  const reprocessed = b.reprocessed_files ?? 0;
  const hasReason = processing + staged + b.failed_files + discarded + expired + dismissedFailed > 0;

  const when = (iso?: string | null) => (iso ? formatInstant(new Date(iso), timeFormat) : null);
  const last = when(b.last_uploaded);
  const first = when(b.first_uploaded);

  const shownTools = b.tools.slice(0, BATCH_TOOL_CHIPS);
  const hiddenTools = b.tools.slice(BATCH_TOOL_CHIPS);

  return (
    <>
      <TableRow className="bg-muted/20 align-top" data-batch-id={b.id}>
        {/* Scan */}
        <TableCell className="min-w-0">
          <div className="flex min-w-0 items-start gap-xs">
            <Layers className="mt-[3px] size-4 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0">
              <p className="truncate font-semibold" title={b.label}>
                {b.label}
              </p>
              <p className="truncate text-caption text-muted-foreground" title={source}>
                Upload batch · {source}
              </p>
              {b.tools.length > 0 && (
                <div className="mt-xxs flex min-w-0 flex-wrap gap-xxs" title={b.tools.join(', ')}>
                  {shownTools.map((t) => (
                    <Badge key={t} variant="outline" className="max-w-full truncate">
                      {t}
                    </Badge>
                  ))}
                  {hiddenTools.length > 0 && (
                    <Badge
                      variant="outline"
                      className="text-muted-foreground"
                      aria-label={`${hiddenTools.length} more tools: ${hiddenTools.join(', ')}`}
                    >
                      +{hiddenTools.length} more
                    </Badge>
                  )}
                </div>
              )}
            </div>
          </div>
        </TableCell>

        {/* When — the upload time(s); a batch has no single run time. */}
        <TableCell className="min-w-0">
          <p className="break-words text-metadata tabular-nums">{last ?? '—'}</p>
          <p className="break-words text-caption text-muted-foreground">
            {first && first !== last ? `uploaded; first file ${first}` : 'uploaded'}
          </p>
        </TableCell>

        {/* New hosts — as on a file row: added, out of the unique hosts seen. */}
        <TableCell title="Hosts these files added to the inventory, out of the unique hosts they observed">
          {b.hosts === 0 ? (
            <span className="text-caption text-muted-foreground">No hosts</span>
          ) : (
            <>
              {b.new_hosts > 0 ? (
                <span className="tabular-nums font-semibold text-success">+{b.new_hosts.toLocaleString()}</span>
              ) : (
                <span className="tabular-nums text-muted-foreground">0</span>
              )}
              <p className="mt-xxs text-caption tabular-nums text-muted-foreground">
                of {b.hosts.toLocaleString()} unique seen
              </p>
            </>
          )}
        </TableCell>

        {/* What it contributed — files imported, then why any were not. */}
        <TableCell className="min-w-0 text-metadata">
          <p
            className="break-words tabular-nums"
            title="Imported files matching the page filters, of every file the batch imported. A re-processed file is imported again as a new file of the same batch."
          >
            {total === 0 ? (
              <span className="text-muted-foreground">Nothing imported</span>
            ) : (
              <>
                {count(b.files, 'file')} imported
                {total > b.files && (
                  <span className="text-caption text-muted-foreground"> (matching, of {total.toLocaleString()})</span>
                )}
                {reprocessed > 0 && (
                  <span className="text-caption text-muted-foreground">
                    {' '}· incl. {reprocessed.toLocaleString()} re-processed
                  </span>
                )}
              </>
            )}
          </p>
          {b.open_ports > 0 && (
            <p
              className="text-caption tabular-nums text-muted-foreground"
              title="Open-port observations across the matching files. Observations, not distinct ports: a port seen by two files counts twice."
            >
              {count(b.open_ports, 'port observation')}
            </p>
          )}
          {processing > 0 && <p className="text-caption text-muted-foreground">{processing.toLocaleString()} processing</p>}
          {staged > 0 && (
            <p className="text-caption text-warning">
              {staged.toLocaleString()} waiting for format review
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
          {b.failed_files > 0 && (
            <p className="text-caption">
              <Link to="/parse-errors?status=needs_attention" className={`${reasonLink} text-destructive`}>
                {b.failed_files.toLocaleString()} failed
              </Link>
            </p>
          )}
          {expired > 0 && (
            <p className="text-caption text-muted-foreground">
              <Link
                to="/parse-errors?status=failed"
                className={reasonLink}
                title="Uploaded but never started: nobody reviewed their format within 24 hours, so the files were removed"
              >
                {expired.toLocaleString()} expired before import
              </Link>
            </p>
          )}
          {dismissedFailed > 0 && (
            <p className="text-caption text-muted-foreground">
              <Link to="/parse-errors?status=failed" className={reasonLink}>
                {dismissedFailed.toLocaleString()} failed (dismissed)
              </Link>
            </p>
          )}
          {discarded > 0 && (
            <p className="text-caption text-muted-foreground">{discarded.toLocaleString()} discarded before import</p>
          )}
          {total === 0 && !hasReason && (
            <p className="text-caption text-muted-foreground">no file of this batch reached the import</p>
          )}
        </TableCell>

        {/* Actions */}
        <TableCell>
          <div className="flex justify-end">
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
                {total === 0
                  ? 'No file of this batch was imported — see the reasons on the row.'
                  : 'No files in this batch match the current filters.'}
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
