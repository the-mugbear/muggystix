/**
 * /test-plans/:planId/plan — entries table with filters, sortable
 * columns, expandable detail rows.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Loader2,
  Search,
  X,
} from 'lucide-react';
import { TestPlanEntryResponse, updateTestPlanEntry } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { formatStatusLabel } from '../../utils/statusMeta';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import {
  isStructuredTest,
  getTestChipLabel,
  StructuredTestCard,
} from '../../components/ProposedTestList';
import EntryResultsPanel from '../../components/EntryResultsPanel';
import { Alert, AlertDescription } from '../../components/ui/alert';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardContent } from '../../components/ui/card';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../../components/ui/table';
import { useTestPlanContext } from './TestPlanLayout';
import { cn } from '../../utils/cn';

const ENTRY_STATUSES = ['proposed', 'approved', 'in_progress', 'completed', 'rejected'];
const PRIORITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};
const PHASE_ORDER: Record<string, number> = {
  reconnaissance: 0,
  enumeration: 1,
  exploitation: 2,
  post_exploitation: 3,
  reporting: 4,
};

// v4.40.0 — entries are paginated client-side. A plan with thousands of
// entries previously rendered every row (table + expandable detail) in
// one pass, freezing the page on load. We now render one page of
// PAGE_SIZE at a time over the already-filtered/sorted list.
const PAGE_SIZE = 50;

type SortColumn = 'host' | 'priority' | 'phase' | 'tests' | 'status' | 'rationale' | 'findings';
type SortDirection = 'asc' | 'desc';
type Tone =
  | 'default'
  | 'success'
  | 'warning'
  | 'destructive'
  | 'info'
  | 'muted'
  | 'secondary'
  | 'outline'
  | 'severity-critical'
  | 'severity-high'
  | 'severity-medium'
  | 'severity-low';

const stripAttribution = (text: string): string =>
  text.replace(/^🤖\s*\*{0,2}Agent-generated\*{0,2}\s*—\s*\S+\s*/i, '').trimStart();

const priorityTone = (p: string): Tone => {
  switch (p) {
    case 'critical':
      return 'severity-critical';
    case 'high':
      return 'severity-high';
    case 'medium':
      return 'severity-medium';
    case 'low':
      return 'severity-low';
    default:
      return 'muted';
  }
};

const entryStatusTone = (status: string | null | undefined): Tone => {
  switch (status) {
    case 'proposed':
      return 'info';
    case 'approved':
      return 'default';
    case 'in_progress':
      return 'warning';
    case 'completed':
      return 'success';
    case 'rejected':
      return 'destructive';
    default:
      return 'muted';
  }
};

const getEntrySearchText = (entry: TestPlanEntryResponse): string =>
  [
    entry.host_ip,
    entry.host_hostname,
    entry.priority,
    entry.test_phase,
    entry.status,
    stripAttribution(entry.rationale),
    stripAttribution(entry.findings || ''),
    entry.notes || '',
    ...(entry.proposed_tests || []).map((t) => getTestChipLabel(t)),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();

const compareStrings = (l: string, r: string): number =>
  l.localeCompare(r, undefined, { numeric: true, sensitivity: 'base' });

const compareEntries = (
  l: TestPlanEntryResponse,
  r: TestPlanEntryResponse,
  column: SortColumn,
): number => {
  switch (column) {
    case 'host':
      return compareStrings(
        `${l.host_ip || l.host_id} ${l.host_hostname || ''}`,
        `${r.host_ip || r.host_id} ${r.host_hostname || ''}`,
      );
    case 'priority':
      return (PRIORITY_ORDER[l.priority] ?? Number.MAX_SAFE_INTEGER) -
        (PRIORITY_ORDER[r.priority] ?? Number.MAX_SAFE_INTEGER);
    case 'phase':
      return (PHASE_ORDER[l.test_phase] ?? Number.MAX_SAFE_INTEGER) -
        (PHASE_ORDER[r.test_phase] ?? Number.MAX_SAFE_INTEGER);
    case 'tests': {
      const d = (l.proposed_tests?.length || 0) - (r.proposed_tests?.length || 0);
      if (d !== 0) return d;
      return compareStrings(
        (l.proposed_tests || []).map(getTestChipLabel).join(', '),
        (r.proposed_tests || []).map(getTestChipLabel).join(', '),
      );
    }
    case 'status':
      return compareStrings(formatStatusLabel(l.status), formatStatusLabel(r.status));
    case 'rationale':
      return compareStrings(stripAttribution(l.rationale), stripAttribution(r.rationale));
    case 'findings':
      return compareStrings(
        stripAttribution(l.findings || ''),
        stripAttribution(r.findings || ''),
      );
    default:
      return 0;
  }
};

const PlanTab: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const {
    planId,
    plan,
    progress,
    canManage,
    selectedSessionId,
    reload,
    loadMoreEntries,
    isLoadingMoreEntries,
  } = useTestPlanContext();

  const [searchText, setSearchText] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [priorityFilter, setPriorityFilter] = useState('all');
  const [phaseFilter, setPhaseFilter] = useState('all');
  const [sortColumn, setSortColumn] = useState<SortColumn>('priority');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [expandedEntries, setExpandedEntries] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(0);

  // Audit FBK·H13 + PRF·H5: the search input used to re-run the filter
  // pipeline on every keystroke.  Two-part fix:
  //  1. Debounce the value used by the filter (typing stays live; the
  //     filter pass runs 300ms after the last keystroke).
  //  2. Precompute the per-entry search string so the filter doesn't
  //     re-stringify every entry on every keystroke (was O(entries) per
  //     keystroke; now O(1) per keystroke after a one-time O(entries)
  //     pass that invalidates only when plan.entries changes).
  const debouncedSearchText = useDebouncedValue(searchText, 300);

  const entrySearchIndex = useMemo(
    () => plan.entries.map((entry) => ({ entry, searchText: getEntrySearchText(entry) })),
    [plan.entries],
  );

  const hasActiveFilters =
    searchText.trim() !== '' ||
    statusFilter !== 'all' ||
    priorityFilter !== 'all' ||
    phaseFilter !== 'all';

  const filteredEntries = useMemo(() => {
    const normalized = debouncedSearchText.trim().toLowerCase();
    return entrySearchIndex
      .filter(({ entry, searchText: entryText }) => {
        if (statusFilter !== 'all' && entry.status !== statusFilter) return false;
        if (priorityFilter !== 'all' && entry.priority !== priorityFilter) return false;
        if (phaseFilter !== 'all' && entry.test_phase !== phaseFilter) return false;
        if (normalized && !entryText.includes(normalized)) return false;
        return true;
      })
      .map(({ entry }) => entry)
      .sort((l, r) => {
        const result = compareEntries(l, r, sortColumn);
        if (result !== 0) return sortDirection === 'asc' ? result : -result;
        return l.id - r.id;
      });
  }, [entrySearchIndex, debouncedSearchText, statusFilter, priorityFilter, phaseFilter, sortColumn, sortDirection]);

  // Pagination over the filtered/sorted list. `safePage` clamps so a
  // filter change that shrinks the result set can't strand us on an
  // out-of-range (empty) page before the reset effect fires.
  const pageCount = Math.max(1, Math.ceil(filteredEntries.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pagedEntries = useMemo(
    () => filteredEntries.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE),
    [filteredEntries, safePage],
  );

  // Any change to the filter/sort inputs jumps back to page 1.
  useEffect(() => {
    setPage(0);
  }, [debouncedSearchText, statusFilter, priorityFilter, phaseFilter, sortColumn, sortDirection]);

  const visibleEntryIds = useMemo(
    () => new Set(filteredEntries.map((e) => e.id)),
    [filteredEntries],
  );

  useEffect(() => {
    setExpandedEntries((prev) => {
      const next = new Set<number>();
      prev.forEach((id) => {
        if (visibleEntryIds.has(id)) next.add(id);
      });
      return next;
    });
  }, [visibleEntryIds]);

  // v2.85.0 — ``plan.entries`` is now a server-paginated slice; the
  // authoritative full count is ``plan.entries_total``.  Fall back to
  // the slice length when the server didn't paginate (legacy callers
  // that omit entries_limit).  serverLoadedCount is how many entries
  // are physically in memory — drives the "load more" affordance.
  const totalEntries = plan.entries_total ?? plan.entries.length;
  const serverLoadedCount = plan.entries.length;
  const hasMoreOnServer = serverLoadedCount < totalEntries;
  // v2.85.1 — filters run client-side over the loaded slice only.  When
  // entries_total > serverLoadedCount, "Showing X of total" misleads
  // the user into thinking the filter scanned the whole plan; spell out
  // that they're filtering loaded entries and hint at "Load more".
  const sortedEntryCountLabel = hasMoreOnServer
    ? `Showing ${filteredEntries.length} of ${serverLoadedCount} loaded (${totalEntries} total)`
    : `Showing ${filteredEntries.length} of ${totalEntries} entr${totalEntries === 1 ? 'y' : 'ies'}`;
  // Empty-state copy shared by the desktop table and mobile card
  // variants — kept in one place so a wording change can't drift
  // between them.  "apply the filter to more entries" is filter-neutral
  // (covers Status/Priority/Phase as well as the search box).
  const noFilteredEntriesMessage = hasMoreOnServer
    ? `No matches in the ${serverLoadedCount} loaded entries — load more below to apply the filter to more entries (${totalEntries - serverLoadedCount} remaining).`
    : 'No entries match the current filters.';
  // ---------------------------------------------------------------------
  // Deep-link to a specific entry (#entry-N).  "My Work" queue items link
  // here (e.g. /test-plans/42#entry-913); previously the hash was inert —
  // the anchor only exists for the current client page of the loaded server
  // slice, so entries on another page (or beyond what was fetched) had no
  // DOM node and the operator landed at the top of the plan.  This effect
  // drives the whole resolution: pull more pages until the entry is loaded,
  // clear any filter hiding it, page to it, expand it, then scroll + focus.
  // The handled-ref makes it idempotent per hash target so it doesn't fight
  // the user once they've arrived.
  const hashEntryId = useMemo(() => {
    const m = /^#entry-(\d+)$/.exec(location.hash);
    return m ? Number(m[1]) : null;
  }, [location.hash]);
  const deepLinkHandledRef = useRef<string | null>(null);

  useEffect(() => {
    if (hashEntryId == null) return;
    if (deepLinkHandledRef.current === location.hash) return;

    // 1. Ensure the entry is physically loaded.
    const loaded = plan.entries.some((e) => e.id === hashEntryId);
    if (!loaded) {
      if (hasMoreOnServer && !isLoadingMoreEntries) {
        // Pull the next server page; appended entries re-trigger this effect.
        loadMoreEntries();
      } else if (!hasMoreOnServer) {
        // Fully loaded and still absent — the entry isn't in this plan.
        // Stop trying so we don't loop.
        deepLinkHandledRef.current = location.hash;
      }
      return;
    }

    // 2. Make sure no active filter hides it.  Clearing filters re-runs this
    //    effect (and resets page to 0 via the existing reset effect).
    const inFiltered = filteredEntries.some((e) => e.id === hashEntryId);
    if (!inFiltered) {
      setSearchText('');
      setStatusFilter('all');
      setPriorityFilter('all');
      setPhaseFilter('all');
      return;
    }

    // 3. Page to the entry's client page.
    const idx = filteredEntries.findIndex((e) => e.id === hashEntryId);
    const targetPage = Math.floor(idx / PAGE_SIZE);
    if (safePage !== targetPage) {
      setPage(targetPage);
      return;
    }

    // 4. On the right page — expand, scroll, focus once.
    setExpandedEntries((prev) => {
      if (prev.has(hashEntryId)) return prev;
      const next = new Set(prev);
      next.add(hashEntryId);
      return next;
    });
    deepLinkHandledRef.current = location.hash;
    // Defer to the next frame so the (now-expanded) row has rendered.
    requestAnimationFrame(() => {
      const el = document.getElementById(`entry-${hashEntryId}`);
      if (!el) return;
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      // Focus for keyboard users; the row isn't normally focusable.
      el.setAttribute('tabindex', '-1');
      (el as HTMLElement).focus({ preventScroll: true });
    });
  }, [
    hashEntryId, location.hash, plan.entries, filteredEntries, safePage,
    hasMoreOnServer, isLoadingMoreEntries, loadMoreEntries,
  ]);

  // "Visible" = the current page (expand-all over thousands of entries is
  // the very thing pagination guards against).
  const allVisibleExpanded =
    pagedEntries.length > 0 && pagedEntries.every((e) => expandedEntries.has(e.id));

  const toggleEntry = (id: number) => {
    setExpandedEntries((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSort = (column: SortColumn) => {
    if (sortColumn === column) {
      setSortDirection((p) => (p === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortColumn(column);
    setSortDirection(column === 'tests' ? 'desc' : 'asc');
  };

  const clearFilters = () => {
    setSearchText('');
    setStatusFilter('all');
    setPriorityFilter('all');
    setPhaseFilter('all');
  };

  const handleToggleVisibleEntries = () => {
    setExpandedEntries((prev) => {
      const next = new Set(prev);
      if (allVisibleExpanded) pagedEntries.forEach((e) => next.delete(e.id));
      else pagedEntries.forEach((e) => next.add(e.id));
      return next;
    });
  };

  const handleEntryStatusChange = async (entry: TestPlanEntryResponse, newStatus: string) => {
    try {
      await updateTestPlanEntry(planId, entry.id, {
        status: newStatus,
        expected_updated_at: entry.updated_at,
      });
      await reload();
      // Toast `id` so repeat status changes update the existing toast
      // in place instead of stacking N copies in a bulk-edit
      // workflow (audit M12).
      toast.success(`Entry status set to ${newStatus.replace('_', ' ')}`, {
        id: `entry-status-${entry.id}`,
        autoHideMs: 2000,
      });
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update entry.'), {
        id: `entry-status-${entry.id}-err`,
      });
    }
  };

  const sortIcon = (column: SortColumn) => {
    if (sortColumn !== column) return <ArrowUpDown className="size-3 opacity-50" aria-hidden />;
    return sortDirection === 'asc' ? (
      <ArrowUp className="size-3" aria-hidden />
    ) : (
      <ArrowDown className="size-3" aria-hidden />
    );
  };

  const SortHeader: React.FC<{ column: SortColumn; label: string; className?: string }> = ({
    column,
    label,
    className,
  }) => {
    const isSorted = sortColumn === column;
    const ariaSort: React.AriaAttributes['aria-sort'] = isSorted
      ? sortDirection === 'asc' ? 'ascending' : 'descending'
      : 'none';
    const sortStateLabel = isSorted
      ? sortDirection === 'asc' ? 'sorted ascending' : 'sorted descending'
      : 'not sorted';
    // aria-sort must live on the <th>; the button is just the toggle.
    return (
      <TableHead className={className} aria-sort={ariaSort}>
        <button
          type="button"
          onClick={() => handleSort(column)}
          aria-label={`Sort by ${label}, currently ${sortStateLabel}`}
          className="inline-flex items-center gap-xxs rounded-control text-inherit hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {label}
          {sortIcon(column)}
        </button>
      </TableHead>
    );
  };

  const renderExpandedContent = (entry: TestPlanEntryResponse) => (
    <div className="grid grid-cols-1 gap-md md:grid-cols-2">
      <div>
        <p className="mb-xxs text-metadata font-semibold">Rationale</p>
        <p className="whitespace-pre-wrap break-words text-metadata">{entry.rationale}</p>
      </div>

      <div className="md:col-span-2">
        <p className="mb-xxs text-metadata font-semibold">
          Proposed Tests ({(entry.proposed_tests || []).length})
        </p>
        {(entry.proposed_tests || []).length === 0 ? (
          <p className="text-metadata text-muted-foreground">None specified</p>
        ) : (
          <div className="flex flex-col gap-xs">
            {(entry.proposed_tests || []).map((test, i) =>
              isStructuredTest(test) ? (
                <StructuredTestCard key={i} test={test} hostIp={entry.host_ip} />
              ) : (
                <Badge key={i} variant="outline" className="self-start">
                  {test}
                </Badge>
              ),
            )}
          </div>
        )}
      </div>

      {entry.findings && (
        <div>
          <p className="mb-xxs text-metadata font-semibold">Findings</p>
          <p className="whitespace-pre-wrap break-words text-metadata">{entry.findings}</p>
        </div>
      )}

      {entry.notes && (
        <div>
          <p className="mb-xxs text-metadata font-semibold">Notes</p>
          <p className="whitespace-pre-wrap break-words text-metadata">{entry.notes}</p>
        </div>
      )}

      <div className="md:col-span-2">
        <div className="flex flex-wrap items-center gap-md">
          {entry.started_at && (
            <span className="text-caption text-muted-foreground">
              Started: {new Date(entry.started_at).toLocaleString()}
            </span>
          )}
          {entry.completed_at && (
            <span className="text-caption text-muted-foreground">
              Completed: {new Date(entry.completed_at).toLocaleString()}
            </span>
          )}
          <span className="text-caption text-muted-foreground">
            Created: {new Date(entry.created_at).toLocaleString()}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="md:ml-auto"
            onClick={() => navigate(`/hosts/${entry.host_id}`)}
          >
            View Host Detail
            <ExternalLink className="size-3" aria-hidden />
          </Button>
        </div>
      </div>

      {(plan.status === 'in_progress' || plan.status === 'completed') && (
        <div className="md:col-span-2">
          <EntryResultsPanel
            planId={planId}
            entryId={entry.id}
            sessionId={selectedSessionId}
            proposedTests={entry.proposed_tests}
          />
        </div>
      )}
    </div>
  );

  return (
    <>
      {progress && progress.total_entries > 0 && (
        <div className="mb-sm grid grid-cols-1 gap-sm sm:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardContent className="p-sm text-center">
              <p className="text-page-title font-semibold">{progress.total_entries}</p>
              <p className="text-metadata text-muted-foreground">Total Entries</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-sm text-center">
              <p className="text-page-title font-semibold">{progress.hosts_tested}</p>
              <p className="text-metadata text-muted-foreground">Tested</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-sm text-center">
              <p className="text-page-title font-semibold">{progress.hosts_remaining}</p>
              <p className="text-metadata text-muted-foreground">Remaining</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-sm text-center">
              <div
                className="mb-xxs h-2 w-full overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(progress.completion_pct)}
                aria-valuetext={`${Math.round(progress.completion_pct)} percent of plan execution complete`}
              >
                <div
                  className="h-full bg-primary transition-all"
                  style={{ width: `${progress.completion_pct}%` }}
                />
              </div>
              <p className="text-subheading font-semibold">{progress.completion_pct.toFixed(0)}%</p>
              <p className="text-metadata text-muted-foreground">Execution Progress</p>
            </CardContent>
          </Card>

          <div className="sm:col-span-2 lg:col-span-4">
            <div className="flex flex-wrap gap-xs">
              {Object.entries(progress.by_status).map(([status, count]) => (
                <Badge key={status} variant={entryStatusTone(status)}>
                  {formatStatusLabel(status)}: {count}
                </Badge>
              ))}
              {Object.entries(progress.by_priority).map(([priority, count]) => (
                <Badge key={`p-${priority}`} variant={priorityTone(priority)}>
                  {priority}: {count}
                </Badge>
              ))}
            </div>
          </div>
        </div>
      )}

      <Card className="mb-sm">
        <CardContent className="flex flex-col gap-sm p-sm lg:flex-row lg:items-end">
          <div className="flex-1">
            <Label htmlFor="entry-search">Search entries</Label>
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-xs top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                id="entry-search"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                placeholder="Host, tool, rationale, findings, notes"
                className="pl-lg"
              />
            </div>
          </div>
          <div className="min-w-44">
            <Label htmlFor="entry-status">Status</Label>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger id="entry-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                {ENTRY_STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {formatStatusLabel(s)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="min-w-44">
            <Label htmlFor="entry-priority">Priority</Label>
            <Select value={priorityFilter} onValueChange={setPriorityFilter}>
              <SelectTrigger id="entry-priority">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Priorities</SelectItem>
                {Object.keys(PRIORITY_ORDER).map((p) => (
                  <SelectItem key={p} value={p}>
                    {formatStatusLabel(p)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="min-w-52">
            <Label htmlFor="entry-phase">Phase</Label>
            <Select value={phaseFilter} onValueChange={setPhaseFilter}>
              <SelectTrigger id="entry-phase">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Phases</SelectItem>
                {Object.keys(PHASE_ORDER).map((p) => (
                  <SelectItem key={p} value={p}>
                    {formatStatusLabel(p)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-wrap items-center gap-xs lg:ml-auto">
            <Badge variant={hasActiveFilters ? 'default' : 'outline'}>{sortedEntryCountLabel}</Badge>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleToggleVisibleEntries}
              disabled={pagedEntries.length === 0}
            >
              {allVisibleExpanded ? 'Collapse page' : 'Expand page'}
            </Button>
            <Button variant="ghost" size="sm" onClick={clearFilters} disabled={!hasActiveFilters}>
              <X className="size-4" aria-hidden /> Clear
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Desktop / tablet table view (md and up).  Below md the table
          becomes unreadable at 8 columns — even with overflow-x-auto
          the horizontal scroll is unusable on a phone.  The mobile
          card variant below replaces it under md. */}
      <Card className="hidden md:block">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10" />
                  <SortHeader column="host" label="Host" className="w-[14%]" />
                  <SortHeader column="priority" label="Priority" className="w-[8%]" />
                  <SortHeader column="phase" label="Phase" className="w-[10%]" />
                  <SortHeader column="tests" label="Proposed Tests" className="w-[22%]" />
                  <SortHeader column="status" label="Status" className="w-[12%]" />
                  <SortHeader column="rationale" label="Rationale" className="w-[20%]" />
                  <SortHeader column="findings" label="Findings" className="w-[14%]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {plan.entries.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="py-md text-center text-metadata text-muted-foreground">
                      No entries in this test plan yet.
                    </TableCell>
                  </TableRow>
                ) : filteredEntries.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="py-md text-center text-metadata text-muted-foreground">
                      {noFilteredEntriesMessage}
                    </TableCell>
                  </TableRow>
                ) : (
                  pagedEntries.map((entry) => {
                    const isExpanded = expandedEntries.has(entry.id);
                    return (
                      <React.Fragment key={entry.id}>
                        {/* Anchor for #entry-{id} deep-links from My Work.
                            Native jump works when the entry is on the visible
                            page; a follow-up will flip the (two-level paged)
                            list to the entry's page + scroll. */}
                        <TableRow id={`entry-${entry.id}`}>
                          <TableCell>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => toggleEntry(entry.id)}
                              aria-label={
                                isExpanded
                                  ? `Collapse entry for ${entry.host_ip || entry.host_id}`
                                  : `Expand entry for ${entry.host_ip || entry.host_id}`
                              }
                              aria-expanded={isExpanded}
                            >
                              {isExpanded ? (
                                <ChevronUp className="size-4" aria-hidden />
                              ) : (
                                <ChevronDown className="size-4" aria-hidden />
                              )}
                            </Button>
                          </TableCell>
                          <TableCell>
                            <button
                              type="button"
                              onClick={() => navigate(`/hosts/${entry.host_id}`)}
                              className="block w-full truncate text-left font-semibold text-primary hover:underline focus:outline-none focus-visible:underline"
                            >
                              {entry.host_ip || entry.host_id}
                            </button>
                            {entry.host_hostname && (
                              <p className="truncate text-caption text-muted-foreground">
                                {entry.host_hostname}
                              </p>
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant={priorityTone(entry.priority)} className="whitespace-nowrap">
                              {entry.priority}
                            </Badge>
                          </TableCell>
                          <TableCell className="truncate">
                            {formatStatusLabel(entry.test_phase)}
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-wrap gap-xxs">
                              {(entry.proposed_tests || []).slice(0, 3).map((t, i) => (
                                <Badge key={i} variant="outline">
                                  {getTestChipLabel(t)}
                                </Badge>
                              ))}
                              {(entry.proposed_tests || []).length > 3 && (
                                <Badge variant="muted">
                                  +{entry.proposed_tests.length - 3}
                                </Badge>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            {canManage ? (
                              <Select
                                value={entry.status}
                                onValueChange={(v) => handleEntryStatusChange(entry, v)}
                              >
                                <SelectTrigger
                                  aria-label={`Status for entry on host ${entry.host_ip || entry.host_id}`}
                                  className="h-7"
                                >
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {ENTRY_STATUSES.map((s) => (
                                    <SelectItem key={s} value={s}>
                                      {formatStatusLabel(s)}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            ) : (
                              <Badge variant={entryStatusTone(entry.status)} className="whitespace-nowrap">
                                {formatStatusLabel(entry.status)}
                              </Badge>
                            )}
                          </TableCell>
                          {/* Audit RSP·H6 — line-clamp doesn't apply
                              to display: table-cell, so wrap the text
                              in a block <p> for the clamp to take. */}
                          <TableCell>
                            <p className="line-clamp-2 text-metadata">
                              {stripAttribution(entry.rationale)}
                            </p>
                          </TableCell>
                          <TableCell>
                            <p className="line-clamp-2 text-metadata">
                              {entry.findings ? stripAttribution(entry.findings) : '-'}
                            </p>
                          </TableCell>
                        </TableRow>
                        {isExpanded && (
                          <TableRow>
                            <TableCell colSpan={8} className="bg-accent p-md">
                              {renderExpandedContent(entry)}
                            </TableCell>
                          </TableRow>
                        )}
                      </React.Fragment>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Mobile card list (below md).  Each entry becomes a stacked
          card with the same affordances as the table row: chevron to
          expand, host link, status select (or badge), priority +
          phase chips, proposed-tests chips, clamped rationale +
          findings.  Expansion reuses renderExpandedContent so the
          desktop and mobile expanded bodies stay in sync. */}
      <div className="flex flex-col gap-sm md:hidden">
        {plan.entries.length === 0 ? (
          <Card>
            <CardContent className="p-md text-center text-metadata text-muted-foreground">
              No entries in this test plan yet.
            </CardContent>
          </Card>
        ) : filteredEntries.length === 0 ? (
          <Card>
            <CardContent className="p-md text-center text-metadata text-muted-foreground">
              {noFilteredEntriesMessage}
            </CardContent>
          </Card>
        ) : (
          pagedEntries.map((entry) => {
            const isExpanded = expandedEntries.has(entry.id);
            const proposed = entry.proposed_tests || [];
            return (
              <Card key={entry.id}>
                <CardContent className="flex flex-col gap-xs p-sm">
                  {/* Row 1: host (truncating) + expand toggle */}
                  <div className="flex items-start gap-xs">
                    <div className="min-w-0 flex-1">
                      <button
                        type="button"
                        onClick={() => navigate(`/hosts/${entry.host_id}`)}
                        className="block w-full truncate text-left font-semibold text-primary hover:underline focus:outline-none focus-visible:underline"
                      >
                        {entry.host_ip || entry.host_id}
                      </button>
                      {entry.host_hostname && (
                        <p className="truncate text-caption text-muted-foreground">
                          {entry.host_hostname}
                        </p>
                      )}
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => toggleEntry(entry.id)}
                      aria-label={
                        isExpanded
                          ? `Collapse entry for ${entry.host_ip || entry.host_id}`
                          : `Expand entry for ${entry.host_ip || entry.host_id}`
                      }
                      aria-expanded={isExpanded}
                    >
                      {isExpanded ? (
                        <ChevronUp className="size-4" aria-hidden />
                      ) : (
                        <ChevronDown className="size-4" aria-hidden />
                      )}
                    </Button>
                  </div>

                  {/* Row 2: priority + phase + status — three small chips
                      that always fit on one phone line.  Status uses
                      the same select control as the desktop table when
                      the user can manage. */}
                  <div className="flex flex-wrap items-center gap-xs">
                    <Badge variant={priorityTone(entry.priority)}>{entry.priority}</Badge>
                    <Badge variant="outline">{formatStatusLabel(entry.test_phase)}</Badge>
                    <div className="ml-auto">
                      {canManage ? (
                        <Select
                          value={entry.status}
                          onValueChange={(v) => handleEntryStatusChange(entry, v)}
                        >
                          <SelectTrigger
                            aria-label={`Status for entry on host ${entry.host_ip || entry.host_id}`}
                            className="h-7 min-w-32"
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {ENTRY_STATUSES.map((s) => (
                              <SelectItem key={s} value={s}>
                                {formatStatusLabel(s)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : (
                        <Badge variant={entryStatusTone(entry.status)}>
                          {formatStatusLabel(entry.status)}
                        </Badge>
                      )}
                    </div>
                  </div>

                  {/* Row 3: proposed test chips (capped at 3 + overflow
                      pill) — same shape as the desktop column. */}
                  {proposed.length > 0 && (
                    <div className="flex flex-wrap gap-xxs">
                      {proposed.slice(0, 3).map((t, i) => (
                        <Badge key={i} variant="outline" className="max-w-full truncate">
                          {getTestChipLabel(t)}
                        </Badge>
                      ))}
                      {proposed.length > 3 && (
                        <Badge variant="muted">+{proposed.length - 3}</Badge>
                      )}
                    </div>
                  )}

                  {/* Row 4: clamped rationale (labelled).  Hidden when
                      empty so the card stays compact on minimal entries. */}
                  {entry.rationale && (
                    <div>
                      <p className="text-caption font-semibold text-muted-foreground">
                        Rationale
                      </p>
                      <p className="line-clamp-2 break-words text-metadata">
                        {stripAttribution(entry.rationale)}
                      </p>
                    </div>
                  )}

                  {/* Row 5: clamped findings — only shown when present
                      so reviewers spot the difference at a glance. */}
                  {entry.findings && (
                    <div>
                      <p className="text-caption font-semibold text-muted-foreground">
                        Findings
                      </p>
                      <p className="line-clamp-2 break-words text-metadata">
                        {stripAttribution(entry.findings)}
                      </p>
                    </div>
                  )}

                  {isExpanded && (
                    <div className="mt-xs border-t pt-sm">
                      {renderExpandedContent(entry)}
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })
        )}
      </div>

      {/* Pager — only shown when the filtered set spans more than one
          page. Keeps a multi-thousand-entry plan from rendering every
          row (and every expandable detail body) at once. */}
      {filteredEntries.length > PAGE_SIZE && (
        <div className="mt-sm flex flex-wrap items-center justify-between gap-sm">
          <p className="text-caption text-muted-foreground">
            Showing {safePage * PAGE_SIZE + 1}–
            {Math.min((safePage + 1) * PAGE_SIZE, filteredEntries.length)} of{' '}
            {filteredEntries.length}
          </p>
          <div className="flex items-center gap-xs">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={safePage === 0}
            >
              Previous
            </Button>
            <span className="text-caption text-muted-foreground">
              Page {safePage + 1} of {pageCount}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={safePage >= pageCount - 1}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* v2.85.0 — server-side load-more.  Distinct from the client
          paginator above: that one walks the slice already in memory,
          this one fetches the next page from the backend.  Only renders
          when entries_total > entries currently loaded, so plans that
          fit in one server page hide it entirely. */}
      {hasMoreOnServer && (
        <div className="mt-md flex flex-col items-center gap-xs">
          <Button
            variant="outline"
            size="sm"
            onClick={loadMoreEntries}
            disabled={isLoadingMoreEntries}
          >
            {isLoadingMoreEntries
              ? 'Loading…'
              : `Load more (${totalEntries - serverLoadedCount} remaining)`}
          </Button>
          <p className="text-caption text-muted-foreground">
            Showing {serverLoadedCount} of {totalEntries} entries from the server.
          </p>
        </div>
      )}
    </>
  );
};

export default PlanTab;
