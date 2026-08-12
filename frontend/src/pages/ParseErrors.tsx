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
  CheckCircle2,
  AlertOctagon,
  Server,
  Network,
  Copy,
  Loader2,
  Search,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { Input } from '../components/ui/input';
import {
  getIngestionResults,
  getParseError,
  type IngestionResultItem,
  type IngestionResultsResponse,
  type IngestionResultsSortBy,
  type ParseError,
} from '../services/api';
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

const timeAgo = (dateString: string | null): string => {
  if (!dateString) return '-';
  const now = Date.now();
  const then = new Date(dateString).getTime();
  const diff = now - then;
  if (diff < 0) return 'just now';
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(dateString).toLocaleDateString();
};

const STATUS_VARIANT: Record<string, 'success' | 'destructive' | 'info' | 'muted'> = {
  completed: 'success',
  failed: 'destructive',
  processing: 'info',
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
    if (focusErrorId === null || loading) return;
    // Scans links with the PARSE ERROR id, so resolve it back to the row that
    // produced it. Matching it against `i.id` (the job id) meant the link
    // almost never focused anything, and on a numeric collision focused an
    // unrelated row.
    const row = (data?.items ?? []).find((i) => i.parse_error_id === focusErrorId);
    if (!row) {
      // Not on this page. Clear the param so it doesn't re-fire on every
      // refetch, and say so rather than leaving the operator on a list that
      // silently ignored their link.
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.delete('error_id');
        return next;
      }, { replace: true });
      toast.info(
        `Ingestion result for error #${focusErrorId} isn't on this page — search or page to it.`,
        { id: `pe-focus-${focusErrorId}` },
      );
      return;
    }
    setExpandedRow(row.id);
    requestAnimationFrame(() => {
      document
        .querySelector(`[data-ingestion-row="${row.id}"]`)
        ?.scrollIntoView({ block: 'center' });
    });
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('error_id');
      return next;
    }, { replace: true });
  }, [focusErrorId, loading, data, setSearchParams]);

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
          {/* v2.86.2 — status filter (all / queued / processing /
              completed / failed).  Mirrors the IngestionJob.status enum. */}
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="min-w-36" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="queued">Queued</SelectItem>
              <SelectItem value="processing">Processing</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
            </SelectContent>
          </Select>
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

      {summary && (
        <div className="mb-md grid grid-cols-2 gap-sm md:grid-cols-5">
          {/* Was items.length — the current page only. Its neighbours are
              project-wide, so past 100 uploads this tile could read a total
              SMALLER than the Completed count sitting next to it. `total` is
              the match count for the active filters, which is what the label
              means. */}
          <StatCard label="Total Uploads" value={totalMatching.toLocaleString()} Icon={CloudUpload} tone="text-muted-foreground" />
          <StatCard label="Completed" value={summary.total_completed} Icon={CheckCircle2} tone="text-success" />
          <StatCard label="Failed" value={summary.total_failed} Icon={AlertOctagon} tone="text-destructive" />
          <StatCard label="Total Hosts" value={summary.total_hosts.toLocaleString()} Icon={Server} tone="text-info" />
          <StatCard label="Total Ports" value={summary.total_ports.toLocaleString()} Icon={Network} tone="text-warning" />
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
                          <TableCell><StatusBadge status={item.status} /></TableCell>
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
                              <RowDetail item={item} onViewParseError={handleViewParseError} navigate={navigate} />
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

const RowDetail: React.FC<{
  item: IngestionResultItem;
  onViewParseError: (item: IngestionResultItem) => void;
  navigate: ReturnType<typeof useNavigate>;
}> = ({ item, onViewParseError, navigate }) => {
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
        <div>
          <Button size="sm" variant="outline" onClick={() => onViewParseError(item)}>
            View Details
          </Button>
        </div>
      </div>
    );
  }

  const stats = item.stats;
  return (
    <div className="flex flex-col gap-sm">
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
      {item.scan_id != null && (
        <div>
          <Button size="sm" onClick={() => navigate(`/scans/${item.scan_id}`)}>
            <ExternalLink className="size-4" aria-hidden /> View Scan
          </Button>
        </div>
      )}
    </div>
  );
};

const Field: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div>
    <p className="text-caption text-muted-foreground">{label}</p>
    <p className="text-metadata text-foreground">{value}</p>
  </div>
);

const StatCard: React.FC<{
  label: string;
  value: string | number;
  Icon: LucideIcon;
  tone: string;
}> = ({ label, value, Icon, tone }) => (
  <Card>
    <CardContent className="flex items-center gap-sm p-md">
      <Icon className={cn('size-6 shrink-0', tone)} aria-hidden />
      <div className="min-w-0">
        <p className="text-caption text-muted-foreground">{label}</p>
        <p className="text-section-title font-semibold text-foreground">{value}</p>
      </div>
    </CardContent>
  </Card>
);

export default ParseErrors;
