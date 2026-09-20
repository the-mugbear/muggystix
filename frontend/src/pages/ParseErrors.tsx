import React, { useState, useEffect, useMemo, useCallback, Fragment } from 'react';
import { copyToClipboard } from '../utils/clipboard';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  RefreshCw,
  X as CloseIcon,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  CloudUpload,
  Server,
  Copy,
  Loader2,
  Search,
} from 'lucide-react';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { Input } from '../components/ui/input';
import {
  discardIngestionJob,
  dismissIngestionJob,
  getIngestionResults,
  getParseError,
  getScans,
  type IngestionResultItem,
  type IngestionResultsResponse,
  type IngestionResultsSortBy,
  type ParseError,
  type Scan,
} from '../services/api';
import ImportResult from '../components/scans/ImportResult';
import FormatRetryDialog from '../components/scans/FormatRetryDialog';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '../components/ui/select';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Alert, AlertDescription } from '../components/ui/alert';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../components/ui/tooltip';
import { safeFallback } from '../utils/uiStyles';
import { cn } from '../utils/cn';
import { formatRelativeTime } from '../utils/relativeTime';

const formatFileSize = (bytes: number | null): string => {
  if (bytes == null || bytes === 0) return '-';
  const kb = bytes / 1024;
  const mb = kb / 1024;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  if (kb >= 1) return `${kb.toFixed(1)} KB`;
  return `${bytes} B`;
};

const formatDuration = (seconds: number | null): string => {
  if (seconds == null) return '-';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
};

const timeAgo = (dateString: string | null): string =>
  formatRelativeTime(dateString, { fallback: '-', absoluteAfterDays: 30 });
const STATUS_VARIANT: Record<string, 'success' | 'destructive' | 'info' | 'muted'> = {
  completed: 'success',
  failed: 'destructive',
  processing: 'info',
  staged: 'muted',
  queued: 'muted',
};

const StatusBadge: React.FC<{ status: string }> = ({ status }) => (
  // whitespace-nowrap so multi-char statuses ("processing") don't
  // wrap mid-word inside their cell; widen the column too — see
  // TableHead below.
  <Badge variant={STATUS_VARIANT[status] || 'muted'} className="whitespace-nowrap">
    {status}
  </Badge>
);

const ParseErrors: React.FC = () => {
  const navigate = useNavigate();
  // v5.135.0 — Scans links here with ?error_id=N. Previously it navigated to
  // the bare list, so the operator arrived with no idea which of up to 100
  // rows was the one they clicked.
  const [searchParams, setSearchParams] = useSearchParams();
  const focusErrorId = Number(searchParams.get('error_id')) || null;
  // v5.222.0 — the upload banner and the import result link a JOB by id
  // (`?job_id=`), which is this list's own row id.
  const focusJobId = Number(searchParams.get('job_id')) || null;
  const toast = useToast();
  const [data, setData] = useState<IngestionResultsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedRow, setExpandedRow] = useState<number | null>(null);

  const [selectedParseError, setSelectedParseError] = useState<ParseError | null>(null);
  const [detailDialogOpen, setDetailDialogOpen] = useState(false);
  // v2.86.2 — search runs server-side now (300ms debounce); status
  // and sort knobs were added alongside.  Pre-v2.86.2 the page only
  // filtered the partial slice it had loaded, which silently missed
  // matches further down the list when projects had >100 ingest jobs.
  const [searchText, setSearchText] = useState('');
  const debouncedSearchText = useDebouncedValue(searchText, 300);
  // URL-backed so other surfaces can deep-link a filtered view (the queue
  // health card links here for failed / queued / in-flight jobs). Local state
  // would have made those links land on an unfiltered list.
  const statusFilter = searchParams.get('status') ?? 'all';
  const setStatusFilter = useCallback((value: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value === 'all') next.delete('status');
      else next.set('status', value);
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  const [sortBy, setSortBy] = useState<IngestionResultsSortBy>('created_at');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  // v5.135.0 — the page used to request skip:0/limit:100 unconditionally and
  // discard the `total` the endpoint already returns, so anything past the
  // 100th upload was unreachable by browsing and the truncation was invisible.
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await getIngestionResults({
        skip: page * pageSize,
        limit: pageSize,
        status: statusFilter === 'all' ? undefined : statusFilter,
        search: debouncedSearchText.trim() || undefined,
        sortBy,
        sortOrder,
      });
      setData(result);
    } catch (err: unknown) {
      setError(formatApiError(err, 'Failed to load ingestion results.'));
    } finally {
      setLoading(false);
    }
  };

  // Refetch whenever any filter or sort knob changes (debouncedSearchText
  // is the 300ms-stable view of the search box so a fast typist doesn't
  // fire a request per keystroke).
  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearchText, statusFilter, sortBy, sortOrder, page, pageSize]);

  // A filter/sort change can shrink the result set below the current page, so
  // go back to the first page rather than landing on an empty one.
  useEffect(() => { setPage(0); }, [debouncedSearchText, statusFilter, sortBy, sortOrder]);

  const handleViewParseError = async (item: IngestionResultItem) => {
    // Audit CRIT-8 — pre-fix this catch synthesized a fake ParseError
    // from row data and opened the dialog showing "No details
    // available". Operators believed they were inspecting backend
    // data; they were inspecting an invention. We now surface the
    // failure honestly and refuse to open the dialog.
    try {
      // item.id is the INGESTION JOB id; this endpoint wants a ParseError id.
      // They are independent sequences that overlap, so passing the job id
      // didn't 404 — it returned whichever parse error happened to share the
      // number, and the dialog presented it as this row's detail. That is the
      // same "showing an invention as backend data" failure the CRIT-8 note
      // below says was closed.
      if (item.parse_error_id == null) {
        toast.error(
          `Ingestion #${item.id} has no recorded parse error to open.`,
          { id: `pe-detail-${item.id}` },
        );
        return;
      }
      const detail = await getParseError(item.parse_error_id);
      setSelectedParseError(detail);
      setDetailDialogOpen(true);
    } catch (err) {
      toast.error(
        formatApiError(err, `Couldn't load full details for ingestion #${item.id}.`),
        { id: `pe-detail-${item.id}` },
      );
    }
  };

  // Open and scroll to the row the caller linked to, once it has loaded. The
  // param is cleared afterwards so a later manual collapse isn't undone by a
  // re-render, and so the URL doesn't keep re-focusing on refresh.
  useEffect(() => {
    if ((focusErrorId === null && focusJobId === null) || loading) return;
    // Scans links with the PARSE ERROR id, so resolve it back to the row that
    // produced it. Matching it against `i.id` (the job id) meant the link
    // almost never focused anything, and on a numeric collision focused an
    // unrelated row.  A `job_id` link IS the row id.
    const row = (data?.items ?? []).find(
      (i) =>
        (focusErrorId !== null && i.parse_error_id === focusErrorId) ||
        (focusJobId !== null && i.id === focusJobId),
    );
    const clearFocus = () =>
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.delete('error_id');
        next.delete('job_id');
        return next;
      }, { replace: true });
    if (!row) {
      // Not on this page. Clear the param so it doesn't re-fire on every
      // refetch, and say so rather than leaving the operator on a list that
      // silently ignored their link.
      clearFocus();
      const what = focusErrorId !== null ? `error #${focusErrorId}` : `job #${focusJobId}`;
      toast.info(
        `Ingestion result for ${what} isn't on this page — search or page to it.`,
        { id: `pe-focus-${focusErrorId ?? focusJobId}` },
      );
      return;
    }
    setExpandedRow(row.id);
    requestAnimationFrame(() => {
      document
        .querySelector(`[data-ingestion-row="${row.id}"]`)
        ?.scrollIntoView({ block: 'center' });
    });
    clearFocus();
  }, [focusErrorId, focusJobId, loading, data, setSearchParams]);

  const summary = data?.summary;
  // v2.86.2 — items come pre-filtered + pre-sorted from the server; no
  // more client-side filtering of the partial slice.  The old
  // useMemo-over-allItems block was removed alongside.
  const items = data?.items ?? [];
  // The true match count for the active filters — distinct from items.length,
  // which is only the current page.
  const totalMatching = data?.total ?? 0;

  const copyError = async (text: string) => {
    if (await copyToClipboard(text)) {
      toast.success('Copied error details', { id: 'copy-pe' });
    } else {
      toast.error('Could not copy to clipboard');
    }
  };

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center justify-between gap-sm">
        <h1 className="text-page-title">Ingestion Results</h1>
        <div className="flex flex-wrap items-center gap-xs">
          <div className="relative min-w-56">
            {/* v2.86.2 — server-side search across filename + error +
                last_error.  Replaces the old client-side filename-only
                filter that silently missed matches outside the loaded slice. */}
            <Search
              className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="search"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search filename or error…"
              aria-label="Search ingestion results by filename or error message"
              className="pl-xl"
            />
          </div>
          {/* The status filter is the chip strip below (v5.242.0). */}
          {/* v2.86.2 — sort key + direction.  Two separate selects keep
              the dropdown content short; the previous single-control
              "Newest / Oldest / A→Z / …" pattern proliferates options
              factorially as more sort keys land. */}
          <Select value={sortBy} onValueChange={(v) => setSortBy(v as IngestionResultsSortBy)}>
            <SelectTrigger className="min-w-36" aria-label="Sort by">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="created_at">Sort: Uploaded</SelectItem>
              <SelectItem value="original_filename">Sort: Filename</SelectItem>
              <SelectItem value="status">Sort: Status</SelectItem>
              <SelectItem value="tool_name">Sort: Tool</SelectItem>
              <SelectItem value="file_size">Sort: File size</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="icon"
            onClick={() => setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))}
            aria-label={`Toggle sort direction (currently ${sortOrder === 'asc' ? 'ascending' : 'descending'})`}
            title={sortOrder === 'asc' ? 'Ascending — click for descending' : 'Descending — click for ascending'}
          >
            {sortOrder === 'asc' ? <ChevronUp className="size-4" aria-hidden /> : <ChevronDown className="size-4" aria-hidden />}
          </Button>
          <Button variant="outline" onClick={loadData} disabled={loading}>
            <RefreshCw className={cn('size-4', loading && 'animate-spin')} aria-hidden /> Refresh
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* v5.242.0 — the counts ARE the filter. This was five stat cards that
          did nothing when clicked, beside a dropdown that filtered by the same
          statuses; two of the cards ("Total Hosts" / "Total Ports") summed
          per-scan history rows, so an 87-host project read well over a
          thousand. One strip: each count sets the status filter, and "Needs
          attention" (failed or finished partial, not dismissed) is the view
          Operations' "Inspect import errors" opens. */}
      {summary && (
        <div className="mb-md flex flex-wrap items-center gap-xs" role="group" aria-label="Filter by status">
          {statusChips(summary, statusFilter).map((chip) => {
            const active = statusFilter === chip.value;
            return (
              <button
                key={chip.value}
                type="button"
                aria-pressed={active}
                title={chip.hint}
                onClick={() => setStatusFilter(active && chip.value !== 'all' ? 'all' : chip.value)}
                className={cn(
                  'inline-flex items-center gap-xs rounded-chip border px-sm py-xxs text-metadata focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  active
                    ? 'border-primary bg-primary/10 text-foreground'
                    : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
                )}
              >
                <span>{chip.label}</span>
                <strong className={cn('tabular-nums', chip.tone && chip.count > 0 ? chip.tone : 'text-foreground')}>
                  {chip.count.toLocaleString()}
                </strong>
              </button>
            );
          })}
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          {/* Horizontal scroll wrapper — the table-fixed widths sum to
              ~1080px; on narrower viewports the inner table would push
              past the card and create a page-level horizontal scroll
              (which the UI Style Guide bans).  Scroll lives on this
              wrapper instead so only the table moves. */}
          <div className="overflow-x-auto">
            <Table className="table-fixed w-full">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10" />
                  {/* w-24 (96px) overflowed for `processing` (10 chars
                      + chip padding ~115-125px) into the Filename
                      column.  w-32 (128px) clears every value in
                      STATUS_VARIANT. */}
                  <TableHead className="w-32">Status</TableHead>
                  <TableHead className="w-56">Filename</TableHead>
                  <TableHead className="w-24">Tool</TableHead>
                  <TableHead className="w-24">Hosts</TableHead>
                  <TableHead className="w-24">Ports</TableHead>
                  <TableHead className="w-24">Services</TableHead>
                  <TableHead className="w-20">Size</TableHead>
                  <TableHead className="w-24">Duration</TableHead>
                  <TableHead className="w-32">Uploaded</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <TableRow>
                    <TableCell colSpan={10} className="py-xxl text-center">
                      <Loader2 className="mr-xs inline size-4 animate-spin" aria-hidden />
                      <span>Loading ingestion results…</span>
                    </TableCell>
                  </TableRow>
                ) : items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={10} className="py-xxl text-center">
                      <CloudUpload className="mx-auto mb-sm size-12 text-muted-foreground" aria-hidden />
                      <p className="mb-xs text-subheading text-muted-foreground">No upload history yet</p>
                      <p className="mx-auto mb-md max-w-md text-metadata text-muted-foreground">
                        Parse errors and warnings appear here after a scan is uploaded.
                      </p>
                      <Button onClick={() => navigate('/scans')}>
                        <CloudUpload className="size-4" aria-hidden /> Go to Scans
                      </Button>
                    </TableCell>
                  </TableRow>
                ) : (
                  items.map((item) => {
                    const isExpanded = expandedRow === item.id;
                    const stats = item.stats;
                    return (
                      <Fragment key={item.id}>
                        <TableRow
                          // Scroll target for a ?error_id= deep link from Scans.
                          data-ingestion-row={item.id}
                          onClick={() => setExpandedRow((prev) => (prev === item.id ? null : item.id))}
                          className="cursor-pointer"
                        >
                          <TableCell>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-expanded={isExpanded}
                              aria-label={isExpanded ? 'Collapse details' : 'Expand details'}
                              onClick={(e) => {
                                e.stopPropagation();
                                setExpandedRow((prev) => (prev === item.id ? null : item.id));
                              }}
                            >
                              {isExpanded ? <ChevronUp className="size-4" aria-hidden /> : <ChevronDown className="size-4" aria-hidden />}
                            </Button>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col items-start gap-xxs">
                              <StatusBadge status={item.status} />
                              {/* "Completed" alone hid that part of the file
                                  never made it into the inventory. */}
                              {item.partial && (
                                <Badge variant="warning" className="whitespace-nowrap"
                                  title={item.parser_warnings ?? 'Part of this file was not imported'}>
                                  partial
                                </Badge>
                              )}
                              {item.dismissed_at && (
                                <span className="text-caption text-muted-foreground"
                                  title={`Dismissed ${new Date(item.dismissed_at).toLocaleString()}`}>
                                  dismissed
                                </span>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <p className="truncate font-mono text-caption">{item.original_filename}</p>
                              </TooltipTrigger>
                              <TooltipContent>{item.original_filename}</TooltipContent>
                            </Tooltip>
                          </TableCell>
                          {/* `truncate` doesn't work directly on a
                              display:table-cell — text must live in a
                              block child for the ellipsis to take.
                              (UI Style Guide RSP·H6.) */}
                          <TableCell>
                            <p className="truncate" title={item.tool_name || undefined}>
                              {safeFallback(item.tool_name)}
                            </p>
                          </TableCell>
                          <TableCell>{stats ? `${stats.hosts_up}/${stats.hosts_parsed} up` : '-'}</TableCell>
                          <TableCell>{stats ? `${stats.open_ports}/${stats.ports_found} open` : '-'}</TableCell>
                          <TableCell>{stats ? stats.services_detected : '-'}</TableCell>
                          <TableCell>{formatFileSize(item.file_size)}</TableCell>
                          <TableCell>{formatDuration(item.duration_seconds)}</TableCell>
                          <TableCell>{timeAgo(item.created_at)}</TableCell>
                        </TableRow>
                        {isExpanded && (
                          <TableRow>
                            <TableCell colSpan={10} className="bg-accent/30 p-md">
                              <RowDetail item={item} onViewParseError={handleViewParseError} navigate={navigate} onChanged={() => void loadData()} />
                            </TableCell>
                          </TableRow>
                        )}
                      </Fragment>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
          {/* Server-paged: the endpoint returns `total` for the active
              filters, so the operator can both see how much exists and reach
              it. Without this the list silently ended at the first page. */}
          {totalMatching > 0 && (
            <div className="mt-sm flex flex-wrap items-center justify-between gap-sm">
              <p className="text-caption text-muted-foreground" role="status" aria-live="polite">
                {items.length === 0
                  ? 'No uploads match these filters'
                  : `Showing ${page * pageSize + 1}–${page * pageSize + items.length} of ${totalMatching.toLocaleString()}`}
              </p>
              <div className="flex items-center gap-xs">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(p - 1, 0))}
                  disabled={page === 0 || loading}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => p + 1)}
                  disabled={loading || (page + 1) * pageSize >= totalMatching}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Parse-error detail dialog */}
      <Dialog open={detailDialogOpen} onOpenChange={(next) => !next && setDetailDialogOpen(false)}>
        {/* Audit RSP·M16 — use the size prop instead of bypassing it
            with max-w-3xl, and wrap the long body in DialogBody so the
            footer stays pinned while the body scrolls. */}
        <DialogContent size="lg">
          <DialogHeader>
            <DialogTitle className="flex items-center justify-between">
              <span>Parse Error Details</span>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => setDetailDialogOpen(false)}
                aria-label="Close dialog"
              >
                <CloseIcon className="size-4" aria-hidden />
              </Button>
            </DialogTitle>
          </DialogHeader>
          {selectedParseError && (
            <DialogBody className="flex flex-col gap-md">
              <div className="grid grid-cols-1 gap-md md:grid-cols-2">
                <div>
                  <p className="mb-xs text-subheading font-semibold">File Information</p>
                  <p className="break-words"><strong>Filename:</strong> {selectedParseError.filename}</p>
                  <p><strong>File Type:</strong> {safeFallback(selectedParseError.file_type)}</p>
                  <p><strong>File Size:</strong> {formatFileSize(selectedParseError.file_size)}</p>
                  <p><strong>Error Type:</strong> {selectedParseError.error_type}</p>
                </div>
                <div>
                  <p className="mb-xs text-subheading font-semibold">Status</p>
                  <StatusBadge status={selectedParseError.status} />
                </div>
              </div>
              <div>
                <p className="mb-xs text-subheading font-semibold">User Message</p>
                <Alert variant="info">
                  <AlertDescription>
                    {selectedParseError.user_message || 'No user-friendly message available'}
                  </AlertDescription>
                </Alert>
              </div>
              <div>
                <div className="mb-xs flex items-center justify-between">
                  <p className="text-subheading font-semibold">Technical Details</p>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() =>
                          copyError(
                            `${selectedParseError.error_type}: ${selectedParseError.error_message}\n${selectedParseError.user_message || ''}`,
                          )
                        }
                        aria-label="Copy error details"
                      >
                        <Copy className="size-4" aria-hidden />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Copy error</TooltipContent>
                  </Tooltip>
                </div>
                <Alert variant="destructive">
                  <AlertDescription className="font-mono break-words">
                    {selectedParseError.error_message}
                  </AlertDescription>
                </Alert>
              </div>
              <Accordion type="multiple">
                {selectedParseError.file_preview && (
                  <AccordionItem value="preview">
                    <AccordionTrigger>File Preview</AccordionTrigger>
                    <AccordionContent>
                      <pre className="max-h-72 overflow-auto rounded-control bg-muted p-sm font-mono text-caption">
                        {selectedParseError.file_preview}
                      </pre>
                    </AccordionContent>
                  </AccordionItem>
                )}
                {selectedParseError.error_details && (
                  <AccordionItem value="details">
                    <AccordionTrigger>Technical Error Details</AccordionTrigger>
                    <AccordionContent>
                      <pre className="max-h-96 overflow-auto rounded-control bg-muted p-sm font-mono text-caption">
                        {JSON.stringify(selectedParseError.error_details, null, 2)}
                      </pre>
                    </AccordionContent>
                  </AccordionItem>
                )}
              </Accordion>
            </DialogBody>
          )}
          <DialogFooter>
            <Button onClick={() => setDetailDialogOpen(false)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

/**
 * Dismiss a failed or partial import (v5.242.0). Operations lists both as
 * blocked until someone does; the action lived only on the Scans page's live
 * queue, and only for failed jobs — so the page Operations sends people to
 * could not clear what it was sent to clear. Dismissing acknowledges the row;
 * it stays in this list, still failed / still partial.
 */
const DismissAction: React.FC<{ item: IngestionResultItem; onChanged: () => void }> = ({ item, onChanged }) => {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const blocked = item.status === 'failed' || (item.status === 'completed' && !!item.partial);
  if (!blocked) return null;
  if (item.dismissed_at) {
    return (
      <span className="text-caption text-muted-foreground">
        Dismissed {new Date(item.dismissed_at).toLocaleString()} — no longer listed as blocked
      </span>
    );
  }
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={saving}
      title="Acknowledge this import. It stays in this list; it stops being listed as blocked on Operations."
      onClick={async () => {
        setSaving(true);
        try {
          await dismissIngestionJob(item.id);
          onChanged();
        } catch (err) {
          toast.error(formatApiError(err, 'Could not dismiss this import.'));
        } finally {
          setSaving(false);
        }
      }}
    >
      {saving && <Loader2 className="size-3 animate-spin" aria-hidden />}
      Dismiss
    </Button>
  );
};

const RowDetail: React.FC<{
  item: IngestionResultItem;
  onViewParseError: (item: IngestionResultItem) => void;
  navigate: ReturnType<typeof useNavigate>;
  onChanged: () => void;
}> = ({ item, onViewParseError, navigate, onChanged }) => {
  // v5.231.0 — retry with a reviewed format / explicit re-process, on the
  // retained file (phase E).
  const [retryOpen, setRetryOpen] = useState(false);
  const [reprocessOpen, setReprocessOpen] = useState(false);
  const retainedNote = item.file_retained
    ? `File retained${item.retained_until ? ` until ${new Date(item.retained_until).toLocaleDateString()}` : ''}`
    : 'File no longer retained — re-upload to import again';

  if (item.status === 'failed' || item.error) {
    return (
      <div className="flex flex-col gap-sm">
        <Alert variant="destructive">
          <AlertDescription>
            <p className="font-semibold">
              {item.error?.error_type ? `Error type: ${item.error.error_type}` : 'Upload failed'}
            </p>
            <p className="mt-xxs font-mono text-caption break-words">
              {item.error?.error_message || 'No error message available'}
            </p>
            {item.error?.user_message && (
              <p className="mt-xs">{item.error.user_message}</p>
            )}
          </AlertDescription>
        </Alert>
        <div className="flex flex-wrap items-center gap-xs">
          <Button size="sm" variant="outline" onClick={() => onViewParseError(item)}>
            View Details
          </Button>
          {item.file_retained && (
            <Button size="sm" onClick={() => setRetryOpen(true)}>
              Review format and retry
            </Button>
          )}
          <DismissAction item={item} onChanged={onChanged} />
          <span className="text-caption text-muted-foreground">{retainedNote}</span>
        </div>
        <FormatRetryDialog
          open={retryOpen}
          onOpenChange={setRetryOpen}
          jobId={item.id}
          filename={item.original_filename}
          mode="retry"
          onDone={onChanged}
        />
      </div>
    );
  }

  if (item.status === 'staged') {
    return (
      <div className="flex flex-col gap-sm">
        <p className="text-metadata text-muted-foreground">
          Stored but not imported. Nothing runs until you start it; a staged file expires 24 hours after upload.
        </p>
        <div className="flex flex-wrap items-center gap-xs">
          <Button size="sm" onClick={() => setRetryOpen(true)} disabled={!item.file_retained}>
            Review format and import
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={async () => {
              try {
                await discardIngestionJob(item.id);
                onChanged();
              } catch (err) {
                console.error('Could not discard the staged job:', err);
              }
            }}
          >
            Discard
          </Button>
        </div>
        <FormatRetryDialog
          open={retryOpen}
          onOpenChange={setRetryOpen}
          jobId={item.id}
          filename={item.original_filename}
          mode="start"
          onDone={onChanged}
        />
      </div>
    );
  }

  const stats = item.stats;
  return (
    <div className="flex flex-col gap-sm">
      {/* v5.242.0 — a partial import says what was lost, first. */}
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
            <div><DismissAction item={item} onChanged={onChanged} /></div>
          </AlertDescription>
        </Alert>
      )}
      {/* v5.222.0 — the import result, the same block the upload banner
          and the scan page show, so a completed job is reconcilable here. */}
      {item.scan_id != null && item.status === 'completed' && (
        <CompletedImportResult scanId={item.scan_id} />
      )}
      {/* v5.227.0 — the format chain, so "how was this file read" is on
          record: detected → override → parsed by. */}
      {(item.final_format_label || item.detected_format_label || item.format_override_label) && (
        <p className="text-caption text-muted-foreground">
          {item.detected_format_label && (
            <>Detected as <span className="text-foreground">{item.detected_format_label}</span></>
          )}
          {item.format_override_label && (
            <>{item.detected_format_label ? ' · ' : ''}you chose <span className="text-foreground">{item.format_override_label}</span></>
          )}
          {item.final_format_label && (
            <>{item.detected_format_label || item.format_override_label ? ' · ' : ''}parsed by <span className="text-foreground">{item.final_format_label}</span></>
          )}
          {item.source_tool && <> · source tool <span className="text-foreground">{item.source_tool}</span></>}
        </p>
      )}
      <div className="grid grid-cols-2 gap-sm md:grid-cols-4">
        <Field label="Scan Type" value={safeFallback(item.scan_type)} />
        <Field label="Tool" value={safeFallback(item.tool_name)} />
        {stats && (
          <>
            <Field label="Hosts Parsed" value={stats.hosts_parsed} />
            <Field label="Hosts Up" value={stats.hosts_up} />
            <Field label="Open Ports" value={stats.open_ports} />
            <Field label="Services" value={stats.services_detected} />
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-xs">
        {item.scan_id != null && (
          <Button size="sm" onClick={() => navigate(`/scans/${item.scan_id}`)}>
            <ExternalLink className="size-4" aria-hidden /> View Scan
          </Button>
        )}
        {item.status === 'completed' && item.file_retained && (
          <Button size="sm" variant="outline" onClick={() => setReprocessOpen(true)}
            title="Run the retained file through the pipeline again as a new import">
            Re-process…
          </Button>
        )}
        {item.status === 'completed' && (
          <span className="text-caption text-muted-foreground">{retainedNote}</span>
        )}
      </div>
      <FormatRetryDialog
        open={reprocessOpen}
        onOpenChange={setReprocessOpen}
        jobId={item.id}
        filename={item.original_filename}
        mode="reprocess"
        priorScanId={item.scan_id}
        onDone={onChanged}
      />
    </div>
  );
};

/** Fetches the scan row's summary for a completed job and renders its
 *  import result. Quiet on failure: the fields beneath still say what parsed. */
const CompletedImportResult: React.FC<{ scanId: number }> = ({ scanId }) => {
  const [row, setRow] = useState<Scan | null>(null);
  useEffect(() => {
    let cancelled = false;
    getScans(0, 1, { ids: [scanId] })
      .then((rows) => {
        if (!cancelled) setRow(rows[0] ?? null);
      })
      .catch(() => {
        if (!cancelled) setRow(null);
      });
    return () => {
      cancelled = true;
    };
  }, [scanId]);
  if (!row) return null;
  return (
    <div className="rounded-panel border border-border p-sm">
      <p className="mb-xxs text-caption text-muted-foreground">Import result</p>
      {/* The row prints the format chain itself (it has it for failed jobs too). */}
      <ImportResult scan={row} showFormatChain={false} />
    </div>
  );
};

const Field: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div>
    <p className="text-caption text-muted-foreground">{label}</p>
    <p className="text-metadata text-foreground">{value}</p>
  </div>
);

interface StatusChip {
  /** The `?status=` value; `needs_attention` is a view, not a job status. */
  value: string;
  label: string;
  count: number;
  hint: string;
  tone?: string;
}

/** The status filter, as counts. Transient states appear only while they hold
 *  something; "Needs attention" always shows, because zero is an answer. */
const statusChips = (summary: IngestionResultsResponse['summary'], active: string): StatusChip[] => {
  const queued = summary.total_queued ?? 0;
  const processing = summary.total_processing ?? 0;
  const staged = summary.total_staged ?? 0;
  const all = summary.total_completed + summary.total_failed + queued + processing + staged;
  const chips: StatusChip[] = [
    { value: 'all', label: 'All uploads', count: all, hint: 'Every upload in this project' },
    {
      value: 'needs_attention', label: 'Needs attention', count: summary.total_needs_attention ?? 0,
      tone: 'text-warning',
      hint: 'Failed, or finished partial, and not dismissed — what Operations lists as blocked',
    },
    { value: 'failed', label: 'Failed', count: summary.total_failed, tone: 'text-destructive', hint: 'Nothing from these files is in the inventory (dismissed ones included)' },
    { value: 'completed', label: 'Completed', count: summary.total_completed, hint: 'Imported — a partial import is marked on its row' },
  ];
  if (staged > 0) chips.push({ value: 'staged', label: 'Awaiting format review', count: staged, hint: 'Uploaded, not started' });
  if (queued > 0) chips.push({ value: 'queued', label: 'Queued', count: queued, hint: 'Waiting for a worker' });
  if (processing > 0) chips.push({ value: 'processing', label: 'Processing', count: processing, hint: 'Being imported now' });
  // A deep link can carry a filter whose chip is hidden (an empty transient
  // state): the active filter must always be visible, and clearable.
  if (!chips.some((c) => c.value === active)) {
    chips.push({ value: active, label: active, count: 0, hint: 'The active filter — click to clear' });
  }
  return chips;
};

export default ParseErrors;
