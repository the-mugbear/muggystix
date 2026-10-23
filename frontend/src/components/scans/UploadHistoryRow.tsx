/**
 * An UPLOAD row of the import history (5.418.0; owner decision 2026-10-22,
 * G15: ONE import history).  An upload that was not imported — failed,
 * expired, discarded — has no scan, so it is a row of its own; under a status
 * filter every upload the filter names is one of these.  Analysts and above
 * only: the server gives nobody else upload entries, and the rows' detail is
 * the analyst's read (`GET /ingestion-results?ids=`).
 *
 * Everything here was the Ingestion results view's (`pages/IngestionResults`,
 * removed): the error and `failure_kind`, "Review format and retry"
 * (`FormatRetryDialog`), "Import what the file holds" for a file cut short,
 * the uninterpreted lines, Dismiss, the format chain, whether the file is
 * still retained, and the recorded parse error's detail.
 */
import React, { useCallback, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronUp, Copy, ExternalLink, Loader2 } from 'lucide-react';

import {
  discardIngestionJob,
  dismissIngestionJob,
  getParseError,
  getScans,
  type IngestionResultItem,
} from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { useProjectId } from '../../hooks/useProjectId';
import { useProjectRole } from '../../hooks/useProjectRole';
import { invalidateReads, useLastSettled } from '../../lib/query';
import { copyToClipboard } from '../../utils/clipboard';
import { formatApiError } from '../../utils/apiErrors';
import { importResultsPath } from '../../utils/importResultsPath';
import { INGESTION_JOB_READS } from '../../utils/ingestionReads';
import { describeUpload } from '../../utils/scanTime';
import { isCutShort } from '../../utils/truncatedImport';
import { formatDate, formatTimestamp } from '../../utils/relativeTime';
import { safeFallback } from '../../utils/uiStyles';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '../ui/accordion';
import { Alert, AlertDescription } from '../ui/alert';
import { Badge } from '../ui/badge';
import { BreakableName } from '../ui/breakable-name';
import { Button } from '../ui/button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { TableCell, TableRow } from '../ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import FormatRetryDialog from './FormatRetryDialog';
import ImportResult from './ImportResult';
import KeepTruncatedImport from './KeepTruncatedImport';
import { ROW_LINK_CLASS, ScanRowActions } from './ScanRowActions';
import UninterpretedLines from './UninterpretedLines';

export const formatFileSize = (bytes: number | null): string => {
  if (bytes == null || bytes === 0) return '-';
  const kb = bytes / 1024;
  const mb = kb / 1024;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  if (kb >= 1) return `${kb.toFixed(1)} KB`;
  return `${bytes} B`;
};

/** v5.288.0 — "0.0s" on most rows was noise: a sub-100 ms import says so. */
export const formatDuration = (seconds: number | null): string => {
  if (seconds == null) return '—';
  if (seconds < 0.1) return '<0.1s';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
};

/** v5.288.0 — "0/0 open" on every web / DNS / auth import was noise: a
 *  figure only when the file had any. */
export const hostsFigure = (stats: IngestionResultItem['stats']): string =>
  stats && stats.hosts_parsed > 0 ? `${stats.hosts_up}/${stats.hosts_parsed} up` : '—';
export const portsFigure = (stats: IngestionResultItem['stats']): string =>
  stats && stats.ports_found > 0 ? `${stats.open_ports}/${stats.ports_found} open` : '—';

/**
 * Why a failed row was not imported, readable without expanding it
 * (v5.288.0).  Discards and expiries carry fixed messages
 * (staged_import_service); anything else shows the parser's specific cause,
 * else the first line of what the importer said.
 */
export const failureReason = (item: IngestionResultItem): string | null => {
  if (item.status !== 'failed') return null;
  const raw = (item.error?.error_message || item.error?.user_message || '').trim();
  if (raw.startsWith('Discarded before import')) return 'Discarded before import';
  if (raw.startsWith('Staged upload expired')) return 'Not started within 24 hours of upload; the file was removed';
  if (item.failure_reason?.trim()) return item.failure_reason.trim();
  const line = (item.error?.user_message || raw).trim().split('\n')[0]?.trim();
  return line || 'Import failed — no message recorded';
};

/**
 * The status a row reads as.  A staged upload that expired, or that an
 * operator discarded, is written `failed` by the server, but nothing failed —
 * the same split the server's `expired` / `discarded` views make.
 */
export const displayStatus = (item: IngestionResultItem): string => {
  if (item.status !== 'failed') return item.status;
  const raw = (item.error?.error_message || item.error?.user_message || '').trim();
  if (raw.startsWith('Staged upload expired')) return 'expired';
  if (raw.startsWith('Discarded before import')) return 'discarded';
  return 'failed';
};

const STATUS_VARIANT: Record<string, 'success' | 'destructive' | 'info' | 'muted'> = {
  completed: 'success',
  failed: 'destructive',
  processing: 'info',
  staged: 'muted',
  queued: 'muted',
  expired: 'muted',
  discarded: 'muted',
};

const STATUS_WORD: Record<string, string> = { staged: 'awaiting format review' };

const StatusBadge: React.FC<{ status: string }> = ({ status }) => (
  <Badge variant={STATUS_VARIANT[status] || 'muted'} className="whitespace-nowrap">
    {STATUS_WORD[status] ?? status}
  </Badge>
);

/** Whether Operations lists the upload as blocked until someone dismisses it. */
export const isDismissable = (item: IngestionResultItem): boolean =>
  !item.dismissed_at && (item.status === 'failed' || (item.status === 'completed' && !!item.partial));

/** Says the reads that list or count uploads are out of date (one was
 *  dismissed, discarded or started again); the ones on screen are read again. */
export const useJobReadsChanged = () => {
  const queryClient = useQueryClient();
  return useCallback(() => {
    void invalidateReads(queryClient, ...INGESTION_JOB_READS);
  }, [queryClient]);
};

interface UploadHistoryRowProps {
  upload: IngestionResultItem;
  /** Opened on arrival (a link named this one upload). */
  defaultExpanded?: boolean;
}

/** One upload, in the history's five columns, and its detail when expanded. */
const UploadHistoryRow: React.FC<UploadHistoryRowProps> = ({ upload: item, defaultExpanded = false }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const reason = failureReason(item);
  const status = displayStatus(item);
  const uploaded = describeUpload(item, { withZone: false });
  const stats = item.stats;
  return (
    <>
      <TableRow className="align-top" data-upload-row={item.id}>
        <TableCell>
          <div className="mb-xxs flex items-center gap-xxs">
            <Button
              variant="ghost"
              size="icon"
              className="-ml-xxs size-7 shrink-0"
              aria-expanded={expanded}
              aria-label={expanded ? `Hide details of ${item.original_filename}` : `Show details of ${item.original_filename}`}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? <ChevronUp className="size-4" aria-hidden /> : <ChevronDown className="size-4" aria-hidden />}
            </Button>
            <BreakableName
              as="p"
              name={item.original_filename}
              title={item.original_filename}
              className="min-w-0 font-mono text-caption font-semibold"
            />
          </div>
          <div className="flex flex-wrap items-center gap-xxs">
            <StatusBadge status={status} />
            {item.partial && (
              <Badge variant="warning" className="whitespace-nowrap" title={item.parser_warnings ?? 'Part of this file was not imported'}>
                partial
              </Badge>
            )}
            {item.tool_name && <Badge variant="outline">{item.tool_name.toLowerCase()}</Badge>}
            {item.dismissed_at && (
              <span className="text-caption text-muted-foreground" title={`Dismissed ${formatTimestamp(item.dismissed_at)}`}>
                dismissed
              </span>
            )}
            <span className="shrink-0 font-mono text-caption text-muted-foreground" title="Upload (ingestion job) id">
              upload #{item.id}
            </span>
          </div>
          {item.superseded_by_job_id != null && (
            <p className="mt-xxs break-words text-caption text-success" data-testid="superseded-by">
              Superseded — imported by{' '}
              <Link
                to={importResultsPath({ job_id: item.superseded_by_job_id })}
                className="rounded underline underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                upload #{item.superseded_by_job_id}
              </Link>
            </p>
          )}
        </TableCell>
        <TableCell className="min-w-0">
          {/* The column's one meaning: when the file was uploaded. An upload
              that was not imported has no scan, so no scan time. */}
          <p className="break-words text-metadata tabular-nums" title={uploaded.explanation}>{uploaded.label}</p>
          <p className="break-words text-caption tabular-nums text-muted-foreground" title="File size, and how long the import took">
            {formatFileSize(item.file_size)}
            {item.duration_seconds != null && ` · took ${formatDuration(item.duration_seconds)}`}
          </p>
        </TableCell>
        <TableCell>
          {/* Nothing was added by an upload that was not imported. */}
          <span className="text-caption text-muted-foreground">—</span>
        </TableCell>
        <TableCell className="min-w-0">
          {reason ? (
            <p
              className="line-clamp-2 break-words text-caption text-muted-foreground"
              title={item.error?.error_message || reason}
              data-testid="failure-reason"
            >
              {reason}
            </p>
          ) : item.partial ? (
            <p className="line-clamp-2 break-words text-caption text-warning">
              Part of this file is not in the inventory{(item.skipped_count ?? 0) > 0 ? ` (${item.skipped_count} skipped)` : ''}
            </p>
          ) : item.status === 'completed' && stats && (stats.hosts_parsed > 0 || stats.ports_found > 0) ? (
            <p className="break-words text-caption tabular-nums text-muted-foreground">
              {[hostsFigure(stats), portsFigure(stats)].filter((f) => f !== '—').join(' · ')}
            </p>
          ) : (
            <span className="text-caption text-muted-foreground">
              {item.status === 'completed' ? 'Imported — see its scan' : 'Not imported yet'}
            </span>
          )}
        </TableCell>
        <TableCell>
          <ScanRowActions
            link={item.scan_id != null ? (
              <Link to={`/scans/${item.scan_id}`} className={ROW_LINK_CLASS} title="Open the scan this upload made">
                Scan
              </Link>
            ) : null}
          />
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow>
          <TableCell colSpan={5} className="bg-accent/30 p-md">
            <UploadDetail item={item} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
};

/**
 * Dismiss a failed or partial import (v5.242.0). Operations lists both as
 * blocked until someone does; dismissing acknowledges the row — it stays in
 * the history, still failed / still partial.
 */
const DismissAction: React.FC<{ item: IngestionResultItem }> = ({ item }) => {
  const toast = useToast();
  const { canWrite } = useProjectRole();
  const projectId = useProjectId();
  const jobReadsChanged = useJobReadsChanged();
  const dismiss = useMutation({
    mutationFn: () => dismissIngestionJob(projectId, item.id),
    onSuccess: jobReadsChanged,
    onError: (err) => toast.error(formatApiError(err, 'Could not dismiss this import.')),
  });
  const blocked = item.status === 'failed' || (item.status === 'completed' && !!item.partial);
  if (!blocked) return null;
  if (item.dismissed_at) {
    return (
      <span className="text-caption text-muted-foreground">
        Dismissed {formatTimestamp(item.dismissed_at)} — no longer listed as blocked
      </span>
    );
  }
  if (!canWrite) return null;
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={dismiss.isPending}
      title="Acknowledge this import. It stays in the history; it stops being listed as needing attention."
      onClick={() => dismiss.mutate()}
    >
      {dismiss.isPending && <Loader2 className="size-3 animate-spin" aria-hidden />}
      Dismiss
    </Button>
  );
};

/** "detected → you chose → parsed by", so "how was this file read" is on record. */
const FormatChain: React.FC<{ item: IngestionResultItem }> = ({ item }) => {
  if (!(item.final_format_label || item.detected_format_label || item.format_override_label)) return null;
  return (
    <p className="break-words text-caption text-muted-foreground" data-testid="format-chain">
      {item.detected_format_label && (
        <>Detected as <span className="text-foreground">{item.detected_format_label}</span></>
      )}
      {item.format_override_label && (
        item.format_override_label === item.detected_format_label
          ? <> · confirmed by you</>
          : <>{item.detected_format_label ? ' · ' : ''}you chose <span className="text-warning">{item.format_override_label}</span></>
      )}
      {item.final_format_label && (
        <>{item.detected_format_label || item.format_override_label ? ' · ' : ''}parsed by <span className="text-foreground">{item.final_format_label}</span></>
      )}
      {item.source_tool && <> · source tool <span className="text-foreground">{item.source_tool}</span></>}
      {item.final_file_type && (
        <>
          {' · '}
          <Link
            to={`/reference/tool-coverage?format=${encodeURIComponent(item.final_file_type)}`}
            className="text-info underline-offset-2 hover:underline"
          >
            what BlueStick reads from this format
          </Link>
        </>
      )}
    </p>
  );
};

const retainedNote = (item: IngestionResultItem): string => (item.file_retained
  ? `File retained${item.retained_until ? ` until ${formatDate(item.retained_until)}` : ''}`
  : 'File no longer retained — re-upload to import again');

/** What an upload's row says when opened. */
const UploadDetail: React.FC<{ item: IngestionResultItem }> = ({ item }) => {
  const toast = useToast();
  const { canWrite } = useProjectRole();
  const projectId = useProjectId();
  const jobReadsChanged = useJobReadsChanged();
  const discard = useMutation({
    mutationFn: () => discardIngestionJob(projectId, item.id),
    onSuccess: jobReadsChanged,
    onError: (err) => toast.error(formatApiError(err, 'Could not discard the staged upload.')),
  });
  const [retryOpen, setRetryOpen] = useState(false);
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const [errorOpen, setErrorOpen] = useState(false);

  if (item.status === 'failed' || item.error) {
    const cutShort = isCutShort(item);
    return (
      <div className="flex flex-col gap-sm">
        <Alert variant="destructive">
          <AlertDescription>
            <p className="font-semibold">
              {cutShort
                ? 'The file was cut short — nothing from it was imported'
                : item.error?.error_type ? `Error type: ${item.error.error_type}` : 'Upload failed'}
            </p>
            <p className="mt-xxs break-words font-mono text-caption">
              {item.error?.error_message || 'No error message available'}
            </p>
            {cutShort ? (
              <p className="mt-xs">
                A retry reads the same file and fails the same way. If you have the complete file, upload that instead.
              </p>
            ) : item.error?.user_message
              && item.error.user_message.trim() !== (item.error.error_message || '').trim() && (
              <p className="mt-xs">{item.error.user_message}</p>
            )}
          </AlertDescription>
        </Alert>
        {cutShort && (
          <KeepTruncatedImport jobId={item.id} filename={item.original_filename} fileRetained={!!item.file_retained} />
        )}
        <FormatChain item={item} />
        <div className="flex flex-wrap items-center gap-xs">
          {item.parse_error_id != null && (
            <Button size="sm" variant="outline" onClick={() => setErrorOpen(true)}>
              View details
            </Button>
          )}
          {canWrite && item.file_retained && (
            <Button size="sm" onClick={() => setRetryOpen(true)}>
              Review format and retry
            </Button>
          )}
          <DismissAction item={item} />
          <span className="text-caption text-muted-foreground">{retainedNote(item)}</span>
        </div>
        <FormatRetryDialog open={retryOpen} onOpenChange={setRetryOpen} jobId={item.id} filename={item.original_filename} mode="retry" />
        {item.parse_error_id != null && (
          <ParseErrorDialog
            open={errorOpen}
            onOpenChange={setErrorOpen}
            jobId={item.id}
            parseErrorId={item.parse_error_id}
          />
        )}
      </div>
    );
  }

  if (item.status === 'staged') {
    return (
      <div className="flex flex-col gap-sm">
        <p className="text-metadata text-muted-foreground">
          Stored but not imported. Nothing runs until you start it; a staged file expires 24 hours after upload.
        </p>
        <FormatChain item={item} />
        {canWrite && (
          <div className="flex flex-wrap items-center gap-xs">
            <Button size="sm" onClick={() => setRetryOpen(true)} disabled={!item.file_retained}>
              Review format and import
            </Button>
            <Button size="sm" variant="outline" disabled={discard.isPending} onClick={() => discard.mutate()}>
              Discard
            </Button>
          </div>
        )}
        <FormatRetryDialog open={retryOpen} onOpenChange={setRetryOpen} jobId={item.id} filename={item.original_filename} mode="start" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-sm">
      {item.partial && (
        <Alert variant="warning">
          <AlertDescription className="flex flex-col gap-xs">
            <p className="font-semibold">
              Partial import — part of this file is not in the inventory
              {(item.skipped_count ?? 0) > 0 && ` (${item.skipped_count} skipped)`}
            </p>
            <p className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words font-mono text-caption">
              {safeFallback(item.parser_warnings, 'The parser recorded no detail.')}
            </p>
            <div><DismissAction item={item} /></div>
          </AlertDescription>
        </Alert>
      )}
      {item.scan_id != null && item.status === 'completed' && <CompletedImportResult scanId={item.scan_id} />}
      {(item.uninterpreted_total ?? 0) > 0 && (
        <UninterpretedLines jobId={item.id} total={item.uninterpreted_total ?? 0}
          distinct={item.uninterpreted_distinct ?? 0} formatKey={item.final_file_type} />
      )}
      <FormatChain item={item} />
      <div className="flex flex-wrap items-center gap-xs">
        {item.scan_id != null && (
          <Button size="sm" asChild>
            <Link to={`/scans/${item.scan_id}`}>
              <ExternalLink className="size-4" aria-hidden /> View scan
            </Link>
          </Button>
        )}
        {canWrite && item.status === 'completed' && item.file_retained && (
          <Button size="sm" variant="outline" onClick={() => setReprocessOpen(true)}
            title="Run the retained file through the pipeline again as a new import">
            Re-process…
          </Button>
        )}
        {item.status === 'completed' && (
          <span className="text-caption text-muted-foreground">{retainedNote(item)}</span>
        )}
      </div>
      <FormatRetryDialog
        open={reprocessOpen}
        onOpenChange={setReprocessOpen}
        jobId={item.id}
        filename={item.original_filename}
        mode="reprocess"
        priorScanId={item.scan_id}
      />
    </div>
  );
};

/** The import result of a completed upload, as the upload banner and the
 *  scan page show it.  Quiet on failure: the row still says what happened. */
const CompletedImportResult: React.FC<{ scanId: number }> = ({ scanId }) => {
  const projectId = useProjectId();
  const summary = useQuery({
    queryKey: ['getScans', projectId, { ids: [scanId] }, { limit: 1 }],
    queryFn: ({ signal }) => getScans(projectId, 0, 1, { ids: [scanId], signal }),
  });
  const row = summary.data?.[0] ?? null;
  if (!row) return null;
  return (
    <div className="rounded-panel border border-border p-sm">
      <p className="mb-xxs text-caption text-muted-foreground">Import result</p>
      <ImportResult scan={row} showFormatChain={false} />
    </div>
  );
};

/**
 * The recorded parse error of one upload.  A READ: keyed by the parse error,
 * asked while the dialog is open, and a failed lookup is said — the dialog
 * never opens on a stand-in made from the row (audit CRIT-8).
 */
const ParseErrorDialog: React.FC<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  jobId: number;
  parseErrorId: number;
}> = ({ open, onOpenChange, jobId, parseErrorId }) => {
  const projectId = useProjectId();
  const toast = useToast();
  const detail = useQuery({
    queryKey: ['getParseError', projectId, parseErrorId],
    queryFn: ({ signal }) => getParseError(projectId, parseErrorId, signal),
    enabled: open,
  });
  const shown = useLastSettled(open ? detail.data ?? null : null) ?? null;
  const failed = open && detail.isError;
  React.useEffect(() => {
    if (!failed) return;
    toast.error(
      formatApiError(detail.error, `Couldn't load full details for upload #${jobId}.`),
      { id: `pe-detail-${jobId}` },
    );
    onOpenChange(false);
  }, [failed, detail.error, jobId, onOpenChange, toast]);
  const copyError = async (text: string) => {
    if (await copyToClipboard(text)) toast.success('Copied error details', { id: 'copy-pe' });
    else toast.error('Could not copy to clipboard');
  };
  return (
    <Dialog open={open && !!detail.data} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Parse error details</DialogTitle>
        </DialogHeader>
        {shown && (
          <DialogBody className="flex flex-col gap-md">
            <div className="grid grid-cols-1 gap-md md:grid-cols-2">
              <div className="min-w-0">
                <p className="mb-xs text-subheading font-semibold">File</p>
                <p className="break-words"><strong>Filename:</strong> {shown.filename}</p>
                <p><strong>File type:</strong> {safeFallback(shown.file_type)}</p>
                <p><strong>File size:</strong> {formatFileSize(shown.file_size)}</p>
                <p className="break-words"><strong>Error type:</strong> {shown.error_type}</p>
              </div>
              <div>
                <p className="mb-xs text-subheading font-semibold">Status</p>
                <StatusBadge status={shown.status} />
              </div>
            </div>
            <div>
              <p className="mb-xs text-subheading font-semibold">What it means</p>
              <Alert variant="info">
                <AlertDescription className="break-words">
                  {shown.user_message || 'No user-friendly message available'}
                </AlertDescription>
              </Alert>
            </div>
            <div>
              <div className="mb-xs flex items-center justify-between">
                <p className="text-subheading font-semibold">Technical details</p>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => void copyError(`${shown.error_type}: ${shown.error_message}\n${shown.user_message || ''}`)}
                      aria-label="Copy error details"
                    >
                      <Copy className="size-4" aria-hidden />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Copy error</TooltipContent>
                </Tooltip>
              </div>
              <Alert variant="destructive">
                <AlertDescription className="break-words font-mono">{shown.error_message}</AlertDescription>
              </Alert>
            </div>
            <Accordion type="multiple">
              {shown.file_preview && (
                <AccordionItem value="preview">
                  <AccordionTrigger>File preview</AccordionTrigger>
                  <AccordionContent>
                    <pre className="max-h-72 overflow-auto rounded-control bg-muted p-sm font-mono text-caption">
                      {shown.file_preview}
                    </pre>
                  </AccordionContent>
                </AccordionItem>
              )}
              {shown.error_details && (
                <AccordionItem value="details">
                  <AccordionTrigger>Technical error details</AccordionTrigger>
                  <AccordionContent>
                    <pre className="max-h-96 overflow-auto rounded-control bg-muted p-sm font-mono text-caption">
                      {JSON.stringify(shown.error_details, null, 2)}
                    </pre>
                  </AccordionContent>
                </AccordionItem>
              )}
            </Accordion>
          </DialogBody>
        )}
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default UploadHistoryRow;
