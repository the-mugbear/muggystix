import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, Layers, Loader2 } from 'lucide-react';
import { getScans } from '../../services/api';
import type { IngestionJob, Scan, ScanBatchSummary } from '../../services/api';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { TableCell, TableRow } from '../ui/table';
import { formatInstant, type TimeFormatOptions } from '../../utils/scanTime';
import { batchDisplayName } from '../../utils/batchLabel';
import { toolFamily } from './ScanContribution';

// A batch's files load when it is expanded. A sweep of a few hundred files
// fits one request; past that the operator narrows with the page's search.
const FILES_PER_BATCH = 500;

/** Tool chips shown on a batch row before "+N more" (v5.287.0): a batch of
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
 * Where every file of a batch went (v5.288.0).  Imported + each reason a file
 * was not imported adds up to the files that reached the server
 * (`uploaded_files`, one ingestion job each); a generated label's count is
 * what was DROPPED, so the difference is what the upload refused (the
 * duplicate guard's 409 creates no job).  A "46 files" batch read "17 files
 * imported · 4 failed" with 25 files unexplained — they were refused
 * duplicates.  A re-processed file adds a job without being dropped again.
 */
export function batchRefusedAtUpload(b: ScanBatchSummary): number {
  const dropped = batchDisplayName(b.label).dropped;
  if (dropped == null || b.uploaded_files == null) return 0;
  const reachedServer = b.uploaded_files - (b.reprocessed_files ?? 0);
  return Math.max(0, dropped - reachedServer);
}

/**
 * One upload batch as a group row of the import history (v5.239.0).
 *
 * An agent splitting a large scope into hundreds of chunks, or an operator
 * dropping many files at once, is ONE row, expandable to its files (v5.207.0).
 * It sits in the same chronological table as single files and, since
 * v5.287.0, in the SAME columns: Scan (name, source, tools) · When (upload
 * time) · New hosts · What it contributed (files, hosts, port observations,
 * and why any file was not imported) · the Files toggle.  Since v5.288.0 its
 * expanded files are rows of those same columns too.
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

  const name = batchDisplayName(b.label);
  const uploader = b.created_by_name || b.created_by;
  const source =
    b.recon_session_id != null
      ? `Recon session #${b.recon_session_id}`
      : uploader
        ? `Uploaded by ${uploader}`
        : 'Upload';
  // A generated title already says "Upload batch"; a name does not.
  const subtitle = name.generated ? source : `Upload batch · ${source}`;
  const total = b.total_files ?? b.files;
  const processing = b.processing_files ?? b.pending_files;
  // Staged = uploaded, waiting for the format review; neither processing nor
  // failed, so a fresh batch used to read "nothing imported" with no reason.
  const staged = b.staged_files ?? 0;
  const discarded = b.discarded_files ?? 0;
  const expired = b.expired_files ?? 0;
  const dismissedFailed = b.dismissed_failed_files ?? 0;
  const cancelled = b.cancelled_files ?? 0;
  const reprocessed = b.reprocessed_files ?? 0;
  const refused = batchRefusedAtUpload(b);
  const hasReason =
    processing + staged + b.failed_files + discarded + expired + dismissedFailed + cancelled + refused > 0;

  const when = (iso?: string | null) => (iso ? formatInstant(new Date(iso), timeFormat) : null);
  // The upload time is the batch's creation (the moment the files were
  // dropped); its files' scan rows are written as each import finishes.
  const uploaded = when(b.created_at) ?? when(b.first_uploaded);
  const last = when(b.last_uploaded);
  // v5.288.0 — "Sep 23 … uploaded; first file Sep 18 …" read as nonsense: the
  // later time was a re-processed file joining the batch.  Say that.
  const reprocessedAt = reprocessed > 0 && last && last !== uploaded ? last : null;

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
              <p className="break-words font-semibold" title={name.generated ? b.label : undefined}>
                {name.title}
              </p>
              {/* v5.288.0 — wraps: "Uploaded by Adminis…" hid whose upload it was. */}
              <p className="break-words text-caption text-muted-foreground">{subtitle}</p>
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

        {/* When — the upload time; a batch has no single run time. */}
        <TableCell className="min-w-0">
          <p className="break-words text-metadata tabular-nums">{uploaded ?? '—'}</p>
          <p className="break-words text-caption text-muted-foreground">uploaded</p>
          {reprocessedAt && (
            <p className="break-words text-caption text-muted-foreground">re-processed {reprocessedAt}</p>
          )}
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

        {/* What it contributed — files imported, then why each other file
            was not: the lines add up to every file of the batch. */}
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
                    aria-label={`Review the ${stagedJobs.length} waiting file${stagedJobs.length === 1 ? '' : 's'} of ${name.title}`}
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
          {cancelled > 0 && (
            <p className="text-caption text-muted-foreground">{cancelled.toLocaleString()} cancelled</p>
          )}
          {refused > 0 && (
            <p
              className="text-caption text-muted-foreground"
              title="Dropped with this batch but refused by the server on upload — almost always because the same file was already uploaded to this project — so they never reached the import."
            >
              {refused.toLocaleString()} refused at upload (already uploaded)
            </p>
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
              aria-label={`${state ? 'Hide' : 'Show'} the files of ${name.title}`}
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
      {state === 'error' && (
        <TableRow>
          <TableCell colSpan={colSpan} className="bg-muted/30 p-sm">
            <p className="text-metadata text-destructive">
              Couldn&apos;t load this batch&apos;s files. Collapse it and try again.
            </p>
          </TableCell>
        </TableRow>
      )}
      {Array.isArray(state) && state.length === 0 && stagedJobs.length === 0 && (
        <TableRow>
          <TableCell colSpan={colSpan} className="bg-muted/30 p-sm">
            <p className="text-metadata text-muted-foreground">
              {total === 0
                ? 'No file of this batch was imported — see the reasons on the row.'
                : 'No files in this batch match the current filters.'}
            </p>
          </TableCell>
        </TableRow>
      )}
      {/* v5.288.0 — the files are rows of the parent table's columns (Scan ·
          When · New hosts · Contributed), so they read under its headers;
          they were a header-less list whose figures landed anywhere. */}
      {Array.isArray(state) && (state.length > 0 || stagedJobs.length > 0) && (
        <>
          {stagedJobs.map((job) => (
            <TableRow key={`staged-${job.id}`} className="bg-muted/30 align-top" data-batch-file>
              <TableCell className="min-w-0 pl-xl">
                <span className="block break-all font-mono text-metadata">{job.original_filename}</span>
              </TableCell>
              <TableCell className="min-w-0 text-caption text-muted-foreground">
                {when(job.created_at) ?? '—'}
              </TableCell>
              <TableCell className="text-caption text-muted-foreground">—</TableCell>
              <TableCell className="min-w-0 text-caption text-warning">Waiting for review — not imported</TableCell>
              <TableCell>
                {onReviewStaged && (
                  <div className="flex justify-end">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 shrink-0"
                      onClick={() => onReviewStaged([job])}
                      aria-label={`Review format and import ${job.original_filename}`}
                    >
                      Review
                    </Button>
                  </div>
                )}
              </TableCell>
            </TableRow>
          ))}
          {state.map((s) => {
            const ports = portsLabel(s);
            return (
              <TableRow key={s.id} className="bg-muted/30 align-top" data-batch-file>
                <TableCell className="min-w-0 pl-xl">
                  <button
                    type="button"
                    onClick={() => onViewScan(s.id)}
                    className="block max-w-full break-all text-left font-mono text-metadata text-primary hover:underline focus:outline-none focus-visible:underline"
                  >
                    {s.filename}
                  </button>
                  <span className="block truncate text-caption text-muted-foreground">
                    {s.tool_name || s.scan_type || '—'}
                  </span>
                </TableCell>
                <TableCell className="min-w-0 text-caption tabular-nums text-muted-foreground">
                  {when(s.created_at) ?? '—'}
                </TableCell>
                <TableCell title="Hosts this file added to the inventory, out of all the hosts it observed">
                  {s.total_hosts === 0 ? (
                    <span className="text-caption text-muted-foreground">No hosts</span>
                  ) : (
                    <>
                      {s.new_hosts > 0 ? (
                        <span className="tabular-nums font-semibold text-success">+{s.new_hosts.toLocaleString()}</span>
                      ) : (
                        <span className="tabular-nums text-muted-foreground">0</span>
                      )}
                      <p className="mt-xxs text-caption tabular-nums text-muted-foreground">
                        of {count(s.total_hosts, 'host')} seen
                      </p>
                    </>
                  )}
                </TableCell>
                <TableCell className="min-w-0 text-caption tabular-nums">
                  {ports ?? (
                    <span className="text-muted-foreground" title="This tool does not report open ports">
                      —
                    </span>
                  )}
                </TableCell>
                <TableCell />
              </TableRow>
            );
          })}
          {state.length === FILES_PER_BATCH && (
            <TableRow>
              <TableCell colSpan={colSpan} className="bg-muted/30 py-xxs text-caption text-muted-foreground">
                Showing the first {FILES_PER_BATCH} files by name — use the search above to find a specific one.
              </TableCell>
            </TableRow>
          )}
        </>
      )}
    </>
  );
};

export default ScanBatchRow;
