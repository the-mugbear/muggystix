import React, { useRef, useState, useCallback, useMemo } from 'react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AlertCircle, ChevronDown, GitCompareArrows, Loader2, Upload } from 'lucide-react';
import { useListQuery } from '../hooks/useListQuery';
import { useUrlSearchDraft } from '../hooks/useUrlSearchDraft';
import { invalidateReads } from '../lib/query';
import {
  getScans,
  getScansSummary,
  getScanBatches,
  getImportHistory,
  getIngestionResults,
} from '../services/api';
import type {
  Scan,
  IngestionJob,
  IngestionResultItem,
  ScanBatchSummary,
  ImportHistoryEntry,
  ImportHistoryPage,
  ImportHistoryStatus,
  ScanInventorySummary,
} from '../services/api';
import { TableSkeleton } from '../components/PageSkeleton';
import { useToast } from '../contexts/ToastContext';
import { useProject } from '../contexts/ProjectContext';
import { useProjectId } from '../hooks/useProjectId';
import { useProjectRole } from '../hooks/useProjectRole';
import { formatApiError } from '../utils/apiErrors';
import { useScanInventoryMarker } from '../components/scans/useScanInventoryMarker';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import PostureLead from '../components/posture/PostureLead';
import PostureSection from '../components/posture/PostureSection';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { formatDate, formatRelativeTime, formatTimestamp } from '../utils/relativeTime';
import UploadStatusFilter, { IMPORT_HISTORY_STATUSES } from '../components/scans/UploadStatusFilter';
import UploadReviewDialog from '../components/scans/UploadReviewDialog';
import DeleteScanDialog from '../components/scans/DeleteScanDialog';
import { IngestionQueue, QUEUE_READS, useIngestionQueue } from '../components/scans/IngestionQueue';
import { UploadBanner, useUploadBanner } from '../components/scans/UploadBanner';
import ImportHistoryTable, {
  SCANS_SORT_COLUMNS, type ScansSortBy, type ScansSortOrder,
} from '../components/scans/ImportHistoryTable';
import { hydrateHistoryRows, orderHistoryRows } from '../utils/importHistory';
import { importResultsPath } from '../utils/importResultsPath';
import type { ScanTimeColumn } from '../components/scans/ScanTimeCells';
import { ListFilterBar, ListFilterSearch } from '../components/ListFilterBar';
import { InfoTip } from '../components/ui/info-tip';
import { cn } from '../utils/cn';

/** One page of the import history as the page lists it.  Grouped by upload it
 *  is the server's ORDER (`entries`) with the rows that order names, fetched
 *  by id; as all files it is the files themselves, already in order. */
interface HistoryPage {
  flat: boolean;
  /** What the page is counted by — where the next one starts: the files
   *  themselves as all files, the order's entries grouped by upload. */
  items: Array<ImportHistoryEntry | Scan>;
  entries: ImportHistoryEntry[];
  scans: Scan[];
  batches: ScanBatchSummary[];
  /** The uploads that were not imported (or that a status filter lists). */
  uploads: IngestionResultItem[];
  /** One kind of row could not be loaded: the page is missing entries. */
  partial: boolean;
  total: number | null;
  hasMore: boolean;
  /** The status filter's counts (analysts and above; null otherwise). */
  statusCounts: ImportHistoryPage['status_counts'];
  /** How many of each kind of row the whole history holds under its filters
   *  — the server's totals, never the loaded pages' (review 2026-10-22,
   *  B3-2).  `uploads` is null from a server that does not send it. */
  kindTotals: { batches: number; scans: number; uploads: number | null } | null;
}

/** What lists or counts the imported files. */
const INVENTORY_READS = [
  'getImportHistory', 'getScans', 'getScansSummary', 'getBatchUnimportedJobs',
] as const;

// All filtering and sorting is server-side: a project can hold far more scans
// than one page, and filtering the loaded page would silently miss matching
// scans further down.  The page is "Load more"; 250 files covers most
// installations in one page (a button at the bottom of a 100-row table was
// not noticed).
const SCAN_LIMIT = 250;
// History rows per page. Each page's rows are fetched by id, and the id list
// rides in the query string, so this stays well under its cap.
const HISTORY_PAGE = 100;
const DATE_RANGE_PRESETS: ReadonlyArray<{ label: string; days: number | null }> = [
  { label: 'All time', days: null },
  { label: 'Last 7d', days: 7 },
  { label: 'Last 30d', days: 30 },
  { label: 'Last 90d', days: 90 },
];

/**
 * The filters, the sort and the view are the address's (UI_STYLE_GUIDE §39):
 * read from it on every render — never copied into state — so a link to this
 * page with other filters (Operations' `?since=`), and Back, are what the
 * controls show and what the lists are asked for.  A value the address cannot
 * mean is the default.
 */
/** What choosing a status filter changes in the address. */
const statusChanges = (status: ImportHistoryStatus | null): Record<string, string | null> => (
  { status, job_id: null, error_id: null, batch_files: null }
);

/** A positive whole number from the address, else null. */
const idParam = (raw: string | null): number | null => (raw && /^\d+$/.test(raw) && Number(raw) > 0 ? Number(raw) : null);

function useScansAddress() {
  const [urlParams, setUrlParams] = useSearchParams();
  // The uploads' filters (5.418.0; they were the Ingestion results view's):
  // a status view, or one upload by its id or its parse error's.  Read only
  // for analysts and above, who may read uploads — for anyone else the
  // address names the history, whatever it says (hidden, not refused).
  const { canWrite: readsUploads } = useProjectRole();
  const rawStatus = urlParams.get('status') as ImportHistoryStatus | null;
  const statusFilter: ImportHistoryStatus | null = readsUploads && rawStatus && IMPORT_HISTORY_STATUSES.includes(rawStatus)
    ? rawStatus : null;
  const focusJobId = readsUploads ? idParam(urlParams.get('job_id')) : null;
  const focusErrorId = readsUploads ? idParam(urlParams.get('error_id')) : null;
  const toolFilter = urlParams.get('tool') || '';
  const dateRangeDays: number | null = (() => {
    const raw = urlParams.get('days');
    if (!raw) return null;
    const parsed = parseInt(raw, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  })();
  const sortBy: ScansSortBy = (() => {
    const raw = urlParams.get('sort_by') as ScansSortBy | null;
    return raw && SCANS_SORT_COLUMNS.includes(raw) ? raw : 'created_at';
  })();
  const sortOrder: ScansSortOrder = urlParams.get('sort_order') === 'asc' ? 'asc' : 'desc';
  // The all-files view lists imported files; a status view lists uploads.
  const showBatchFiles = urlParams.get('batch_files') === 'show'
    && statusFilter === null && focusJobId === null && focusErrorId === null;
  // Who uploaded the file (an agent's uploads are its operator's).
  const uploaderFilter: number | null = (() => {
    const parsed = parseInt(urlParams.get('uploaded_by') || '', 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  })();
  // `?since=<ISO>`: the imports since a moment, so Operations' "8 new
  // imports" opens those 8.  Choosing a day range replaces it.
  const sinceIso: string | null = (() => {
    const raw = urlParams.get('since');
    return raw && !Number.isNaN(Date.parse(raw)) ? raw : null;
  })();
  // The one writer: each named param is set, or left out of the address when
  // its value is `null` (the default).  Replaced, not pushed — a filter is not
  // a place Back should stop at.  One call per change: the router gives an
  // updater the address of this render, so two calls in a row would lose the
  // first.
  const setUrlFilters = useCallback((changes: Record<string, string | null>) => {
    setUrlParams((prev) => {
      const next = new URLSearchParams(prev);
      Object.entries(changes).forEach(([name, value]) => {
        if (value === null) next.delete(name);
        else next.set(name, value);
      });
      return next;
    }, { replace: true });
  }, [setUrlParams]);
  // The search box: `draft` is what is being typed, `value` what is in the
  // address — and so what is asked for.
  const search = useUrlSearchDraft('search');
  const searchValue = search.value;
  const hasActiveFilters = toolFilter !== '' || searchValue !== ''
    || dateRangeDays !== null || sinceIso !== null || uploaderFilter !== null;
  const createdAfterIso = useMemo(() => {
    if (sinceIso) return sinceIso;
    if (dateRangeDays == null) return undefined;
    return new Date(Date.now() - dateRangeDays * 24 * 60 * 60 * 1000).toISOString();
  }, [dateRangeDays, sinceIso]);
  // One filter object for every list, the summary and the batch rows, so
  // they can never disagree about what "the current filters" are.
  const listFilters = useMemo(
    () => ({
      search: searchValue || undefined,
      tool: toolFilter || undefined,
      createdAfter: createdAfterIso,
      uploadedBy: uploaderFilter ?? undefined,
    }),
    [searchValue, toolFilter, createdAfterIso, uploaderFilter],
  );
  // Sorting: the same column again toggles asc / desc; another column sorts
  // by it, descending.  In the address the defaults (`created_at`, `desc`)
  // are left out.
  const sortOn = (column: ScansSortBy) => {
    if (sortBy === column) {
      setUrlFilters({ sort_order: sortOrder === 'asc' ? null : 'asc' });
      return;
    }
    setUrlFilters({ sort_by: column === 'created_at' ? null : column, sort_order: null });
  };
  return {
    readsUploads,
    statusFilter,
    focusJobId,
    focusErrorId,
    /** The list is the uploads one view names, flat, instead of the grouped
     *  history. */
    uploadView: statusFilter !== null || focusJobId !== null || focusErrorId !== null,
    // One write: a status replaces a link's one upload, and the uploads are
    // listed one row each, so the all-files switch is let go too.
    setStatusFilter: (status: ImportHistoryStatus | null) => setUrlFilters(statusChanges(status)),
    /** The address that same write makes — for a link elsewhere on the page
     *  (the queue strip's failed line) that must open exactly the list the
     *  chip opens. */
    statusHref: (status: ImportHistoryStatus) => {
      const next = new URLSearchParams(urlParams);
      Object.entries(statusChanges(status)).forEach(([name, value]) => {
        if (value === null) next.delete(name);
        else next.set(name, value);
      });
      return `/scans?${next.toString()}`;
    },
    showWholeHistory: () => setUrlFilters({ status: null, job_id: null, error_id: null }),
    toolFilter,
    setToolFilter: (tool: string) => setUrlFilters({ tool: tool || null }),
    dateRangeDays,
    sinceIso,
    setDays: (days: number | null) => setUrlFilters({ since: null, days: days == null ? null : String(days) }),
    uploaderFilter,
    setUploaderFilter: (userId: number | null) =>
      setUrlFilters({ uploaded_by: userId == null ? null : String(userId) }),
    showBatchFiles,
    setShowBatchFiles: (show: boolean) => setUrlFilters({ batch_files: show ? 'show' : null }),
    sortBy,
    sortOrder,
    sortOn,
    search,
    hasActiveFilters,
    listFilters,
    clearFilters: () => {
      // One write for all of them; the box drops what was being typed, so it
      // is not committed a moment later.
      search.setDraft('');
      setUrlFilters({ search: null, tool: null, days: null, since: null, uploaded_by: null });
    },
  };
}

type ScansAddress = ReturnType<typeof useScansAddress>;

export default function Scans() {
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  const invalidate = useCallback((names: readonly string[]) => {
    void invalidateReads(queryClient, ...names);
  }, [queryClient]);
  const refreshQueue = useCallback(() => invalidate(QUEUE_READS), [invalidate]);
  const refreshInventory = useCallback(() => invalidate(INVENTORY_READS), [invalidate]);

  // Deleting a scan: the dialog is about one scan, which stays set while it
  // closes.
  const [scanToDelete, setScanToDelete] = useState<Scan | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  // The upload review (choose → review formats → import → results).  Staged
  // files are handed back to it too ("Review N waiting", a queue row's
  // Review, a batch's Review).
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [reviewResume, setReviewResume] = useState<{ jobs: IngestionJob[] } | null>(null);
  const openReview = useCallback((jobs: IngestionJob[]) => {
    if (jobs.length === 0) return;
    setReviewResume({ jobs });
    setUploadDialogOpen(true);
  }, []);

  const queue = useIngestionQueue(projectId);
  // An import that finished put the queue — and, when it completed, the lists
  // and counts of imported files — out of date.
  const banner = useUploadBanner(projectId, refreshQueue, refreshInventory);

  const address = useScansAddress();
  const { showBatchFiles, sortBy, sortOrder, listFilters, hasActiveFilters, uploadView } = address;
  // What narrows the list to uploads, in the request and in the list's key.
  const uploadFilters = useMemo(() => ({
    status: address.statusFilter ?? undefined,
    jobId: address.focusJobId ?? undefined,
    errorId: address.focusErrorId ?? undefined,
  }), [address.statusFilter, address.focusJobId, address.focusErrorId]);
  // The project's effective "skip informational Nessus observations" (its own
  // choice, else the deployment default), sent with each upload; changed in
  // Project settings → Imports.
  const { currentProject } = useProject();
  // Importing and retrying are a project analyst's (R32): a viewer or auditor
  // reads the import history without the write controls.  DELETING a scan is
  // the project admin's on the server (`deletion-impact` and `DELETE
  // /scans/{id}`), so an analyst is not offered it either.
  const { canWrite, isProjectAdmin } = useProjectRole();
  const skipInformational = currentProject?.skip_informational_effective ?? false;

  // The import history, a page at a time ("Load more").
  //   Grouped by upload: the SERVER decides the order of batches and single
  //   files together (`GET /scans/history`); a page is that order plus its
  //   rows, fetched by id from the endpoints that compute them.
  //   All files: one flat, sortable list of every imported file.
  // On the list hook (`useListQuery`), with what this list needs of it: each
  // page states its own end (`hasMore` — the history's `has_more`; for all
  // files, which has no total, "a full page came back"); every loaded page is
  // read (`responses`); and the previous rows stay only WHILE another filter
  // loads (`keepPrevious: 'loading'`) — after a failed read on a new filter
  // only the error and Retry show.
  const historyList = useListQuery<ImportHistoryEntry | Scan, HistoryPage>(
    showBatchFiles ? 'getScans' : 'getImportHistory',
    async ({ offset, limit, signal }): Promise<HistoryPage> => {
      if (showBatchFiles) {
        const files = await getScans(projectId, offset, limit, {
          ...listFilters, sortBy, sortOrder, unbatched: false, signal,
        });
        return {
          flat: true, items: files, entries: [], scans: files, batches: [], uploads: [], partial: false, total: null,
          hasMore: files.length === limit, statusCounts: null, kindTotals: null,
        };
      }
      // The ORDER of every kind at once — batches, single files and (for an
      // analyst) the uploads that were not imported, or under a status filter
      // the uploads it names — paged on the server; the rows by id.
      const page = await getImportHistory(projectId, {
        ...listFilters, ...uploadFilters, ...(offset > 0 ? { skip: offset } : {}), limit, signal,
      });
      const rows = await hydrateHistoryRows(
        projectId, page.items, listFilters, { getScans, getScanBatches, getIngestionResults },
      );
      return {
        flat: false, items: page.items, entries: page.items, scans: rows.scans, batches: rows.batches,
        uploads: rows.uploads,
        partial: rows.partial,
        total: typeof page.total === 'number' ? page.total : null,
        hasMore: page.has_more,
        statusCounts: page.status_counts ?? null,
        kindTotals: {
          batches: page.batch_total, scans: page.scan_total, uploads: page.upload_total ?? null,
        },
      };
    },
    showBatchFiles
      ? [projectId, { ...listFilters, sortBy, sortOrder, unbatched: false }]
      : [projectId, listFilters, uploadFilters],
    {
      pageSize: showBatchFiles ? SCAN_LIMIT : HISTORY_PAGE,
      keepPrevious: 'loading',
      within: projectId,
      errorMessage: showBatchFiles ? 'Could not load the imported scans.' : 'Could not load the import history.',
    },
  );
  const historyPages = historyList.responses;
  // The import history's ORDER (batches and single files together, newest
  // first), from the server; `scans` and `batches` hold the rows (an upload
  // batch is one row per sweep, its files listed individually only in the
  // all-files view).
  const history = useMemo(() => historyPages?.flatMap((page) => page.entries) ?? [], [historyPages]);
  const scans = useMemo(() => historyPages?.flatMap((page) => page.scans) ?? [], [historyPages]);
  const batches = useMemo(() => historyPages?.flatMap((page) => page.batches) ?? [], [historyPages]);
  const uploads = useMemo(() => historyPages?.flatMap((page) => page.uploads) ?? [], [historyPages]);
  const historyPartial = !!historyPages?.some((page) => page.partial);
  const historyTotal = historyPages?.[0]?.total ?? null;
  const kindTotals = historyPages?.[0]?.kindTotals ?? null;
  // The status filter's counts, from the first page (the server states them
  // on every page, under the same filters).
  const statusCounts = historyPages?.[0]?.statusCounts ?? null;
  // Which way the rows ON SCREEN are listed: while the other view loads, they
  // are still the previous view's.
  const listedFlat = historyPages?.[0]?.flat ?? showBatchFiles;
  // The skeleton is for the first read only.  A read that failed with nothing
  // to show and is being tried again keeps the page — and what it said went
  // wrong — until the answer arrives: the upload review may be open on it.
  // (`retrying` is the hook's; with rows of the previous filter on screen it
  // is that filter loading again, not this.)
  const retryingFailedLoad = historyList.retrying && historyPages === null;
  const loading = historyPages === null && historyList.failure === null && !retryingFailedLoad;
  // A failed "Load more" is a toast; it is not a failure of the list on screen
  // (the hook's `error` leaves it out).
  const failedNow = historyList.error;
  const lastFailure = useRef<string | null>(null);
  if (failedNow) lastFailure.current = failedNow;
  else if (!retryingFailedLoad) lastFailure.current = null;
  const historyError = failedNow ?? (retryingFailedLoad ? lastFailure.current : null);
  const { loadMore } = historyList;
  const loadMoreScans = useCallback(async () => {
    try {
      await loadMore();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not load more of the import history'));
    }
  }, [loadMore, toast]);

  // Filter-aware totals for the lead and the tool counts — counted server-side
  // so they reflect every matching file, not just the loaded pages.  Its own
  // read: a failure here must not block the table.  When it fails its counts
  // are shown as not known, never as the previous filter's or the loaded rows'.
  const summaryQuery = useQuery({
    queryKey: ['getScansSummary', projectId, listFilters],
    queryFn: ({ signal }) => getScansSummary(projectId, { ...listFilters, signal }),
    placeholderData: keepPreviousData,
  });
  const summaryFailed = summaryQuery.isError || (summaryQuery.isPending && summaryQuery.errorUpdateCount > 0);
  const inventorySummary = summaryFailed ? null : summaryQuery.data ?? null;

  // The import history's rows, in the server's order.
  const historyRows = useMemo(
    () => orderHistoryRows(history, scans, batches, uploads), [history, scans, batches, uploads],
  );
  const tableRows = useMemo(
    () => (listedFlat
      ? scans.map((scan) => ({ kind: 'scan' as const, key: `scan-${scan.id}`, scan }))
      : historyRows),
    [listedFlat, scans, historyRows],
  );
  // The time column is the upload time — the grouped history's own order —
  // except in the all-files view sorted by when the scans ran.  It follows
  // the rows ON SCREEN (`listedFlat`): while the other view loads they are
  // still the previous view's, and keep the time they were listed by.
  const timeColumn: ScanTimeColumn = listedFlat && sortBy === 'start_time' ? 'ran' : 'uploaded';

  // Refresh when ANY scan lands: the banner follows only uploads this tab
  // started, so scans from an agent or another tab raised counters elsewhere
  // while this list stayed stale.  The marker (a count + the newest id,
  // polled while the tab is visible; the first answer is the baseline) and
  // its baseline are `useScanInventoryMarker`'s.
  const inventoryMoved = useCallback(() => {
    refreshInventory();
    refreshQueue();
  }, [refreshInventory, refreshQueue]);
  const inventoryMarker = useScanInventoryMarker(projectId, inventoryMoved);

  // The tool counts are EVERY file matching the search/date filters,
  // server-side, batched files included.  The loaded `scans` are the fallback
  // for an older backend (grouped, they hold only the unbatched files).
  const toolChips = useMemo<[string, number][]>(() => {
    const server = inventorySummary?.tool_counts;
    if (server && Object.keys(server).length > 0) {
      return Object.entries(server).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    }
    const counts = new Map<string, number>();
    for (const scan of scans) {
      const key = (scan.tool_name || scan.scan_type || 'Other').toUpperCase();
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [inventorySummary, scans]);

  // The status filter's counts.  Grouped, they ride on the history's own
  // answer; in the all-files view (which lists files, not uploads) one more
  // read of the history's first entry carries them, so the queue strip's
  // failed line is there whichever way the history is listed.  Analysts only:
  // the server sends nobody else counts.
  const flatViewCounts = useQuery({
    queryKey: ['getImportHistory', projectId, listFilters, { limit: 1 }],
    queryFn: ({ signal }) => getImportHistory(projectId, { ...listFilters, limit: 1, signal }),
    enabled: showBatchFiles && address.readsUploads,
  });
  const historyStatusCounts = showBatchFiles ? flatViewCounts.data?.status_counts ?? null : statusCounts;
  // The queue strip's one line about failures (owner decision 2026-10-22: a
  // failed upload is a row of the history, not of the queue): the history's
  // own Needs-attention count under its filters — failed or partial, not
  // dismissed, not superseded; the one "imports needing someone" — opening
  // that exact list, the address its chip writes.  Not Failed: that count
  // includes uploads someone already dismissed, which need no review.
  const queueFailed = address.readsUploads && historyStatusCounts?.needs_attention != null
    ? { count: historyStatusCounts.needs_attention, href: address.statusHref('needs_attention') }
    : null;

  const handleViewScan = (scanId: number) => navigate(`/scans/${scanId}`);
  const handleDeleteClick = (scan: Scan) => {
    setScanToDelete(scan);
    setDeleteDialogOpen(true);
  };

  // The newest upload among what is loaded (the first page is newest first).
  // (Upload rows are not imports: an upload that failed is not "the last import".)
  const lastImportAt = [
    ...history.filter((h) => h.kind !== 'upload').map((h) => h.at),
    ...scans.map((sc) => sc.created_at),
    ...batches.map((b) => b.last_uploaded),
  ].filter((v): v is string => !!v).sort((x, y) => new Date(x).getTime() - new Date(y).getTime()).pop() ?? null;

  if (loading) {
    // The title stays where it is while the list is read.
    return (
      <div className="p-md md:p-lg">
        <div className="mb-md flex min-h-10 flex-wrap items-start justify-between gap-sm">
          <h1 className="text-page-title font-semibold">Scans</h1>
        </div>
        <TableSkeleton rows={8} columns={6} />
      </div>
    );
  }

  const nothingLoaded = scans.length === 0 && batches.length === 0 && uploads.length === 0;
  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-start justify-between gap-sm">
        <div>
          <h1 className="text-page-title font-semibold">Scans</h1>
        </div>
        <div className="flex flex-wrap items-center gap-xs">
          <Button variant="outline" onClick={() => navigate('/scans/compare')}>
            <GitCompareArrows className="size-4" aria-hidden /> Compare scans
          </Button>
          {canWrite && (
            <Button onClick={() => setUploadDialogOpen(true)}>
              <Upload className="size-4" aria-hidden /> Upload scans
            </Button>
          )}
        </div>
      </div>
      {/* One sentence: counts from the filter-aware /scans/summary (every
          matching file, not the loaded page). */}
      <ScansLead
        files={inventorySummary?.total_files ?? inventorySummary?.total_scans ?? scans.length}
        filesUnknown={summaryFailed}
        filtered={hasActiveFilters}
        readsUploads={address.readsUploads}
        needAttention={inventorySummary?.imports_need_attention}
        notImported={inventorySummary?.imports_not_imported}
        byReason={inventorySummary?.imports_not_imported_by_reason}
        superseded={inventorySummary?.imports_superseded}
        lastImportAt={lastImportAt}
      />

      <UploadBanner banner={banner} />

      <IngestionQueue
        projectId={projectId}
        queue={queue}
        failed={queueFailed}
        canWrite={canWrite}
        onRefresh={refreshQueue}
        onReview={openReview}
      />

      <PostureSection title="Import history">
      {historyError && (
        <Alert variant="destructive" className="mb-sm" data-testid="history-error">
          <AlertDescription className="flex flex-wrap items-center gap-sm">
            <span className="min-w-0 flex-1 break-words">
              {historyError}
              {!nothingLoaded && ' The rows below are from the last successful load.'}
            </span>
            <Button size="sm" variant="outline" onClick={refreshInventory}>Retry</Button>
          </AlertDescription>
        </Alert>
      )}
      {historyError && nothingLoaded ? null : !hasActiveFilters && !uploadView && nothingLoaded ? (
        <div className="py-xl text-center">
          <Upload className="mx-auto mb-sm size-16 text-muted-foreground" aria-hidden />
          <p className="text-subheading text-muted-foreground">No scans uploaded yet</p>
          <p className="text-metadata text-muted-foreground">
            {canWrite
              ? <>Use Upload scans to import Nmap, Nessus, Masscan, OpenVAS, httpx, dnsx, BloodHound,
                EyeWitness, NetExec, and other supported scanner exports — expand "Supported
                formats" below the upload area for the full list and per-tool notes.</>
              : 'Nothing has been imported into this project. A project analyst uploads scanner output here.'}
          </p>
        </div>
      ) : (
        <div>
          <HistoryFilterBar
            address={address}
            toolChips={toolChips}
            summary={inventorySummary}
            summaryFailed={summaryFailed}
            loaded={nothingLoaded ? undefined : (
              <>
                {/* How much of the list is loaded, so a partial view is never
                    mistaken for the whole one. */}
                {showBatchFiles
                  ? `${scans.length.toLocaleString()}${inventorySummary?.total_files != null ? ` of ${inventorySummary.total_files.toLocaleString()}` : ''} file${scans.length === 1 ? '' : 's'}`
                  : `${historyRows.length.toLocaleString()}${historyTotal != null && historyTotal > historyRows.length ? ` of ${historyTotal.toLocaleString()}` : ''} upload${historyRows.length === 1 ? '' : 's'}`
                    // The whole history's breakdown, from the server's totals
                    // — never the loaded pages' rows (review 2026-10-22, B3-2).
                    + (uploadView || !kindTotals ? '' : ` · ${kindTotals.batches.toLocaleString()} batch${kindTotals.batches === 1 ? '' : 'es'}, `
                      + `${kindTotals.scans.toLocaleString()} single file${kindTotals.scans === 1 ? '' : 's'}`
                      + (kindTotals.uploads ? `, ${kindTotals.uploads.toLocaleString()} not imported` : ''))}
                {historyList.hasMore ? ' · more below' : hasActiveFilters || uploadView ? ' (filtered)' : ''}
              </>
            )}
            fallbackTotal={scans.length}
          />
          {/* The status filter: analysts and above, who may open what it
              counts (the server sends no counts to anyone else). */}
          {statusCounts && !showBatchFiles && (
            <UploadStatusFilter
              counts={statusCounts}
              active={address.statusFilter}
              onChange={address.setStatusFilter}
              shownUploads={uploads}
              canWrite={canWrite}
              busy={historyList.loadingMore}
            />
          )}
          {(address.focusJobId !== null || address.focusErrorId !== null) && (
            <p className="mb-xxs text-caption text-muted-foreground" data-testid="one-upload">
              {address.focusJobId !== null
                ? `Upload #${address.focusJobId}`
                : `The upload that recorded parse error #${address.focusErrorId}`}
              {' · '}
              <button type="button" className="text-info hover:underline" onClick={address.showWholeHistory}>
                Show the whole history
              </button>
            </p>
          )}
          <p className="mb-xxs text-caption text-muted-foreground">
            {showBatchFiles
              ? 'Every imported file, batched or not. Click a column heading to sort.'
              : uploadView
                ? 'One row per upload, newest upload first.'
                : address.readsUploads
                  ? 'Newest upload first. Files dropped together are one batch row; expand it for its files. An upload that was not imported is a row of its own.'
                  : 'Newest upload first. Files dropped together are one batch row; expand it for its files.'}
          </p>
          {historyPartial && !showBatchFiles && (
            <p role="alert" className="mb-xxs text-caption text-warning">
              Some rows of this history could not be loaded, so the list below is missing entries.
              Refresh to try again.
            </p>
          )}
          {tableRows.length === 0 && uploadView ? (
            <div className="flex flex-col items-start gap-xs border-l-4 border-border py-xs pl-md">
              <p className="flex items-center gap-xs text-subheading font-semibold">
                <AlertCircle className="size-4 text-muted-foreground" aria-hidden /> No uploads match
              </p>
              <Button variant="outline" size="sm" onClick={address.showWholeHistory}>
                Show the whole history
              </Button>
            </div>
          ) : tableRows.length === 0 ? (
            // Filter-aware empty state — the filters remain visible so the
            // reader can clear or refine without navigating away.
            <div className="flex flex-col items-start gap-xs border-l-4 border-border py-xs pl-md">
                <p className="flex items-center gap-xs text-subheading font-semibold">
                  <AlertCircle className="size-4 text-muted-foreground" aria-hidden /> No scans match these filters
                </p>
                <p className="max-w-md text-metadata text-muted-foreground">
                  Adjust the search, tool, date range or uploader above — or clear them to see every scan
                  in this project.
                </p>
                <Button variant="outline" size="sm" onClick={address.clearFilters}>
                  Clear filters
                </Button>
            </div>
          ) : (
            <ImportHistoryTable
              rows={tableRows}
              sortable={showBatchFiles}
              timeColumn={timeColumn}
              sortBy={sortBy}
              sortOrder={sortOrder}
              onSort={address.sortOn}
              toolFilter={address.toolFilter}
              onToolFilter={address.setToolFilter}
              stagedByBatch={queue.stagedByBatch}
              onReviewStaged={canWrite ? openReview : undefined}
              filters={listFilters}
              onViewScan={handleViewScan}
              onDelete={isProjectAdmin ? handleDeleteClick : undefined}
              expandUploads={address.focusJobId !== null || address.focusErrorId !== null}
              uploadLinks={address.readsUploads}
            />
          )}

          {historyList.hasMore && (
            <div className="mt-md flex justify-center">
              <Button
                variant="outline"
                onClick={loadMoreScans}
                disabled={historyList.loadingMore}
              >
                {historyList.loadingMore ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <ChevronDown className="size-4" aria-hidden />
                )}
                {historyList.loadingMore ? 'Loading…' : showBatchFiles ? `Load ${SCAN_LIMIT} more` : `Load ${HISTORY_PAGE} more uploads`}
              </Button>
            </div>
          )}
        </div>
      )}
      </PostureSection>

      {/* The upload review stages and inspects each file; a started file is
          handed to the banner above by job id. */}
      <UploadReviewDialog
        open={uploadDialogOpen}
        onOpenChange={(next) => {
          setUploadDialogOpen(next);
          if (!next) {
            // Cleared on close: left set, the next "Upload scans" would bring
            // back files discarded in this review.
            setReviewResume(null);
            refreshQueue();
          }
        }}
        resume={reviewResume}
        projectName={currentProject?.name}
        skipInformational={skipInformational}
        onViewScan={handleViewScan}
        onStarted={(started) => {
          // The row is 'received' until the server answers about its job; a
          // key started again takes its row back (dismissed or not).
          banner.start({ key: started.key, filename: started.filename, jobId: started.jobId });
        }}
      />

      <DeleteScanDialog
        scan={scanToDelete}
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onDeleted={() => {
          setDeleteDialogOpen(false);
          setScanToDelete(null);
          refreshInventory();
          // The inventory marker moves with this delete, and the lists are being
          // read again already: its new value is the baseline, not a change.
          inventoryMarker.rebase();
        }}
      />
    </div>
  );
}

/**
 * The import history's filters, ONE row: search, tool, range, uploader, the
 * Grouped / All files switch, and how much of the list is loaded.
 */
const HistoryFilterBar: React.FC<{
  address: ScansAddress;
  toolChips: [string, number][];
  summary: ScanInventorySummary | null;
  summaryFailed: boolean;
  /** How much of the list is loaded (absent: nothing is). */
  loaded: React.ReactNode;
  /** The file count when the summary has none. */
  fallbackTotal: number;
}> = ({ address, toolChips, summary, summaryFailed, loaded, fallbackTotal }) => {
  const { toolFilter, sinceIso, dateRangeDays, uploaderFilter, showBatchFiles, search } = address;
  return (
    <ListFilterBar summary={loaded}>
      <ListFilterSearch
        value={search.draft}
        onChange={search.setDraft}
        placeholder="Search filename, tool, scan type…"
        label="Search scan inventory"
      />
      {/* The list matches a tool whatever its case, so the select does too:
          a link written `?tool=nmap` shows "nmap (9)", not a blank box
          (the options' values are the server's spelling, `NMAP`). */}
      <Select
        value={toolFilter
          ? toolChips.find(([g]) => g.toLowerCase() === toolFilter.toLowerCase())?.[0] ?? toolFilter
          : '__all'}
        onValueChange={(v) => address.setToolFilter(v === '__all' ? '' : v)}
      >
        <SelectTrigger className="h-8 w-44 text-metadata" aria-label="Filter scans by tool">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all">
            All tools ({summaryFailed ? '—' : (summary?.total_files ?? fallbackTotal).toLocaleString()})
          </SelectItem>
          {toolChips.map(([group, count]) => (
            <SelectItem key={group} value={group}>
              {group.toLowerCase()} ({summaryFailed ? '—' : count.toLocaleString()})
            </SelectItem>
          ))}
          {/* A tool picked from a row badge that the counts do not list. */}
          {toolFilter && !toolChips.some(([g]) => g.toLowerCase() === toolFilter.toLowerCase()) && (
            <SelectItem value={toolFilter}>{toolFilter.toLowerCase()}</SelectItem>
          )}
        </SelectContent>
      </Select>
      <Select
        value={sinceIso ? 'since' : dateRangeDays == null ? 'all' : String(dateRangeDays)}
        onValueChange={(v) => {
          if (v === 'since') return;
          // A day range replaces `since`.
          address.setDays(v === 'all' ? null : parseInt(v, 10));
        }}
      >
        <SelectTrigger className="h-8 w-44 text-metadata" aria-label="Filter scans by upload date"
          title={sinceIso ? `Uploaded since ${formatTimestamp(sinceIso)}` : undefined}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {sinceIso && (
            <SelectItem value="since">Since {formatDate(sinceIso)}</SelectItem>
          )}
          {DATE_RANGE_PRESETS.map((preset) => (
            <SelectItem key={preset.label} value={preset.days == null ? 'all' : String(preset.days)}>
              {preset.days == null ? 'Any time' : `Last ${preset.days} days`}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {/* Who uploaded it — offered once there is more than one uploader (or a
          link arrived with one chosen). The counts follow the other filters,
          never this one, so everyone stays listed. */}
      {((summary?.uploaders?.length ?? 0) > 1 || uploaderFilter != null) && (
        <Select
          value={uploaderFilter == null ? 'anyone' : String(uploaderFilter)}
          onValueChange={(v) => address.setUploaderFilter(v === 'anyone' ? null : parseInt(v, 10))}
        >
          <SelectTrigger className="h-8 w-44 min-w-0 text-metadata" aria-label="Filter scans by uploader">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="anyone">Uploaded by anyone</SelectItem>
            {(summary?.uploaders ?? []).map((u) => (
              // The full name is shown; the id stays the value.
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
              && !(summary?.uploaders ?? []).some((u) => u.user_id === uploaderFilter) && (
              <SelectItem value={String(uploaderFilter)}>User #{uploaderFilter} (0)</SelectItem>
            )}
          </SelectContent>
        </Select>
      )}
      {/* One history, two ways to read it (a status filter lists uploads,
          one row each, so neither applies to it). */}
      {!address.uploadView && (
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
            onClick={() => address.setShowBatchFiles(opt.value)}
            className={cn(
              'px-sm py-xxs text-metadata transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              showBatchFiles === opt.value ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
      )}
    </ListFilterBar>
  );
};

/**
 * The page's lead: how much has been imported, whether anything needs
 * attention, and when the last file arrived.  "Nothing failed" is said only
 * when the queue was actually read AND no job of the project failed.
 *
 * The failure figures come from the summary, over the whole project
 * (`imports_need_attention`: failed or partial, not dismissed — the count
 * the history's "Needs attention" filter lists; `imports_not_imported`:
 * failures already dismissed, discards and expiries included).  The lead used
 * to count only the 25 most recent jobs, so it said "nothing failed" beside a
 * batch whose 31 files had all expired.  `failed` (the recent queue) is the
 * fallback when the summary did not carry the figures.
 *
 * Short and specific: "40 files imported · 31 expired before review, not
 * imported · last import 55 minutes ago".  The closed failures are named by
 * their ACTUAL reasons with their counts (`byReason`, from the summary)
 * instead of listing every possible one, and with no total of their own
 * (review 2026-10-22, B3-3); the explanation is an info tip.
 */
/** [key, singular, plural]: "(25 discarded, 1 dismissed failure)" read
 *  "(25 discarded, 1 failed, dismissed)". */
const NOT_IMPORTED_REASON: Array<[string, string, string]> = [
  ['expired', 'expired before review', 'expired before review'],
  ['discarded', 'discarded', 'discarded'],
  ['dismissed', 'dismissed failure', 'dismissed failures'],
];

/** Which status filter of the history lists each reason (expired and
 *  discarded uploads are out of its Failed filter; the dismissed failures
 *  have their own — the link used to open every failed import). */
const NOT_IMPORTED_VIEW: Record<string, string> = { expired: 'expired', discarded: 'discarded', dismissed: 'failed_dismissed' };

const notImportedReasons = (byReason?: Record<string, number>): Array<{ key: string; text: string }> =>
  NOT_IMPORTED_REASON
    .filter(([key]) => (byReason?.[key] ?? 0) > 0)
    .map(([key, one, many]) => {
      const n = byReason![key];
      return { key, text: `${n.toLocaleString()} ${n === 1 ? one : many}` };
    });

const ScansLead: React.FC<{
  files: number;
  /** The summary could not be read: `files` is not the count. */
  filesUnknown?: boolean;
  filtered: boolean;
  /** The project's imports needing someone (the summary's); absent when the
   *  summary did not carry it — then the lead says nothing about failures
   *  (5.418.0: the recent queue no longer lists failures to fall back on). */
  needAttention?: number;
  notImported?: number;
  byReason?: Record<string, number>;
  superseded?: number;
  lastImportAt: string | null;
  /** Analysts and above read the uploads that were not imported; for anyone
   *  else the lead says nothing about them — every figure here opens a list
   *  of uploads they cannot open (5.418.0). */
  readsUploads?: boolean;
}> = ({
  files, filesUnknown = false, filtered, needAttention, notImported = 0, byReason, superseded = 0,
  lastImportAt, readsUploads = true,
}) => {
  if (files === 0 && !filtered && !filesUnknown) return null;
  const last = lastImportAt ? formatRelativeTime(lastImportAt, { style: 'long' }) : null;
  if (!readsUploads) {
    return (
      <PostureLead className="mb-md" tone="neutral">
        <span className="tabular-nums">
          {filesUnknown
            ? `The imported files${filtered ? ' matching these filters' : ''} could not be counted`
            : `${files.toLocaleString()} file${files === 1 ? '' : 's'} imported${filtered ? ' (matching these filters)' : ''}`}
        </span>
        {last && <><span className="text-muted-foreground"> · </span>last import {last}</>}
      </PostureLead>
    );
  }
  const projectWide = needAttention != null;
  const attention = needAttention ?? 0;
  const reasons = notImportedReasons(byReason);
  const sep = <span className="text-muted-foreground"> · </span>;
  return (
    <PostureLead className="mb-md" tone={attention > 0 ? 'warning' : 'neutral'}>
      <span className="tabular-nums">
        {filesUnknown
          ? `The imported files${filtered ? ' matching these filters' : ''} could not be counted`
          : `${files.toLocaleString()} file${files === 1 ? '' : 's'} imported${filtered ? ' (matching these filters)' : ''}`}
      </span>
      {projectWide && (
        <>
          {attention > 0 && (
            <>
              {sep}
              <Link to={importResultsPath({ status: 'needs_attention' })} className="text-warning underline-offset-2 hover:underline">
                {attention.toLocaleString()} failed or partial import{attention === 1 ? '' : 's'}{' '}
                need{attention === 1 ? 's' : ''} attention
              </Link>
            </>
          )}
          {notImported > 0 && (
            <>
              {sep}
              {/* Review 2026-10-22, B3-3: "1 never imported (1 dismissed
                  failure)" read as the number of files never imported, while
                  the history's Failed filter listed more (it holds the
                  failures that need attention too).  These are failures
                  someone already closed, so each reason is named with its
                  count and opens its own status filter, and no figure claims
                  to be every upload that was not imported. */}
              {reasons.length > 0 ? (
                <>
                  {reasons.map((r, i) => (
                    <React.Fragment key={r.key}>
                      {i > 0 && (i === reasons.length - 1 ? ' and ' : ', ')}
                      <Link to={importResultsPath({ status: NOT_IMPORTED_VIEW[r.key] })} className="underline-offset-2 hover:underline">
                        {r.text}
                      </Link>
                    </React.Fragment>
                  ))}
                  , not imported
                </>
              ) : (
                // A server that sends no breakdown: no one filter lists
                // exactly these, so the figure opens nothing.
                <>{notImported.toLocaleString()} closed failure{notImported === 1 ? '' : 's'}, not imported</>
              )}
            </>
          )}
          {/* Failures whose file a later upload imported: not "need
              attention", but still listed until dismissed. */}
          {projectWide && superseded > 0 && (
            <>
              {sep}
              <Link to={importResultsPath({ status: 'superseded' })} className="underline-offset-2 hover:underline">
                {superseded.toLocaleString()} failed import{superseded === 1 ? ' was' : 's were'} re-imported later
              </Link>
            </>
          )}
          {attention === 0 && notImported === 0 && superseded === 0 && !filesUnknown && <>{sep}nothing failed</>}
        </>
      )}
      {last && <>{sep}last import {last}</>}{' '}
      <InfoTip
        label="About these figures"
        className="align-middle"
        text="Every imported file counts, batched files included. “Need attention” is every import of this project that failed or finished partial and nobody dismissed — the history’s “Needs attention” filter — except those whose same file a later upload imported (“re-imported later”, superseded). “Not imported” names the failures someone already closed, by reason: files discarded at the format review, staged files nobody started within 24 hours (expired before review), and dismissed failures. The history’s Failed filter lists the dismissed failures together with those that need attention. A file refused at upload as a duplicate never becomes an import and is not counted."
      />
    </PostureLead>
  );
};
