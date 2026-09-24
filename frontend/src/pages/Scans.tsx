import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  AlertCircle,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  MoreHorizontal,
  GitCompareArrows,
  Hourglass,
  Loader2,
  Info,
  Trash2,
  Upload,
  PauseCircle,
} from 'lucide-react';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
import {
  getScans,
  getScansSummary,
  deleteScan,
  getScanDeletionImpact,
  getIngestionJobsByIds,
  getRecentIngestionJobs,
  getStagedIngestionJobs,
  dismissIngestionJob,
  cancelIngestionJob,
  discardIngestionJob,
  discardStagedJobs,
  retryIngestionJob,
  getScanCommandExplanation,
  getScanBatches,
  getImportHistory,
  getScanInventoryMarker,
} from '../services/api';
import type {
  Scan,
  ScanInventorySummary,
  IngestionJob,
  CommandExplanation,
  ScanDeletionImpact,
  ScanBatchSummary,
  ImportHistoryEntry,
} from '../services/api';
import LastUpdated from '../components/LastUpdated';
import { ListPageSkeleton } from '../components/PageSkeleton';
import { useToast } from '../contexts/ToastContext';
import { useProject } from '../contexts/ProjectContext';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';
// From the barrel, like every other call here: a direct submodule import
// bypasses a page test's mock and loads the real HTTP client.
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { BreakableName } from '../components/ui/breakable-name';
import { Button } from '../components/ui/button';
import PostureLead from '../components/posture/PostureLead';
import PostureSection from '../components/posture/PostureSection';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '../components/ui/dropdown-menu';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { formatRelativeTime } from '../utils/relativeTime';
import ScanContribution from '../components/scans/ScanContribution';
import ImportResult from '../components/scans/ImportResult';
import UploadReviewDialog from '../components/scans/UploadReviewDialog';
import { ScanBatchRow } from '../components/scans/ScanBatchList';
import { ROW_LINK_CLASS, ScanRowActions } from '../components/scans/ScanRowActions';
import { hydrateHistoryRows, orderHistoryRows, type HistoryFilters } from '../utils/importHistory';
import { ScanWhenCell, ViewerZoneNote } from '../components/scans/ScanTimeCells';
import { formatDuration } from '../utils/scanTime';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { ListFilterBar, ListFilterSearch } from '../components/ListFilterBar';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { InfoTip } from '../components/ui/info-tip';
import { cn } from '../utils/cn';

/** A queue row's status, in words beside its icon. */
const JOB_STATUS_LABEL: Record<string, string> = {
  staged: 'Needs review',
  queued: 'Queued',
  processing: 'Processing',
  failed: 'Failed',
  completed: 'Completed',
};

export default function Scans() {
  const navigate = useNavigate();
  const toast = useToast();
  const [confirmDialog, confirm] = useConfirm();
  const [scans, setScans] = useState<Scan[]>([]);
  // Filter-aware totals for the headline cards — fetched server-side so they
  // reflect every matching scan, not just the loaded (paginated) page.
  const [inventorySummary, setInventorySummary] = useState<ScanInventorySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const fetchGenRef = useRef(0);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [scanToDelete, setScanToDelete] = useState<Scan | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deletionImpact, setDeletionImpact] = useState<ScanDeletionImpact | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [impactError, setImpactError] = useState(false);

  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  // The results banner: one entry per file the review dialog (or the staged
  // queue's "Review and import") has STARTED, followed by job id to its import
  // result (v5.222.0, design review item 5):
  //   received → processing → imported | partial | failed
  // v5.247.0 — the transfer itself, a refused duplicate and "Import again" all
  // live in UploadReviewDialog / useUploadReview now. This page kept its own
  // dropzone, uploader, duplicate handling and a stuck-upload watchdog for
  // them; the dropzone was never attached to an element, so none of it could
  // run. Removed with their three states ('uploading', 'error', 'duplicate').
  const [uploadProgress, setUploadProgress] = useState<
    Record<
      string,
      {
        filename: string;
        status: 'received' | 'processing' | 'imported' | 'partial' | 'failed';
        error?: string;
        startedAt: number;
        jobId?: number;
        /** The worker's latest progress message while processing. */
        jobMessage?: string | null;
        /** The scan row's summary once the job completed — the import result. */
        result?: Scan | null;
        parseErrorId?: number | null;
        batchId?: number;
      }
    >
  >({});
  // The latest entries, readable outside a state updater (where the banner
  // decides which finished files still need their import result), and the
  // entries whose result has already been asked for.
  const uploadProgressRef = useRef(uploadProgress);
  uploadProgressRef.current = uploadProgress;
  const resultRequestedRef = useRef<Set<string>>(new Set());

  const [activeJobIds, setActiveJobIds] = useState<number[]>([]);
  // v5.271.0 — staged files handed back to the upload review ("Review N
  // waiting", a queue row's Review, a batch's Review).  It replaced the
  // one-file FormatRetryDialog, which made a 26-file drop 26 dialogs.
  const [reviewResume, setReviewResume] = useState<{ jobs: IngestionJob[] } | null>(null);
  const [activeJobs, setActiveJobs] = useState<Record<number, IngestionJob>>({});
  const [recentJobs, setRecentJobs] = useState<IngestionJob[]>([]);
  // v5.271.0 — every staged job, not just those among the 25 recent: the
  // 26th file of a drop was missing from the queue, its Review and its Discard.
  const [stagedJobs, setStagedJobs] = useState<IngestionJob[]>([]);
  const [recentJobsFetched, setRecentJobsFetched] = useState<Date | null>(null);
  const [recentJobsLoading, setRecentJobsLoading] = useState(false);
  // v5.270.0 — the queue could not be read: said, never shown as "nothing failed".
  const [recentJobsError, setRecentJobsError] = useState(false);

  const [expandedScanIds, setExpandedScanIds] = useState<number[]>([]);
  // v5.207.0 — upload batches, one row per sweep. Their files leave the flat
  // inventory unless the operator asks to list them individually.
  const [batches, setBatches] = useState<ScanBatchSummary[]>([]);
  // v5.239.0 — the import history's ORDER (batches and single files together,
  // newest first), from the server; `scans` and `batches` hold the rows.
  const [history, setHistory] = useState<ImportHistoryEntry[]>([]);
  const [historyPartial, setHistoryPartial] = useState(false);
  const [historyTotal, setHistoryTotal] = useState<number | null>(null);

  // ---------------------------------------------------------------------
  // Scan Inventory filters + pagination (v4.47.0 QoL pass).
  //
  // All filtering and sorting is server-side: the user has projects with
  // >100 scans, and client-side filtering on the first 100 would silently
  // miss matching scans further down the list.  The URL persists every
  // filter so analysts can bookmark / share a filtered view.
  //
  // Pagination is "Load more" rather than paged navigation — keeps the
  // append model simple, avoids needing a server-side total count, and
  // works smoothly with sort/filter changes (those reset back to skip=0).
  //
  // v2.86.2 — bumped initial page from 100 → 250 after a field report
  // that the page "capped at 100" because the Load More button at the
  // bottom of the table was off-screen and not noticed.  250 covers the
  // vast majority of installations in one page; the button stays as
  // the fallback for the long tail.
  // ---------------------------------------------------------------------
  const SCAN_LIMIT = 250;
  // History rows per page. Each page's rows are fetched by id, and the id
  // list rides in the query string, so this stays well under its cap.
  const HISTORY_PAGE = 100;
  const DATE_RANGE_PRESETS: ReadonlyArray<{ label: string; days: number | null }> = [
    { label: 'All time', days: null },
    { label: 'Last 7d', days: 7 },
    { label: 'Last 30d', days: 30 },
    { label: 'Last 90d', days: 90 },
  ];
  type SortBy = 'created_at' | 'start_time' | 'filename' | 'tool_name' | 'total_hosts' | 'new_hosts';
  type SortOrder = 'asc' | 'desc';

  const [urlParams, setUrlParams] = useSearchParams();
  const [toolFilter, setToolFilter] = useState(() => urlParams.get('tool') || '');
  const [searchText, setSearchText] = useState(() => urlParams.get('search') || '');
  const [dateRangeDays, setDateRangeDays] = useState<number | null>(() => {
    const raw = urlParams.get('days');
    if (!raw) return null;
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  });
  const [sortBy, setSortBy] = useState<SortBy>(() => {
    const raw = urlParams.get('sort_by') as SortBy | null;
    return raw && ['created_at', 'start_time', 'filename', 'tool_name', 'total_hosts', 'new_hosts'].includes(raw)
      ? raw
      : 'created_at';
  });
  const [sortOrder, setSortOrder] = useState<SortOrder>(() => {
    const raw = urlParams.get('sort_order');
    return raw === 'asc' ? 'asc' : 'desc';
  });
  const [showBatchFiles, setShowBatchFiles] = useState(() => urlParams.get('batch_files') === 'show');
  // v5.281.0 — who uploaded the file (an agent's uploads are its operator's).
  const [uploaderFilter, setUploaderFilter] = useState<number | null>(() => {
    const parsed = parseInt(urlParams.get('uploaded_by') || '', 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  });
  // v5.215.0 — whether informational Nessus observations are skipped: the
  // project's effective setting (its own choice, else the deployment default),
  // sent with each upload. UX review 2026-09-24 — changed in Project settings →
  // Imports; the upload dialog states it and links there.
  const { currentProject } = useProject();
  const skipInformational = currentProject?.skip_informational_effective ?? false;
  const debouncedSearchText = useDebouncedValue(searchText, 300);
  const [hasMoreScans, setHasMoreScans] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // Avoid the URL-sync effect firing during the very first render before
  // the user has touched anything — react-router would still write an
  // empty query string, which churns the browser history.
  const filtersInitialized = useRef(false);

  const hasActiveFilters = toolFilter !== '' || debouncedSearchText.trim() !== ''
    || dateRangeDays !== null || uploaderFilter !== null;
  const createdAfterIso = useMemo(() => {
    if (dateRangeDays == null) return undefined;
    return new Date(Date.now() - dateRangeDays * 24 * 60 * 60 * 1000).toISOString();
  }, [dateRangeDays]);
  // One filter object for every list, the summary and the batch rows, so
  // they can never disagree about what "the current filters" are.
  const listFilters = useMemo(
    () => ({
      search: debouncedSearchText.trim() || undefined,
      tool: toolFilter || undefined,
      createdAfter: createdAfterIso,
      uploadedBy: uploaderFilter ?? undefined,
    }),
    [debouncedSearchText, toolFilter, createdAfterIso, uploaderFilter],
  );
  const [expandedJobIds, setExpandedJobIds] = useState<Set<number>>(new Set());
  const [commandCache, setCommandCache] = useState<Record<number, CommandExplanation>>({});

  const toggleJobExpanded = (id: number) => {
    setExpandedJobIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const fetchRecentJobs = useCallback(async () => {
    setRecentJobsLoading(true);
    try {
      const [jobs, staged] = await Promise.all([
        getRecentIngestionJobs(25),
        // The staged list is a completion of the recent one; if it cannot
        // be read, the queue still shows the staged jobs among the recent.
        getStagedIngestionJobs().catch((err) => {
          console.error('Error fetching staged ingestion jobs:', err);
          return null;
        }),
      ]);
      setRecentJobs(jobs);
      setStagedJobs(staged ?? jobs.filter((j) => j.status === 'staged'));
      setRecentJobsFetched(new Date());
      setRecentJobsError(false);
    } catch (err) {
      console.error('Error fetching ingestion jobs:', err);
      setRecentJobsError(true);
    } finally {
      setRecentJobsLoading(false);
    }
  }, []);

  const hydrateHistory = useCallback(
    (items: ImportHistoryEntry[], filters: HistoryFilters) =>
      hydrateHistoryRows(items, filters, { getScans, getScanBatches }),
    [],
  );

  const fetchScans = useCallback(async () => {
    // Only the latest call applies its result: filters, the job poll and the
    // inventory marker all call this, and an older, slower response used to
    // land last (review 2026-09-23 R11).
    const gen = ++fetchGenRef.current;
    const current = () => gen === fetchGenRef.current;
    const filters = listFilters;
    if (showBatchFiles) {
      // All files: one flat, sortable list of every imported file.
      try {
        const data = await getScans(0, SCAN_LIMIT, { ...filters, sortBy, sortOrder, unbatched: false });
        if (!current()) return;
        setScans(data);
        setHasMoreScans(data.length === SCAN_LIMIT);
        setHistoryError(null);
      } catch (err) {
        if (!current()) return;
        console.error('Error fetching scans:', err);
        setHistoryError(formatApiError(err, 'Could not load the imported scans.'));
      }
      setBatches([]);
      setHistory([]);
      setHistoryPartial(false);
    } else {
      // Grouped by upload (v5.239.0): the SERVER decides the order of batches
      // and single files together; this page only fills the rows in.  They
      // were two cards paginated separately, which no client-side merge can
      // put in order past the first page.
      try {
        const page = await getImportHistory({ ...filters, limit: HISTORY_PAGE });
        const rows = await hydrateHistory(page.items, filters);
        if (!current()) return;
        setHistory(page.items);
        setHistoryTotal(typeof page.total === 'number' ? page.total : null);
        setScans(rows.scans);
        setBatches(rows.batches);
        setHistoryPartial(rows.partial);
        setHasMoreScans(page.has_more);
        setHistoryError(null);
      } catch (err) {
        if (!current()) return;
        // A failure is said as one; it used to read "No scans uploaded yet"
        // on a first load, and leave the previous filter's rows on a change.
        console.error('Error fetching import history:', err);
        setHistoryError(formatApiError(err, 'Could not load the import history.'));
      }
    }
    setLoading(false);
    // Headline totals are filter-aware and independent of pagination, so a
    // failure here must not block the table from rendering — fetch separately.
    try {
      const summary = await getScansSummary(filters);
      if (current()) setInventorySummary(summary);
    } catch (err) {
      console.error('Error fetching scan summary:', err);
    }
  }, [listFilters, sortBy, sortOrder, showBatchFiles, hydrateHistory]);

  const loadMoreScans = useCallback(async () => {
    if (loadingMore || !hasMoreScans) return;
    // A page that lands after the filters changed belongs to the old list.
    const gen = fetchGenRef.current;
    setLoadingMore(true);
    try {
      const filters = listFilters;
      if (!showBatchFiles) {
        const page = await getImportHistory({ ...filters, skip: history.length, limit: HISTORY_PAGE });
        const rows = await hydrateHistory(page.items, filters);
        if (gen !== fetchGenRef.current) return;
        setHistory((prev) => [...prev, ...page.items]);
        setScans((prev) => [...prev, ...rows.scans]);
        setBatches((prev) => [...prev, ...rows.batches]);
        if (rows.partial) setHistoryPartial(true);
        setHasMoreScans(page.has_more);
        return;
      }
      const data = await getScans(scans.length, SCAN_LIMIT, {
        ...filters,
        sortBy,
        sortOrder,
        unbatched: false,
      });
      if (gen !== fetchGenRef.current) return;
      setScans((prev) => [...prev, ...data]);
      setHasMoreScans(data.length === SCAN_LIMIT);
    } catch (err) {
      console.error('Error loading more scans:', err);
    } finally {
      setLoadingMore(false);
    }
  }, [
    scans.length,
    listFilters,
    sortBy,
    sortOrder,
    showBatchFiles,
    loadingMore,
    hasMoreScans,
    history.length,
  ]);

  // Per-row tool badge rendered as a clickable filter — same behaviour
  // as the section-header chips.  stopPropagation so it doesn't also
  // trigger the row's own expand/click handlers.
  const renderInlineToolBadge = (scan: Scan) => {
    const label = scan.tool_name || scan.scan_type || 'Unknown';
    const toolKey = (label || 'Other').toUpperCase();
    const active = toolFilter.toLowerCase() === toolKey.toLowerCase();
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setToolFilter(active ? '' : toolKey);
        }}
        aria-pressed={active}
        className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title={active ? `Clear ${toolKey} filter` : `Show only ${toolKey} scans`}
      >
        <Badge variant={active ? 'default' : 'outline'}>{label}</Badge>
      </button>
    );
  };

  // Sortable column header — clicking the same column toggles asc/desc;
  // clicking a different column switches sort and resets to desc.
  const handleSort = (column: SortBy) => {
    if (sortBy === column) {
      setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortBy(column);
    setSortOrder('desc');
  };
  const renderSortHeader = (column: SortBy, label: string, className?: string) => {
    const isSorted = sortBy === column;
    const ariaSort: React.AriaAttributes['aria-sort'] = isSorted
      ? sortOrder === 'asc'
        ? 'ascending'
        : 'descending'
      : 'none';
    return (
      <TableHead className={className} aria-sort={ariaSort}>
        <button
          type="button"
          onClick={() => handleSort(column)}
          className="inline-flex items-center gap-xxs rounded-control text-inherit hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Sort by ${label}, currently ${
            isSorted ? (sortOrder === 'asc' ? 'sorted ascending' : 'sorted descending') : 'not sorted'
          }`}
        >
          {label}
          {!isSorted && <ArrowUpDown className="size-3 opacity-50" aria-hidden />}
          {isSorted && sortOrder === 'asc' && <ArrowUp className="size-3" aria-hidden />}
          {isSorted && sortOrder === 'desc' && <ArrowDown className="size-3" aria-hidden />}
        </button>
      </TableHead>
    );
  };

  // v5.239.0 — the import history's rows, in the server's order.  Grouped by
  // upload is chronological by definition, so its headers do not sort; the
  // all-files view keeps the sortable ones.
  const historyRows = useMemo(() => orderHistoryRows(history, scans, batches), [history, scans, batches]);
  const tableRows = useMemo(
    () => (showBatchFiles
      ? scans.map((scan) => ({ kind: 'scan' as const, key: `scan-${scan.id}`, scan }))
      : historyRows),
    [showBatchFiles, scans, historyRows],
  );
  const historyHeader = (column: SortBy, label: string, className?: string) =>
    showBatchFiles
      ? renderSortHeader(column, label, className)
      : <TableHead className={className}>{label}</TableHead>;

  // URL sync — write the active filters/sort back to the URL whenever
  // they change so the browser back/forward + bookmark/share use cases
  // work.  Skipped on the very first render so we don't churn history
  // with a no-op write.
  useEffect(() => {
    if (!filtersInitialized.current) {
      filtersInitialized.current = true;
      return;
    }
    const next = new URLSearchParams(urlParams);
    if (debouncedSearchText.trim()) next.set('search', debouncedSearchText.trim());
    else next.delete('search');
    if (toolFilter) next.set('tool', toolFilter);
    else next.delete('tool');
    if (dateRangeDays != null) next.set('days', String(dateRangeDays));
    else next.delete('days');
    if (sortBy !== 'created_at') next.set('sort_by', sortBy);
    else next.delete('sort_by');
    if (sortOrder !== 'desc') next.set('sort_order', sortOrder);
    else next.delete('sort_order');
    if (showBatchFiles) next.set('batch_files', 'show');
    else next.delete('batch_files');
    if (uploaderFilter != null) next.set('uploaded_by', String(uploaderFilter));
    else next.delete('uploaded_by');
    setUrlParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearchText, toolFilter, dateRangeDays, sortBy, sortOrder, showBatchFiles, uploaderFilter]);

  useEffect(() => {
    fetchScans();
    fetchRecentJobs();
  }, [fetchScans, fetchRecentJobs]);

  const toggleScanExpanded = useCallback(
    (scanId: number) => {
      setExpandedScanIds((prev) => (prev.includes(scanId) ? prev.filter((id) => id !== scanId) : [...prev, scanId]));
      if (!commandCache[scanId]) {
        getScanCommandExplanation(scanId)
          .then((data) => setCommandCache((prev) => ({ ...prev, [scanId]: data })))
          // Audit FBK·L6 — on failure DON'T write to the cache.  The
          // pre-audit shape stored a synthetic "Failed to load" entry,
          // which then short-circuited every subsequent re-open via the
          // `!commandCache[scanId]` guard and the user could never
          // retry.  Leaving the entry undefined means the next click
          // re-triggers the fetch.
          .catch(() => {
            /* leave cache untouched so the next click retries */
          });
      }
    },
    [commandCache],
  );

  // v5.222.0 — the banner entry that submitted a job follows it: queued /
  // processing show the worker's message; completed fetches the scan row's
  // summary (the same numbers the inventory shows) and becomes 'imported' or
  // 'partial'; failed shows the error and links its parse error.  Entries
  // are keyed by upload key, so match on jobId.
  const applyJobsToUploadEntries = useCallback((jobs: IngestionJob[]) => {
    if (jobs.length === 0) return;

    // v5.239.1 — the import results of the files that just finished, in ONE
    // request.  This was a getScans() per finished file, issued from INSIDE
    // the state updater below: a 30-file drop finishing together sent ~25
    // identical-shaped requests in 300 ms, and an updater is not a place for
    // side effects (React may run it more than once).  `requested` makes each
    // file's result fetched once however many poll ticks report it complete.
    const wanted: Array<{ key: string; scanId: number }> = [];
    for (const [key, entry] of Object.entries(uploadProgressRef.current)) {
      if (entry.jobId == null || entry.result || resultRequestedRef.current.has(key)) continue;
      const job = jobs.find((j) => j.id === entry.jobId);
      if (job?.status === 'completed' && job.scan_id != null) {
        wanted.push({ key, scanId: job.scan_id });
        resultRequestedRef.current.add(key);
      }
    }
    if (wanted.length > 0) {
      const ids = Array.from(new Set(wanted.map((w) => w.scanId)));
      getScans(0, ids.length, { ids })
        .then((rows) => {
          const byId = new Map(rows.map((row) => [row.id, row]));
          setUploadProgress((p) => {
            let next = p;
            for (const { key, scanId } of wanted) {
              const row = byId.get(scanId);
              if (row && next[key]) next = { ...next, [key]: { ...next[key], result: row } };
            }
            return next;
          });
        })
        .catch(() => {
          // Let a later poll tick try again rather than leave the rows blank.
          wanted.forEach((w) => resultRequestedRef.current.delete(w.key));
        });
    }

    setUploadProgress((prev) => {
      let changed = false;
      const nextEntries = { ...prev };
      for (const [key, entry] of Object.entries(prev)) {
        if (entry.jobId == null) continue;
        const job = jobs.find((j) => j.id === entry.jobId);
        if (!job) continue;
        if ((job.status === 'queued' || job.status === 'processing') && entry.status !== 'processing') {
          nextEntries[key] = { ...entry, status: 'processing', jobMessage: job.message ?? null };
          changed = true;
        } else if (job.status === 'processing' && entry.jobMessage !== (job.message ?? null)) {
          nextEntries[key] = { ...entry, jobMessage: job.message ?? null };
          changed = true;
        } else if (job.status === 'failed' && entry.status !== 'failed') {
          nextEntries[key] = {
            ...entry,
            status: 'failed',
            error: job.failure_reason || job.error_message || job.last_error || job.message || 'Import failed',
            parseErrorId: job.parse_error_id ?? null,
          };
          changed = true;
        } else if (job.status === 'completed' && entry.status !== 'imported' && entry.status !== 'partial') {
          const gaps = (job.skipped_count ?? 0) > 0 || !!job.partial;
          nextEntries[key] = { ...entry, status: gaps ? 'partial' : 'imported', jobMessage: job.message ?? null };
          changed = true;
        }
      }
      return changed ? nextEntries : prev;
    });
  }, []);

  // v5.248.0 — following the started files is ONE request per tick
  // (`GET /upload/jobs?ids=`), on the visibility-aware poll. It was one GET per
  // job on a fixed 4 s interval: 30 files meant 30 requests a tick, a slow
  // response overlapped the next tick, and a hidden tab kept going.
  const pollJobs = useCallback(async () => {
    const asked = activeJobIds;
    if (asked.length === 0) return;
    const jobs = await getIngestionJobsByIds(asked); // a rejection backs the poll off
    const returned = new Set(jobs.map((j) => j.id));
    const doneIds: number[] = [];
    let anyCompleted = false;
    const next: Record<number, IngestionJob> = {};
    for (const job of jobs) {
      next[job.id] = job;
      if (job.status === 'completed' || job.status === 'failed') {
        doneIds.push(job.id);
        if (job.status === 'completed') anyCompleted = true;
      }
    }
    // A job the server no longer returns (deleted, or not this user's) was a
    // 404 the old per-job poll retried for the life of the page.
    const goneIds = asked.filter((id) => !returned.has(id));
    setActiveJobs((prev) => ({ ...prev, ...next }));
    // v5.222.0 — carry the job's state onto the file's banner entry.
    applyJobsToUploadEntries(jobs);
    if (doneIds.length > 0 || goneIds.length > 0) {
      const stop = new Set([...doneIds, ...goneIds]);
      setActiveJobIds((prev) => prev.filter((id) => !stop.has(id)));
      fetchRecentJobs();
      if (anyCompleted) fetchScans();
    }
  }, [activeJobIds, fetchScans, fetchRecentJobs, applyJobsToUploadEntries]);

  useVisibilityPoll(pollJobs, 4000, activeJobIds.length > 0);
  // The poll waits one interval before its first run; a file just handed over
  // should show its state at once.
  const pollJobsRef = useRef(pollJobs);
  pollJobsRef.current = pollJobs;
  useEffect(() => {
    if (activeJobIds.length > 0) void pollJobsRef.current().catch(() => undefined);
  }, [activeJobIds]);

  // Recent-jobs polling — depend only on the boolean, not the full
  // recentJobs array.  Pre-audit (H20) this effect re-ran on every
  // poll tick (because each tick called setRecentJobs), which cleared
  // and recreated the interval, producing uneven cadence and
  // occasional duplicate intervals.
  const hasActiveRecent = useMemo(
    () => recentJobs.some((j) => j.status === 'queued' || j.status === 'processing'),
    [recentJobs],
  );
  // Visibility-aware and settle-then-wait (v5.248.0) — it was a fixed interval
  // that overlapped a slow response and kept running in a hidden tab.
  useVisibilityPoll(fetchRecentJobs, 5000, hasActiveRecent);

  // v5.207.0 — refresh when ANY scan lands. The job polling above only
  // follows uploads this tab submitted, so scans from an agent or another
  // tab raised counters elsewhere while this list stayed stale. Polls a
  // count + newest-id marker (one indexed query) while the tab is visible.
  const inventoryMarkerRef = useRef<string | null>(null);
  const checkInventoryMarker = useCallback(async () => {
    // Not caught: a rejection is what makes the poll back off during an
    // outage (it used to swallow the error and keep its 15 s cadence).
    const marker = await getScanInventoryMarker();
    const key = `${marker.count}:${marker.latest_id ?? ''}`;
    if (inventoryMarkerRef.current !== null && inventoryMarkerRef.current !== key) {
      fetchScans();
      fetchRecentJobs();
    }
    inventoryMarkerRef.current = key;
  }, [fetchScans, fetchRecentJobs]);
  useVisibilityPoll(checkInventoryMarker, 15_000);
  // Take the baseline at once, so the first change is seen on the first tick.
  useEffect(() => {
    void checkInventoryMarker().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const groupedScans = useMemo(
    () =>
      scans.reduce<Record<string, Scan[]>>((acc, scan) => {
        const key = (scan.tool_name || scan.scan_type || 'Other').toUpperCase();
        if (!acc[key]) acc[key] = [];
        acc[key].push(scan);
        return acc;
      }, {}),
    [scans],
  );

  // v5.226.0 — the tool chips count EVERY file matching the search/date
  // filters, server-side, batched files included.  The loaded `scans` array
  // is the fallback for an older backend; in grouped mode it holds only the
  // unbatched files, which is the defect this replaces.
  const toolChips = useMemo<[string, number][]>(() => {
    const server = inventorySummary?.tool_counts;
    if (server && Object.keys(server).length > 0) {
      return Object.entries(server).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    }
    return Object.entries(groupedScans)
      .map(([g, rows]) => [g, rows.length] as [string, number])
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [inventorySummary, groupedScans]);

  // Rows render in the order the server returned them (`scans` is used
  // directly below — no client-side re-sort).  getScans is called with
  // sortBy/sortOrder and each appended page preserves that order; a
  // client-side re-sort here previously discarded the server order, so the
  // filename / host-count headers and created_at-ascending appeared dead —
  // the arrow flipped but the rows never moved.

  // v4.18.0 — completed jobs are excluded from the Ingestion Queue
  // display.  Successful ingests already appear in Your Scans below as
  // the canonical Scan row; showing them twice was duplicate-feeling
  // and made the queue look perpetually busy.  Keep `queued`,
  // `processing`, `failed` so the queue is useful for its actual
  // intent: "what's still in flight or needs my attention?".
  // v5.271.0 — plus every staged job, which the 25 recent may not reach.
  // v5.289.0 — minus superseded failures (a later job imported the same
  // file): they need nothing, and are listed on Ingestion Results.
  const pendingJobs = useMemo(() => {
    const recent = recentJobs.filter(
      (j) => j.status !== 'completed' && j.status !== 'staged' && j.superseded_by_job_id == null,
    );
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
  const openReview = useCallback((jobs: IngestionJob[]) => {
    if (jobs.length === 0) return;
    setReviewResume({ jobs });
    setUploadDialogOpen(true);
  }, []);
  // v5.239.0 — the queue is a compact strip: its counts are always shown and
  // the table opens on request.  Whether it is open is a per-browser
  // convenience; the page works the same if storage is unavailable.
  const queueCounts = useMemo(() => ({
    processing: pendingJobs.filter((j) => j.status === 'queued' || j.status === 'processing').length,
    staged: pendingJobs.filter((j) => j.status === 'staged').length,
    failed: pendingJobs.filter((j) => j.status === 'failed').length,
  }), [pendingJobs]);
  // v5.289.0 — the queue box said "1 failed" (its 25 most recent jobs) while
  // the lead and Ingestion Results said 4 need attention.  Both now read the
  // project-wide figure (one definition, server-side); the recent-jobs count
  // is only the fallback when the summary did not carry it.
  const needAttention = inventorySummary?.imports_need_attention ?? queueCounts.failed;
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

  const handleViewScan = (scanId: number) => navigate(`/scans/${scanId}`);
  const handleDeleteClick = (scan: Scan) => {
    setScanToDelete(scan);
    setDeletionImpact(null);
    setImpactError(false);
    setImpactLoading(true);
    setDeleteDialogOpen(true);
    // Fetch the real impact so the modal can detail exactly what's removed.
    // Hosts are deduplicated across scans, so this is rarely a blanket wipe.
    getScanDeletionImpact(scan.id)
      .then((impact) => {
        // Ignore a stale response if the user already targeted another scan.
        setScanToDelete((current) => {
          if (current && current.id === impact.scan_id) setDeletionImpact(impact);
          return current;
        });
      })
      .catch(() => setImpactError(true))
      .finally(() => setImpactLoading(false));
  };
  const handleDeleteConfirm = async () => {
    if (!scanToDelete || deleteLoading) return;
    setDeleteLoading(true);
    try {
      await deleteScan(scanToDelete.id);
      setScans((prev) => prev.filter((s) => s.id !== scanToDelete.id));
      toast.success(`Scan "${scanToDelete.filename}" deleted.`);
      setDeleteDialogOpen(false);
      setScanToDelete(null);
      setDeletionImpact(null);
    } catch (err) {
      // Pre-audit shape silently swallowed failures and closed the
      // dialog, leaving the row in the list while the user believed
      // the delete succeeded.  Keep the dialog open on failure so the
      // user can retry; surface a real toast (audit C8).
      toast.error(formatApiError(err, `Failed to delete scan "${scanToDelete.filename}".`));
    } finally {
      setDeleteLoading(false);
    }
  };

  // v2.59.0 — Scan Timeline removed from this page and replaced by the
  // cross-project /tool-activity surface, which plots SOC-correlation
  // markers using the scan's actual start_time (the SOC use case) rather
  // than upload time.  See ActivityTimeline component for the
  // generalised lane-packing + bar-vs-dot rendering.

  // No-op headline slot in the Scan column. Per-scan-type "headline" badges
  // were dropped as duplicative; kept as a no-op so the JSX call sites stay
  // small and a future Scan-column headline has somewhere to land.
  const statusBadge = (_scan: Scan): React.ReactNode => null;

  const commandDetail = (scan: Scan) => {
    const explanation = commandCache[scan.id];
    const hasCommand = !!(scan.command_line && scan.command_line.trim());

    if (!hasCommand) {
      return (
        <div className="rounded-control bg-accent px-md py-sm text-metadata text-muted-foreground">
          No command line data available for this scan.
          {scan.tool_name && !['nmap', 'masscan'].includes((scan.tool_name || '').toLowerCase()) && (
            <> {scan.tool_name} output does not include producing configuration.</>
          )}
        </div>
      );
    }

    return (
      <div className="flex flex-col gap-sm rounded-control bg-accent px-md py-sm">
        <div>
          <p className="mb-xxs text-caption font-semibold text-muted-foreground">Command</p>
          <div className="break-words rounded-control border border-border bg-card px-sm py-xs font-mono text-caption">
            {scan.command_line}
          </div>
        </div>
        {(scan.version || scan.tool_name || scan.uploaded_by) && (
          <div className="flex flex-wrap gap-md text-caption text-muted-foreground">
            {scan.version && (
              <span>
                <strong>Version:</strong> {scan.version}
              </span>
            )}
            {scan.tool_name && (
              <span>
                <strong>Tool:</strong> {scan.tool_name}
              </span>
            )}
            {scan.uploaded_by && (
              <span>
                <strong>Uploaded by:</strong> {scan.uploaded_by_name || scan.uploaded_by}
              </span>
            )}
          </div>
        )}
        {!explanation && (
          <div className="flex items-center gap-xs text-caption text-muted-foreground">
            <Loader2 className="size-3 animate-spin" aria-hidden />
            Loading argument analysis…
          </div>
        )}
        {explanation?.has_command && explanation.arguments && explanation.arguments.length > 0 && (
          <div>
            <p className="mb-xxs text-caption font-semibold text-muted-foreground">
              Arguments ({explanation.arguments.length})
            </p>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/5">Flag</TableHead>
                  <TableHead className="w-1/6">Category</TableHead>
                  <TableHead>Description</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {explanation.arguments.map((arg, idx) => (
                  <TableRow key={idx}>
                    <TableCell className="truncate font-mono">{arg.arg}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{arg.category}</Badge>
                    </TableCell>
                    <TableCell className="truncate">{arg.description}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
        {explanation?.summary && (
          <Alert variant="info">
            <AlertDescription>{explanation.summary}</AlertDescription>
          </Alert>
        )}
      </div>
    );
  };

  // The newest upload among what is loaded (the first page is newest first).
  const lastImportAt = [
    ...history.map((h) => h.at),
    ...scans.map((sc) => sc.created_at),
    ...batches.map((b) => b.last_uploaded),
  ].filter((v): v is string => !!v).sort((x, y) => new Date(x).getTime() - new Date(y).getTime()).pop() ?? null;

  if (loading) {
    return <ListPageSkeleton titleWidth={160} actionCount={2} tableProps={{ rows: 8, columns: 6 }} />;
  }

  return (
    <div className="p-md md:p-lg">
      {confirmDialog}
      <div className="mb-md flex flex-wrap items-start justify-between gap-sm">
        <div>
          <h1 className="text-page-title font-semibold">Scans</h1>
          <p className="text-metadata text-muted-foreground">
            Import tool output, track ingestion, and review scan inventory from one place.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-xs">
          <Button variant="outline" onClick={() => navigate('/scans/compare')}>
            <GitCompareArrows className="size-4" aria-hidden /> Compare scans
          </Button>
          <Button onClick={() => setUploadDialogOpen(true)}>
            <Upload className="size-4" aria-hidden /> Upload scans
          </Button>
        </div>
      </div>

      {/* v5.270.0 — one sentence instead of four stat cards.  "Hosts up" (a
          scanner's own up flag, not liveness) and "Open services" were totals
          nobody acts on, and "Queue active" repeated the queue below.  Counts
          come from the filter-aware /scans/summary (every matching file, not
          the loaded page). */}
      <ScansLead
        files={inventorySummary?.total_files ?? inventorySummary?.total_scans ?? scans.length}
        filtered={hasActiveFilters}
        failed={queueCounts.failed}
        needAttention={inventorySummary?.imports_need_attention}
        notImported={inventorySummary?.imports_not_imported}
        byReason={inventorySummary?.imports_not_imported_by_reason}
        superseded={inventorySummary?.imports_superseded}
        queueUnknown={recentJobsError && recentJobs.length === 0}
        lastImportAt={lastImportAt}
      />

      {/* Files the operator has started, followed to their import result —
          aria-live so screen readers announce progress when the dialog has
          closed (audit C10). */}
      {Object.keys(uploadProgress).length > 0 && (
        <div className="mb-sm flex flex-col gap-xs" aria-live="polite" aria-atomic="false">
          {Object.entries(uploadProgress).map(([key, p]) => {
            const variant =
              p.status === 'failed'
                ? 'destructive'
                : p.status === 'partial'
                ? 'warning'
                : p.status === 'imported'
                ? 'success'
                : 'info';
            const label: Record<typeof p.status, string> = {
              received: 'Upload received, waiting for the worker',
              processing: 'Processing',
              imported: 'Imported',
              partial: 'Imported with gaps',
              failed: 'Import failed',
            };
            const terminal =
              p.status === 'imported' || p.status === 'partial' || p.status === 'failed';
            return (
              <Alert key={key} variant={variant}>
                <AlertDescription className="flex flex-col gap-xxs">
                  <div className="flex items-baseline justify-between gap-sm">
                    <span className="truncate font-semibold">
                      {label[p.status]}: {p.filename}
                    </span>
                    {terminal && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-6 shrink-0"
                        aria-label={`Dismiss ${p.filename} progress`}
                        onClick={() =>
                          setUploadProgress((prev) => {
                            const { [key]: _removed, ...rest } = prev;
                            return rest;
                          })
                        }
                      >
                        <ChevronUp className="size-3" aria-hidden />
                      </Button>
                    )}
                  </div>
                  {p.status === 'failed' ? (
                    <div className="flex flex-wrap items-center gap-xs">
                      <span className="min-w-0 break-words">{p.error || 'Import failed'}</span>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          navigate(
                            p.parseErrorId != null
                              ? `/parse-errors?error_id=${p.parseErrorId}`
                              : p.jobId != null
                                ? `/parse-errors?job_id=${p.jobId}`
                                : '/parse-errors',
                          )
                        }
                      >
                        Why it failed
                      </Button>
                    </div>
                  ) : p.status === 'imported' || p.status === 'partial' ? (
                    p.result ? (
                      <ImportResult scan={p.result} />
                    ) : (
                      <span className="text-caption text-muted-foreground">
                        {p.jobMessage || 'Import complete.'}
                      </span>
                    )
                  ) : (
                    <span className="text-caption text-muted-foreground">
                      {p.status === 'received'
                        ? 'The file is stored; parsing starts when a worker picks it up.'
                        : p.jobMessage || 'Parsing…'}
                    </span>
                  )}
                </AlertDescription>
              </Alert>
            );
          })}
        </div>
      )}

      {/* Active job progress — jobs this tab did not upload (found queued or
          processing on load); a job with a banner entry above is shown there. */}
      {activeJobIds.length > 0 && (
        <div className="mb-sm flex flex-col gap-xs" aria-live="polite" aria-atomic="false">
          {activeJobIds.map((jobId) => {
            const job = activeJobs[jobId];
            if (!job) return null;
            if (Object.values(uploadProgress).some((e) => e.jobId === jobId)) return null;
            return (
              <Alert key={jobId} variant="info">
                <AlertDescription className="break-words">
                  <strong>{job.status === 'processing' ? 'Processing' : 'Queued'}:</strong>{' '}
                  {job.original_filename || `Job #${jobId}`}{' '}
                  <span className="text-muted-foreground">{job.message || 'Waiting…'}</span>
                </AlertDescription>
              </Alert>
            );
          })}
        </div>
      )}

      {/* Ingestion Queue — v4.18.0: filtered to non-completed jobs only.
          Pre-fix, every successful upload showed up here AND in "Your
          Scans" below, which read as duplicate info.  Now the queue
          shows only what's actionable from a queue perspective:
          in-flight (`queued` / `processing`) and recent failures.
          Successful uploads appear exclusively in Your Scans. */}
      {recentJobsError && pendingJobs.length === 0 && (
        <p role="status" className="mb-md text-caption text-muted-foreground">
          The ingestion queue could not be checked — this is not a confirmation that nothing is
          queued or failed.
        </p>
      )}
      {/* v5.270.0 — a left-rule callout that exists only while something is
          in flight, waiting for review or failed; not a boxed card. */}
      {pendingJobs.length > 0 && (
        <div
          data-testid="ingestion-queue"
          className={cn(
            'mb-md border-l-4 py-xs pl-md',
            needAttention > 0 ? 'border-l-destructive' : queueCounts.staged > 0 ? 'border-l-warning' : 'border-l-info',
          )}
        >
          <div>
            <div className="mb-xs flex flex-wrap items-center justify-between gap-xs">
              {/* v5.239.0 — a compact strip: what is in the queue, in counts,
                  with the table one click away.  It was a full table above the
                  import history whenever a single job was in flight. */}
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-xs">
                  <h2 className="text-metadata font-semibold text-foreground">Ingestion queue</h2>
                  <span className="text-metadata text-muted-foreground">
                    {[
                      queueCounts.processing > 0 ? `${queueCounts.processing} processing` : null,
                      queueCounts.staged > 0 ? `${queueCounts.staged} waiting for review` : null,
                    ].filter(Boolean).join(' · ')}
                    {needAttention > 0 && (
                      <>
                        {queueCounts.processing > 0 || queueCounts.staged > 0 ? ' · ' : ''}
                        <Link
                          to="/parse-errors?status=needs_attention"
                          className="text-destructive underline-offset-2 hover:underline"
                          title="Every import of this project that failed or finished partial and nobody dismissed — the figure in the lead and on Ingestion Results"
                        >
                          {needAttention.toLocaleString()} need{needAttention === 1 ? 's' : ''} attention
                        </Link>
                      </>
                    )}
                  </span>
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
                </div>
                {queueOpen && (
                  <p className="text-metadata text-muted-foreground">
                    In-flight uploads, staged files waiting for a format review, and failures among
                    the recent uploads. Successful uploads appear in Import history below;{' '}
                    <Link to="/parse-errors" className="text-primary underline-offset-2 hover:underline">
                      every import is on Ingestion Results
                    </Link>
                    .
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-xs">
                {/* v5.271.0 — files left waiting (a closed review dialog, a
                    failed inspection) go back into the review, all at once.
                    Before, the only visible action here was Discard; the
                    review was one file per dialog behind "Show jobs". */}
                {stagedJobs.length > 0 && (
                  <Button size="sm" onClick={() => openReview(stagedJobs)}>
                    Review {stagedJobs.length} waiting
                  </Button>
                )}
                {/* v5.232.0 — staged files nobody will start (a closed review
                    dialog, a failed inspection) can be cleared in one go. */}
                {stagedJobs.length > 0 && (
                  <Button
                    size="sm"
                    variant="outline"
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
                          + 'They stay listed in Ingestion Results as discarded.',
                        resourceName: `${n} staged upload${n === 1 ? '' : 's'}`,
                        severity: 'warning',
                        confirmLabel: `Discard ${n}`,
                      });
                      if (!ok) return;
                      try {
                        const res = await discardStagedJobs(ids);
                        toast.info(
                          res.discarded === n
                            ? `Discarded ${res.discarded} staged upload${res.discarded === 1 ? '' : 's'}`
                            : `Discarded ${res.discarded} of ${n}; the rest were no longer staged`,
                        );
                        await fetchRecentJobs();
                      } catch (err) {
                        toast.error(formatApiError(err, 'Could not discard the staged uploads'));
                      }
                    }}
                  >
                    Discard {stagedJobs.length} staged
                  </Button>
                )}
                <LastUpdated
                  lastFetched={recentJobsFetched}
                  onRefresh={fetchRecentJobs}
                  isLoading={recentJobsLoading}
                  label="ingestion jobs"
                  intervalMs={15000}
                />
              </div>
            </div>
            {queueOpen && (
            <div id="ingestion-queue-table" className="overflow-x-auto">
              {/* v5.271.0 — fixed layout with an Actions column wide enough
                  for its buttons: at w-24 a staged row's "Review and import"
                  was cut to "Review and ir". */}
              {/* v5.271.1 — Message is the only column without a width, so it
                  gets what the others leave; with Tool and Duration as
                  columns that was ~80px at a normal window ("Pro…", one word
                  per line).  Both are now second lines of File and Submitted:
                  Tool is "-" on nearly every queue row, Duration on every
                  staged or queued one. */}
              <Table className="min-w-[64rem] table-fixed">
                <TableHeader>
                  <TableRow>
                    {/* w-12 (48px) — the expand chevron is a 40px icon
                        button (control density raised in 4.7.13); the
                        old w-10 (40px) column clipped it and let it
                        bleed into the Status column. */}
                    <TableHead className="w-12" />
                    <TableHead className="w-36">Status</TableHead>
                    <TableHead className="w-1/5">File</TableHead>
                    <TableHead className="w-20 text-right">Size</TableHead>
                    <TableHead>Message</TableHead>
                    <TableHead className="w-44">Submitted</TableHead>
                    <TableHead className="w-44 text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pendingJobs.map((job) => {
                    const isFailure = job.status === 'failed';
                    const isExpanded = expandedJobIds.has(job.id);

                    const elapsed = (() => {
                      if (job.started_at && job.completed_at) {
                        return formatDuration(
                          new Date(job.completed_at).getTime() - new Date(job.started_at).getTime(),
                        );
                      }
                      if (job.started_at && job.status === 'processing') {
                        return `${formatDuration(Date.now() - new Date(job.started_at).getTime())}…`;
                      }
                      return '-';
                    })();

                    // v5.271.0 — under 1 KB in bytes: "0 KB" read as an empty file.
                    const fileSize = job.file_size
                      ? job.file_size > 1048576
                        ? `${(job.file_size / 1048576).toFixed(1)} MB`
                        : job.file_size < 1024
                          ? `${job.file_size} B`
                          : `${(job.file_size / 1024).toFixed(0)} KB`
                      : '-';

                    // v5.289.0 — the parser's specific cause first; the generic
                    // "Failed to parse the file …" only when nothing better exists.
                    const displayMessage = isFailure
                      ? job.failure_reason || job.error_message || job.message || 'Unknown error'
                      : job.status === 'staged'
                        ? 'Not imported yet'
                        // Prefer the import-count summary ("6 DNS records") over
                        // the generic "<tool> processed successfully" so the row
                        // actually shows how much was ingested.
                        : job.progress || job.message || '-';

                    // Dead-letter / liveness signals (backend already returns
                    // these). A job that bounced before settling carries a
                    // retry_count; a 'processing' job whose worker heartbeat has
                    // gone stale is wedged, not working.
                    const retried = (job.retry_count ?? 0) > 0;
                    const STALL_MS = 90_000;
                    const isStalled =
                      job.status === 'processing' &&
                      !!job.last_heartbeat &&
                      Date.now() - new Date(job.last_heartbeat).getTime() > STALL_MS;

                    // A job can succeed and still have lost data: rows skipped
                    // as malformed, or a truncated file that stopped the parse
                    // early. Rendered as plain green "completed", that reads as
                    // "everything imported" — and for scan data the hosts that
                    // never parsed look exactly like hosts that were down.
                    const skipped = job.skipped_count ?? 0;
                    const isDegraded =
                      job.status === 'completed' &&
                      (skipped > 0 || !!job.parser_warnings || !!job.partial);
                    const canExpand = isFailure || isDegraded;

                    return (
                      <React.Fragment key={job.id}>
                        <TableRow className={cn(job.status === 'completed' && 'opacity-75')}>
                          <TableCell className="p-xxs">
                            {canExpand && (
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => toggleJobExpanded(job.id)}
                                aria-label={
                                  isExpanded
                                    ? 'Hide import details'
                                    : isFailure
                                      ? 'Show full error message'
                                      : 'Show import warnings'
                                }
                                aria-expanded={isExpanded}
                              >
                                {isExpanded ? (
                                  <ChevronUp className="size-4" aria-hidden />
                                ) : (
                                  <ChevronDown className="size-4" aria-hidden />
                                )}
                              </Button>
                            )}
                          </TableCell>
                          <TableCell>
                            {/* v5.271.0 — icon plus the word.  Icon and colour
                                alone left a staged row as a grey circle
                                nobody could read; the word is plain text,
                                not the badge that once repeated the icon. */}
                            <span className="inline-flex min-w-0 items-center gap-xxs">

                              {job.status === 'completed' && (
                                <CheckCircle2 className="size-4 text-success" aria-hidden />
                              )}
                              {job.status === 'failed' && (
                                <AlertCircle className="size-4 text-destructive" aria-hidden />
                              )}
                              {job.status === 'processing' && (
                                <Loader2 className="size-4 animate-spin text-info" aria-hidden />
                              )}
                              {job.status === 'queued' && (
                                <Hourglass className="size-4 text-muted-foreground" aria-hidden />
                              )}
                              {/* v5.232.0 — stored, not imported: nothing runs
                                  until the operator starts it. */}
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
                            {isDegraded && (
                              <AlertTriangle
                                className="ml-xxs inline size-4 text-warning"
                                aria-label="Imported with warnings — some data may be missing"
                              />
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
                            <div
                              className={cn(
                                isFailure && 'text-destructive',
                                isFailure && !isExpanded && 'truncate',
                              )}
                            >
                              {displayMessage}
                            </div>
                            {retried && (
                              <Badge
                                variant="outline"
                                className="mt-xxs border-warning/40 text-warning"
                                title={job.last_error ? `Most recent failure: ${job.last_error}` : undefined}
                              >
                                retried {job.retry_count}×
                              </Badge>
                            )}
                            {isDegraded && (
                              <div className="mt-xxs flex flex-col gap-xxs">
                                <Badge
                                  variant="outline"
                                  className="w-fit border-warning/40 text-warning"
                                  title={job.parser_warnings || undefined}
                                >
                                  {job.partial
                                    ? `partial import${skipped > 0 ? ` · ${skipped} skipped` : ''}`
                                    : skipped > 0
                                      ? `${skipped} record${skipped === 1 ? '' : 's'} skipped`
                                      : 'imported with warnings'}
                                </Badge>
                                {job.parser_warnings && (
                                  <p
                                    className={cn(
                                      'text-caption text-warning',
                                      !isExpanded && 'truncate',
                                      isExpanded && 'whitespace-pre-wrap break-words',
                                    )}
                                  >
                                    {job.parser_warnings}
                                  </p>
                                )}
                              </div>
                            )}
                            {job.parse_error_id && (
                              <button
                                type="button"
                                onClick={() => navigate(`/parse-errors?error_id=${job.parse_error_id}`)}
                                className="mt-xxs rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                              >
                                <Badge variant="outline" className="cursor-pointer border-destructive/40 text-destructive hover:bg-destructive/10">
                                  Error #{job.parse_error_id}
                                </Badge>
                              </button>
                            )}
                          </TableCell>
                          <TableCell className="text-caption">
                            <p>{new Date(job.created_at).toLocaleString()}</p>
                            {elapsed !== '-' && (
                              <p className="font-mono text-muted-foreground" title="How long the parse ran">ran {elapsed}</p>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            {job.scan_id && (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => navigate(`/scans/${job.scan_id}`)}
                                aria-label={`Open scan for ${job.original_filename}`}
                              >
                                Open
                              </Button>
                            )}
                            {/* v2.86.2 — failed jobs were stuck in the
                                queue with no acknowledge path; this
                                dismiss button writes dismissed_at so
                                the row drops out on the next refetch.
                                Preserves the failure for the audit
                                trail (admins can re-surface dismissed
                                rows via ?include_dismissed=true). */}
                            {/* v5.232.0 — a staged job (left behind by a closed
                                review dialog, or by a failed inspection) had no
                                action at all here: import it after a format
                                review, or discard it. */}
                            {job.status === 'staged' && (
                              <div className="flex flex-wrap justify-end gap-xxs">
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => openReview([job])}
                                  aria-label={`Review format and import ${job.original_filename}`}
                                >
                                  Review
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  className="text-destructive"
                                  onClick={async () => {
                                    try {
                                      await discardIngestionJob(job.id);
                                      toast.info('Staged upload discarded');
                                      await fetchRecentJobs();
                                    } catch (err) {
                                      toast.error(formatApiError(err, 'Could not discard the staged upload'));
                                    }
                                  }}
                                  aria-label={`Discard staged upload ${job.original_filename}`}
                                >
                                  Discard
                                </Button>
                              </div>
                            )}
                            {(job.status === 'queued' || job.status === 'processing') && (
                              <Button
                                size="sm"
                                variant="ghost"
                                className="text-destructive"
                                onClick={async () => {
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
                                  try {
                                    await cancelIngestionJob(job.id);
                                    toast.info('Ingestion cancelled');
                                    await fetchRecentJobs();
                                  } catch (err) {
                                    toast.error(formatApiError(err, 'Could not cancel ingestion'));
                                  }
                                }}
                                aria-label={`Cancel ingestion for ${job.original_filename}`}
                              >
                                Cancel
                              </Button>
                            )}
                            {isFailure && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={async () => {
                                  try {
                                    await retryIngestionJob(job.id);
                                    toast.info('Re-queued for parsing');
                                    await fetchRecentJobs();
                                  } catch (err) {
                                    // 409 when the upload was already cleaned
                                    // up — the message tells the user to re-upload.
                                    toast.error(formatApiError(err, 'Could not retry ingestion'));
                                  }
                                }}
                                aria-label={`Retry failed ingestion for ${job.original_filename}`}
                              >
                                Retry
                              </Button>
                            )}
                            {isFailure && (
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={async () => {
                                  try {
                                    await dismissIngestionJob(job.id);
                                    await fetchRecentJobs();
                                  } catch (err) {
                                    console.error('Failed to dismiss ingestion job', err);
                                  }
                                }}
                                aria-label={`Dismiss failed ingestion for ${job.original_filename}`}
                              >
                                Dismiss
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                        {isFailure && isExpanded && (
                          <TableRow>
                            <TableCell colSpan={7} className="bg-accent p-md">
                              <p className="mb-xxs text-metadata font-semibold text-destructive">
                                Full error message
                              </p>
                              <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-card p-sm text-caption">
                                {displayMessage}
                              </pre>
                              {job.last_error && job.last_error !== displayMessage && (
                                <>
                                  <p className="mb-xxs mt-sm text-metadata font-semibold text-destructive">
                                    Most recent failure
                                    {retried
                                      ? ` (after ${job.retry_count} ${job.retry_count === 1 ? 'retry' : 'retries'})`
                                      : ''}
                                  </p>
                                  <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-card p-sm text-caption">
                                    {job.last_error}
                                  </pre>
                                </>
                              )}
                            </TableCell>
                          </TableRow>
                        )}
                      </React.Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            )}
          </div>
        </div>
      )}

      <PostureSection title="Import history">

      {/* Scan Timeline moved to /tool-activity in v2.59.0 — that page
          plots scans by their actual scan_start (SOC-correlation
          intent) across ALL projects, and adds recon + execution
          sessions to the same axis.  Per-project scan inventory still
          lives here in tabular form below. */}

      {historyError && (
        <Alert variant="destructive" className="mb-sm" data-testid="history-error">
          <AlertDescription className="flex flex-wrap items-center gap-sm">
            <span className="min-w-0 flex-1 break-words">
              {historyError}
              {(scans.length > 0 || batches.length > 0) && ' The rows below are from the last successful load.'}
            </span>
            <Button size="sm" variant="outline" onClick={() => void fetchScans()}>Retry</Button>
          </AlertDescription>
        </Alert>
      )}
      {historyError && scans.length === 0 && batches.length === 0 ? null : !hasActiveFilters && scans.length === 0 && batches.length === 0 ? (
        <div className="py-xl text-center">
          <Upload className="mx-auto mb-sm size-16 text-muted-foreground" aria-hidden />
          <p className="text-subheading text-muted-foreground">No scans uploaded yet</p>
          <p className="text-metadata text-muted-foreground">
            Use Upload scans to import Nmap, Nessus, Masscan, OpenVAS, httpx, dnsx, BloodHound,
            EyeWitness, NetExec, and other supported scanner exports — expand "Supported
            formats" below the upload area for the full list and per-tool notes.
          </p>
        </div>
      ) : (
        <div>
          {/* v5.270.0 — the filters are ONE row: search, tool, range, the
              Grouped / All files switch, and how much of the list is loaded.
              (Two rows of upper-case count chips and a paragraph before.) */}
          {/* v5.294.0 — the shared ListFilterBar every list page uses. */}
          <ListFilterBar
            summary={(scans.length > 0 || batches.length > 0) ? (
              <>
                {/* v2.86.2 — how much of the list is loaded, so a partial view is
                    never mistaken for the whole one. */}
                {showBatchFiles
                  ? `${scans.length.toLocaleString()}${inventorySummary?.total_files != null ? ` of ${inventorySummary.total_files.toLocaleString()}` : ''} file${scans.length === 1 ? '' : 's'}`
                  : `${historyRows.length.toLocaleString()}${historyTotal != null && historyTotal > historyRows.length ? ` of ${historyTotal.toLocaleString()}` : ''} upload${historyRows.length === 1 ? '' : 's'} · `
                    + `${batches.length} batch${batches.length === 1 ? '' : 'es'}, `
                    + `${scans.length} single file${scans.length === 1 ? '' : 's'}`}
                {hasMoreScans ? ' · more below' : hasActiveFilters ? ' (filtered)' : ''}
              </>
            ) : undefined}
          >
            <ListFilterSearch
              value={searchText}
              onChange={setSearchText}
              placeholder="Search filename, tool, scan type…"
              label="Search scan inventory"
            />
            <Select value={toolFilter || '__all'} onValueChange={(v) => setToolFilter(v === '__all' ? '' : v)}>
              <SelectTrigger className="h-8 w-44 text-metadata" aria-label="Filter scans by tool">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all">
                  All tools ({(inventorySummary?.total_files ?? scans.length).toLocaleString()})
                </SelectItem>
                {toolChips.map(([group, count]) => (
                  <SelectItem key={group} value={group}>
                    {group.toLowerCase()} ({count.toLocaleString()})
                  </SelectItem>
                ))}
                {/* A tool picked from a row badge that the counts do not list. */}
                {toolFilter && !toolChips.some(([g]) => g.toLowerCase() === toolFilter.toLowerCase()) && (
                  <SelectItem value={toolFilter}>{toolFilter.toLowerCase()}</SelectItem>
                )}
              </SelectContent>
            </Select>
            <Select
              value={dateRangeDays == null ? 'all' : String(dateRangeDays)}
              onValueChange={(v) => setDateRangeDays(v === 'all' ? null : parseInt(v, 10))}
            >
              <SelectTrigger className="h-8 w-36 text-metadata" aria-label="Filter scans by upload date">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DATE_RANGE_PRESETS.map((preset) => (
                  <SelectItem key={preset.label} value={preset.days == null ? 'all' : String(preset.days)}>
                    {preset.days == null ? 'Any time' : `Last ${preset.days} days`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {/* Who uploaded it — offered once there is more than one uploader
                (or a link arrived with one chosen). The counts follow the
                other filters, never this one, so everyone stays listed. */}
            {((inventorySummary?.uploaders?.length ?? 0) > 1 || uploaderFilter != null) && (
              <Select
                value={uploaderFilter == null ? 'anyone' : String(uploaderFilter)}
                onValueChange={(v) => setUploaderFilter(v === 'anyone' ? null : parseInt(v, 10))}
              >
                <SelectTrigger className="h-8 w-44 min-w-0 text-metadata" aria-label="Filter scans by uploader">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="anyone">Uploaded by anyone</SelectItem>
                  {(inventorySummary?.uploaders ?? []).map((u) => (
                    // The full name is shown (v5.287.0); the id stays the value.
                    <SelectItem key={u.user_id} value={String(u.user_id)}>
                      <span
                        className="block max-w-56 truncate"
                        title={u.full_name ? `${u.full_name} (${u.username})` : u.username}
                      >
                        {u.full_name || u.username} ({u.files.toLocaleString()})
                      </span>
                    </SelectItem>
                  ))}
                  {/* Chosen, but none of their files match the other filters. */}
                  {uploaderFilter != null
                    && !(inventorySummary?.uploaders ?? []).some((u) => u.user_id === uploaderFilter) && (
                    <SelectItem value={String(uploaderFilter)}>User #{uploaderFilter} (0)</SelectItem>
                  )}
                </SelectContent>
              </Select>
            )}
            {/* v5.239.0 — one history, two ways to read it. */}
            <div
              className="inline-flex overflow-hidden rounded-control border border-border"
              role="group"
              aria-label="How the import history is listed"
            >
              {([
                { value: false, label: 'Grouped by upload' },
                { value: true, label: 'All files' },
              ] as const).map((opt) => (
                <button
                  key={opt.label}
                  type="button"
                  aria-pressed={showBatchFiles === opt.value}
                  onClick={() => setShowBatchFiles(opt.value)}
                  className={cn(
                    'px-sm py-xxs text-metadata transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    showBatchFiles === opt.value ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </ListFilterBar>
          <p className="mb-xxs text-caption text-muted-foreground">
            {showBatchFiles
              ? 'Every imported file, batched or not. Click a column heading to sort.'
              : 'Newest upload first. Files dropped together are one batch row; expand it for its files.'}
          </p>
          {historyPartial && !showBatchFiles && (
            <p role="alert" className="mb-xxs text-caption text-warning">
              Some rows of this history could not be loaded, so the list below is missing entries.
              Refresh to try again.
            </p>
          )}
          {scans.length > 0 && <ViewerZoneNote className="mb-xs" />}
          {(showBatchFiles ? scans.length === 0 : historyRows.length === 0) ? (
            // Filter-aware empty state — section header + filters
            // remain visible so the user can clear or refine without
            // navigating away.
            <div className="flex flex-col items-start gap-xs border-l-4 border-border py-xs pl-md">
                <p className="flex items-center gap-xs text-subheading font-semibold">
                  <AlertCircle className="size-4 text-muted-foreground" aria-hidden /> No scans match these filters
                </p>
                <p className="max-w-md text-metadata text-muted-foreground">
                  Adjust the search, tool, date range or uploader above — or clear them to see every scan
                  in this project.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSearchText('');
                    setToolFilter('');
                    setDateRangeDays(null);
                    setUploaderFilter(null);
                  }}
                >
                  Clear filters
                </Button>
            </div>
          ) : (
          // v5.270.0 — the table sits in the section, not in a card.
          <div className="overflow-x-auto">
              <Table style={{ tableLayout: 'fixed' }}>
                <TableHeader>
                  <TableRow>
                    {/* Grouped by upload is chronological by definition (the
                        server orders batches and single files together), so
                        the headers sort only in the all-files view. */}
                    {/* v5.287.0 — When widened (its time and "run time
                        unknown" were cut off) and Actions narrowed; batch rows
                        fill these same five columns. */}
                    {historyHeader('filename', 'Scan', 'w-[24%]')}
                    {/* v5.270.0 — ONE time column: when the scan ran, per its
                        own output, else when it was uploaded; the other time
                        and the provenance are on hover.  In the all-files view
                        it sorts by either. */}
                    {showBatchFiles ? (
                      <TableHead className="w-[18%]">
                        <span className="inline-flex flex-wrap items-center gap-x-xs">
                          When
                          {(['start_time', 'created_at'] as const).map((col) => {
                            const label = col === 'start_time' ? 'Ran' : 'Uploaded';
                            const sorted = sortBy === col;
                            return (
                              <button
                                key={col}
                                type="button"
                                onClick={() => handleSort(col)}
                                aria-label={`Sort by ${label}, currently ${
                                  sorted ? (sortOrder === 'asc' ? 'sorted ascending' : 'sorted descending') : 'not sorted'
                                }`}
                                className={cn(
                                  'inline-flex items-center gap-xxs rounded-control text-caption normal-case focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                                  sorted ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
                                )}
                              >
                                {label}
                                {sorted && (sortOrder === 'asc'
                                  ? <ArrowUp className="size-3" aria-hidden />
                                  : <ArrowDown className="size-3" aria-hidden />)}
                              </button>
                            );
                          })}
                        </span>
                      </TableHead>
                    ) : (
                      <TableHead className="w-[18%]">When</TableHead>
                    )}
                    {historyHeader('new_hosts', 'New hosts', 'w-[10%]')}
                    <TableHead
                      className="w-[39%]"
                      title="What this scan added or observed, counted from the rows it wrote. Hover a line for what it counts."
                    >
                      What it contributed
                    </TableHead>
                    <TableHead className="w-[9%]"><span className="sr-only">Actions</span></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tableRows.map((row) => {
                    if (row.kind === 'batch') {
                      return (
                        <ScanBatchRow
                          key={row.key}
                          batch={row.batch}
                          stagedJobs={stagedByBatch.get(row.batch.id)}
                          onReviewStaged={openReview}
                          filters={listFilters}
                          onViewScan={handleViewScan}
                          colSpan={5}
                        />
                      );
                    }
                    const scan = row.scan;
                    const isExpanded = expandedScanIds.includes(scan.id);
                    const hasCommand = !!(scan.command_line && scan.command_line.trim());
                    return (
                      <React.Fragment key={scan.id}>
                        <TableRow className="align-top">
                          <TableCell>
                            <div className="mb-xxs flex items-center gap-xxs">
                              {hasCommand && (
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon"
                                      className="-ml-xxs"
                                      onClick={() => toggleScanExpanded(scan.id)}
                                      aria-label={isExpanded ? 'Hide command' : 'Show command'}
                                      aria-expanded={isExpanded}
                                    >
                                      {isExpanded ? (
                                        <ChevronUp className="size-4" aria-hidden />
                                      ) : (
                                        <ChevronDown className="size-4" aria-hidden />
                                      )}
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>
                                    {isExpanded ? 'Hide command' : 'Show command'}
                                  </TooltipContent>
                                </Tooltip>
                              )}
                              {/* min-w-0 lets the filename shrink + wrap
                                  inside the flex row; without it the
                                  span keeps its content width and a long
                                  name overflows into the next column. */}
                              <Link
                                to={`/scans/${scan.id}`}
                                className="min-w-0 rounded font-semibold text-foreground hover:text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                              >
                                <BreakableName name={scan.filename} />
                              </Link>
                            </div>
                            <div className="flex flex-wrap items-center gap-xxs">
                              {renderInlineToolBadge(scan)}
                              {scan.version && <Badge variant="outline">v{scan.version}</Badge>}
                              {statusBadge(scan)}
                              {/* The host-query DSL's `scan:` predicate takes
                                  the numeric id, which was previously not
                                  shown anywhere — operators know the upload by
                                  its filename. This is where the two meet. */}
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <span className="shrink-0 cursor-default font-mono text-caption text-muted-foreground">
                                    #{scan.id}
                                  </span>
                                </TooltipTrigger>
                                <TooltipContent>
                                  Scan id — filter hosts with <code>scan:{scan.id}</code>
                                </TooltipContent>
                              </Tooltip>
                            </div>
                          </TableCell>
                          <TableCell className="min-w-0">
                            <ScanWhenCell scan={scan} />
                          </TableCell>
                          {/* What the scan INTRODUCED, out of what it saw:
                              hosts first discovered here, over every host it
                              observed (new + already known). */}
                          <TableCell title="Hosts this scan added to the inventory, out of all the hosts it observed">
                            {scan.total_hosts === 0 ? (
                              <span className="text-caption text-muted-foreground">No hosts</span>
                            ) : (
                              <>
                                {scan.new_hosts > 0 ? (
                                  <span className="tabular-nums font-semibold text-success">+{scan.new_hosts.toLocaleString()}</span>
                                ) : (
                                  <span className="tabular-nums text-muted-foreground">0</span>
                                )}
                                <p className="mt-xxs text-caption tabular-nums text-muted-foreground">
                                  of {scan.total_hosts.toLocaleString()} seen
                                </p>
                              </>
                            )}
                          </TableCell>
                          {/* v5.205.0 — per-kind contribution (ports, findings,
                              web interfaces, names, auth), ordered by what the
                              tool is for. Replaces "Existing hosts" (now the
                              "of N seen" line) and "Findings / ports". */}
                          <TableCell className="min-w-0">
                            <ScanContribution scan={scan} />
                          </TableCell>
                          <TableCell>
                            {/* v5.270.0 — the filename opens the scan; "Hosts" is
                                a quiet link; delete lives in the row menu, out of
                                reach of a stray click. */}
                            <ScanRowActions
                              link={scan.total_hosts > 0 ? (
                                <Link
                                  to={`/hosts?scan_ids=${scan.id}`}
                                  className={ROW_LINK_CLASS}
                                  title="Open the Hosts page filtered to this scan"
                                >
                                  Hosts
                                </Link>
                              ) : null}
                              menu={
                              <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    className="size-7 text-muted-foreground"
                                    aria-label={`More actions for ${scan.filename || `scan ${scan.id}`}`}
                                  >
                                    <MoreHorizontal className="size-4" aria-hidden />
                                  </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                  <DropdownMenuItem onSelect={() => handleViewScan(scan.id)}>
                                    Open scan
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    onSelect={() => handleDeleteClick(scan)}
                                    className="text-destructive focus:text-destructive"
                                  >
                                    <Trash2 className="size-3.5" aria-hidden /> Delete scan…
                                  </DropdownMenuItem>
                                </DropdownMenuContent>
                              </DropdownMenu>
                              }
                            />
                          </TableCell>
                        </TableRow>
                        {hasCommand && isExpanded && (
                          <TableRow>
                            <TableCell colSpan={5} className="py-sm">
                              {commandDetail(scan)}
                            </TableCell>
                          </TableRow>
                        )}
                      </React.Fragment>
                    );
                  })}
                </TableBody>
              </Table>
          </div>
          )}

          {hasMoreScans && (
            <div className="mt-md flex justify-center">
              <Button
                variant="outline"
                onClick={loadMoreScans}
                disabled={loadingMore}
              >
                {loadingMore ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <ChevronDown className="size-4" aria-hidden />
                )}
                {loadingMore ? 'Loading…' : showBatchFiles ? `Load ${SCAN_LIMIT} more` : `Load ${HISTORY_PAGE} more uploads`}
              </Button>
            </div>
          )}
        </div>
      )}
      </PostureSection>

      {/* Upload dialog — v5.229.0: choose → review formats → import → results
          (staged-import plan, phase C). The dialog stages and inspects each
          file; a started file is handed to the banner above by job id.
          v5.271.0 — also where staged files come back to (`resume`). */}
      <UploadReviewDialog
        open={uploadDialogOpen}
        onOpenChange={(next) => {
          setUploadDialogOpen(next);
          if (!next) {
            // Cleared on close: left set, the next "Upload scans" would bring
            // back files discarded in this review.
            setReviewResume(null);
            void fetchRecentJobs();
          }
        }}
        resume={reviewResume}
        projectName={currentProject?.name}
        skipInformational={skipInformational}
        onViewScan={handleViewScan}
        onStarted={(started) => {
          setUploadProgress((prev) => ({
            ...prev,
            [started.key]: {
              filename: started.filename,
              status: 'received',
              startedAt: started.startedAt,
              jobId: started.jobId,
              batchId: started.batchId,
            },
          }));
          setActiveJobIds((prev) => (prev.includes(started.jobId) ? prev : [...prev, started.jobId]));
        }}
      />

      {/* Delete confirmation */}
      <Dialog
        open={deleteDialogOpen}
        onOpenChange={(next) => !deleteLoading && setDeleteDialogOpen(next)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Scan</DialogTitle>
          </DialogHeader>
          <p className="text-metadata">
            Delete the scan{' '}
            <span className="font-medium break-words">
              &quot;{scanToDelete?.filename}&quot;
            </span>
            ? This action cannot be undone.
          </p>

          {/* What gets removed. Hosts are deduplicated across scans, so this
              is usually NOT a blanket wipe — only hosts seen by no other scan
              are deleted; shared hosts are kept. The modal tells the truth via
              a backend-computed impact preview. */}
          {impactLoading ? (
            <div className="mt-3 flex items-center gap-2 text-metadata text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              Calculating exactly what will be removed…
            </div>
          ) : impactError ? (
            <div className="mt-3 flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-metadata text-muted-foreground">
              <AlertCircle className="size-4 mt-0.5 shrink-0" aria-hidden />
              <span>
                Couldn&apos;t load the removal summary. The scan and any hosts
                seen <em>only</em> by it will still be removed if you continue.
              </span>
            </div>
          ) : deletionImpact ? (
            <div className="mt-3 rounded-md border border-border p-3">
              <div className="text-metadata font-medium mb-2">
                This will permanently remove:
              </div>
              <ul className="space-y-1.5 text-metadata">
                <li className="flex items-center gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                  This scan record and its scan history
                </li>
                <li className="flex items-baseline gap-2">
                  <Trash2 className="size-3.5 shrink-0 text-destructive self-center" aria-hidden />
                  <span>
                    <span className="font-medium">{deletionImpact.hosts_removed.toLocaleString()}</span>{' '}
                    {deletionImpact.hosts_removed === 1 ? 'host' : 'hosts'} seen only by this scan
                    {deletionImpact.sample_removed_ips.length > 0 && (
                      <span className="block text-muted-foreground break-words">
                        {deletionImpact.sample_removed_ips.join(', ')}
                        {deletionImpact.hosts_removed > deletionImpact.sample_removed_ips.length &&
                          `, +${(
                            deletionImpact.hosts_removed - deletionImpact.sample_removed_ips.length
                          ).toLocaleString()} more`}
                      </span>
                    )}
                  </span>
                </li>
                {deletionImpact.ports_removed > 0 && (
                  <li className="flex items-center gap-2">
                    <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                    <span>
                      <span className="font-medium">{deletionImpact.ports_removed.toLocaleString()}</span>{' '}
                      open {deletionImpact.ports_removed === 1 ? 'port' : 'ports'} on those hosts
                    </span>
                  </li>
                )}
                {/* v5.204.0 — findings first recorded by this scan are KEPT
                    (they belong to the host); only their "first seen by"
                    pointer goes. Findings on removed hosts go with the host. */}
                {deletionImpact.vulnerabilities_detached > 0 && (
                  <li className="flex items-center gap-2">
                    <Info className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                    <span>
                      <span className="font-medium">
                        {deletionImpact.vulnerabilities_detached.toLocaleString()}
                      </span>{' '}
                      {deletionImpact.vulnerabilities_detached === 1
                        ? 'vulnerability'
                        : 'vulnerabilities'}{' '}
                      first recorded by this scan are kept on their hosts, but lose that attribution
                    </span>
                  </li>
                )}
                {deletionImpact.web_interfaces_removed > 0 && (
                  <li className="flex items-center gap-2">
                    <Trash2 className="size-3.5 shrink-0 text-destructive" aria-hidden />
                    <span>
                      <span className="font-medium">
                        {deletionImpact.web_interfaces_removed.toLocaleString()}
                      </span>{' '}
                      web {deletionImpact.web_interfaces_removed === 1 ? 'interface' : 'interfaces'}{' '}
                      from this scan
                    </span>
                  </li>
                )}
              </ul>
              {deletionImpact.hosts_kept > 0 && (
                <div className="mt-2.5 flex items-start gap-2 border-t border-border pt-2.5 text-metadata text-muted-foreground">
                  <CheckCircle2 className="size-3.5 mt-0.5 shrink-0 text-emerald-600" aria-hidden />
                  <span>
                    <span className="font-medium text-foreground">
                      {deletionImpact.hosts_kept.toLocaleString()}
                    </span>{' '}
                    {deletionImpact.hosts_kept === 1 ? 'host is' : 'hosts are'} also in other scans
                    and will be kept — their data is preserved.
                  </span>
                </div>
              )}
            </div>
          ) : null}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteDialogOpen(false)}
              disabled={deleteLoading}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={deleteLoading}
            >
              {deleteLoading ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                  Deleting…
                </>
              ) : (
                'Delete'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * The page's lead (v5.270.0): how much has been imported, whether anything
 * needs attention, and when the last file arrived.  "Nothing failed" is said
 * only when the queue was actually read AND no job of the project failed.
 *
 * v5.287.0 — the failure figures come from the summary, over the whole
 * project (`imports_need_attention`: failed or partial, not dismissed — the
 * count Ingestion Results' needs-attention view lists; `imports_not_imported`:
 * failures already dismissed, discards and expiries included).  The lead used
 * to count only the 25 most recent jobs, so it said "nothing failed" beside a
 * batch whose 31 files had all expired.  `failed` (the recent queue) is the
 * fallback when the summary did not carry the figures.
 *
 * v5.288.0 — short and specific: "40 files imported · 31 never imported (31
 * expired before review) · last import 55 minutes ago".  The never-imported
 * figure names the ACTUAL reasons with their counts (`byReason`, from the
 * summary) instead of listing every possible one; the explanation moved from
 * a two-line paragraph into an info tip.
 */
/** v5.289.0 — [key, singular, plural]: "(25 discarded, 1 dismissed failure)"
 *  read "(25 discarded, 1 failed, dismissed)". */
const NOT_IMPORTED_REASON: Array<[string, string, string]> = [
  ['expired', 'expired before review', 'expired before review'],
  ['discarded', 'discarded', 'discarded'],
  ['dismissed', 'dismissed failure', 'dismissed failures'],
];

/** Where each reason is listed on Ingestion Results (v2.408.0 split expired
 *  and discarded uploads out of its Failed view). */
const NOT_IMPORTED_VIEW: Record<string, string> = { expired: 'expired', discarded: 'discarded', dismissed: 'failed' };

const notImportedReasons = (byReason?: Record<string, number>): Array<{ key: string; text: string }> =>
  NOT_IMPORTED_REASON
    .filter(([key]) => (byReason?.[key] ?? 0) > 0)
    .map(([key, one, many]) => {
      const n = byReason![key];
      return { key, text: `${n.toLocaleString()} ${n === 1 ? one : many}` };
    });

const ScansLead: React.FC<{
  files: number;
  filtered: boolean;
  failed: number;
  needAttention?: number;
  notImported?: number;
  byReason?: Record<string, number>;
  superseded?: number;
  queueUnknown: boolean;
  lastImportAt: string | null;
}> = ({ files, filtered, failed, needAttention, notImported = 0, byReason, superseded = 0, queueUnknown, lastImportAt }) => {
  if (files === 0 && !filtered) return null;
  const last = lastImportAt ? formatRelativeTime(lastImportAt, { style: 'long' }) : null;
  const projectWide = needAttention != null;
  const attention = projectWide ? needAttention : failed;
  const reasons = notImportedReasons(byReason);
  const sep = <span className="text-muted-foreground"> · </span>;
  return (
    <PostureLead className="mb-md" tone={attention > 0 ? 'warning' : 'neutral'}>
      <span className="tabular-nums">
        {files.toLocaleString()} file{files === 1 ? '' : 's'} imported{filtered ? ' (matching these filters)' : ''}
      </span>
      {!projectWide && queueUnknown ? (
        <>{sep}the ingestion queue could not be checked</>
      ) : (
        <>
          {attention > 0 && (
            <>
              {sep}
              <Link to="/parse-errors?status=needs_attention" className="text-warning underline-offset-2 hover:underline">
                {attention.toLocaleString()} {projectWide ? 'failed or partial' : 'failed'} import{attention === 1 ? '' : 's'}{' '}
                need{attention === 1 ? 's' : ''} attention
              </Link>
            </>
          )}
          {notImported > 0 && (
            <>
              {sep}
              {/* One reason: its own Ingestion Results view; several: each
                  reason links to its view, the total to every upload. */}
              <Link
                to={reasons.length === 1 ? `/parse-errors?status=${NOT_IMPORTED_VIEW[reasons[0].key]}` : '/parse-errors'}
                className="underline-offset-2 hover:underline"
              >
                {notImported.toLocaleString()} never imported
              </Link>
              {reasons.length === 1 && ` (${reasons[0].text})`}
              {reasons.length > 1 && (
                <>
                  {' ('}
                  {reasons.map((r, i) => (
                    <React.Fragment key={r.key}>
                      {i > 0 && ', '}
                      <Link to={`/parse-errors?status=${NOT_IMPORTED_VIEW[r.key]}`} className="underline-offset-2 hover:underline">
                        {r.text}
                      </Link>
                    </React.Fragment>
                  ))}
                  {')'}
                </>
              )}
            </>
          )}
          {/* v5.289.0 — failures whose file a later upload imported: not
              "need attention", but still listed until dismissed. */}
          {projectWide && superseded > 0 && (
            <>
              {sep}
              <Link to="/parse-errors?status=superseded" className="underline-offset-2 hover:underline">
                {superseded.toLocaleString()} failed import{superseded === 1 ? ' was' : 's were'} re-imported later
              </Link>
            </>
          )}
          {attention === 0 && notImported === 0 && superseded === 0 && <>{sep}nothing failed</>}
        </>
      )}
      {last && <>{sep}last import {last}</>}{' '}
      <InfoTip
        label="About these figures"
        className="align-middle"
        text="Every imported file counts, batched files included. “Need attention” is every import of this project that failed or finished partial and nobody dismissed — Ingestion Results’ needs-attention list — except those whose same file a later upload imported (“re-imported later”, superseded). “Never imported” are failures already dismissed: files discarded at the format review, staged files nobody started within 24 hours (expired), and acknowledged failures. A file refused at upload as a duplicate never becomes an import and is not counted."
      />
    </PostureLead>
  );
};
