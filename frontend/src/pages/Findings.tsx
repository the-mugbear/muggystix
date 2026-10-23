/**
 * /findings — the unified findings view (foundation phase 6).
 *
 * The project's findings — promoted scanner observations, findings made
 * from a test's result and findings written directly — in one list,
 * filterable by status, severity, source and owner.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { findingDetailHref } from '../utils/findingsReturn';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, AlertTriangle, ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';

import {
  Finding,
  FindingFilters,
  FindingHostStatus,
  FindingNeedsFilter,
  FindingListResponse,
  FindingSortField,
  FindingSeverity,
  FindingSource,
  FindingStatus,
  FindingStatusQuery,
  bulkAssignFindings,
  bulkSetFindingStatus,
  listFindings,
  setFindingStatus,
} from '../services/api';
import { useProjectRoster } from '../hooks/useProjectMembers';
import MembersLoadError from '../components/MembersLoadError';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useProjectId } from '../hooks/useProjectId';
import { useProjectRole } from '../hooks/useProjectRole';
import { useConfirm } from '../hooks/useConfirm';
import { formatApiError } from '../utils/apiErrors';
import { invalidateReads } from '../lib/query';
import { useListCursor } from '../hooks/useListCursor';
import { usePagedList } from '../hooks/usePagedList';
import { useUrlPage } from '../hooks/useUrlPage';
import { useUrlSearchDraft } from '../hooks/useUrlSearchDraft';
import { SeverityBadge } from '../components/ui/SeverityBadge';
import ListFilterBar, { FILTER_TRIGGER_CLASS, ListFilterSearch } from '../components/ListFilterBar';
import { formatTimestamp } from '../utils/relativeTime';
import SeverityBar from '../components/ui/SeverityBar';
import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import { DataTablePagination } from '../components/ui/data-table';
import { StatusReasonDialog, type StatusReasonRequest } from '../components/findings/StatusReasonDialog';
import { InfoTip } from '../components/ui/info-tip';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { safeFallback } from '../utils/uiStyles';
import { cn } from '../utils/cn';

import ProposedFindingsLine from '../components/findings/ProposedFindingsLine';
import { OBSERVATIONS_TO_JUDGE_PATH, observationsPathFromFindingsView } from '../utils/observationsPath';
import {
  HOST_STATE_FILTER_LABEL, SOURCE_LABEL, SOURCE_OPTIONS, STATUS_LABEL, TERMINAL_STATUSES, describeEndpointStates,
  matchesStatusFilter,
} from '../utils/findingStatus';
import { ENDPOINT_STATES, endpointPreviewTitle } from '../utils/findingEndpoints';
import { NEED_FILTER_OPTIONS, NEED_SECTION, needsFilterOf, rowNeeds } from '../utils/findingNeeds';
import { selectAllState } from '../utils/selection';

// Compact age ("31d") from an ISO timestamp — the full date goes in the
// cell's title. Falls back safely on a missing/invalid value rather than
// rendering "Invalid Date"; a future timestamp (clock skew) reads "now".
/** The list read's failure when the server gave no reason of its own. */
const LOAD_FAILED = 'Failed to load findings.';

const compactAge =(iso: string | null | undefined, now: number = Date.now()): string => {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const mins = Math.floor((now - t) / 60_000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 365) return `${days}d`;
  return `${Math.floor(days / 365)}y`;
};

type SummaryPrompt =
  | { kind: 'single'; findingId: number; status: FindingStatus; title: string }
  // `offscreen` = how many of `ids` aren't on the visible page, so the dialog
  // can state the real blast radius instead of a bare count.
  | { kind: 'bulk'; status: FindingStatus; ids: number[]; offscreen: number };

type StatusFilterValue = FindingStatusQuery | 'all';
/** `?owner=`: `me`, `unowned`, or one person's user id (5.356.0 — the filter
 *  offered only the reader and "nobody", so findings promoted by a teammate,
 *  who owns what they promote, could not be listed).  Anything else is "any". */
type OwnerFilterValue = 'any' | 'me' | 'unowned' | `${number}`;
const ownerFilterOf = (raw: string | null, myId: number | null | undefined): OwnerFilterValue => {
  if (raw === 'me' || raw === 'unowned') return raw;
  if (raw && /^[1-9]\d*$/.test(raw)) return Number(raw) === myId ? 'me' : (raw as `${number}`);
  return 'any';
};

/**
 * The project's findings — the judged record reports are built from.
 *
 * 5.404.0 — the scanner observations it used to hold as a second view are an
 * Inventory page of their own (`/observations`, with an inspector); an old
 * `?view=observations` link lands there, narrowed to what is not yet judged
 * (what that view listed).
 */
const Findings: React.FC = () => {
  const [searchParams] = useSearchParams();
  if (searchParams.get('view') === 'observations') {
    return <Navigate to={observationsPathFromFindingsView(searchParams)} replace />;
  }
  return (
    <div className="p-md md:p-lg">
      <div className="mb-md">
        <h1 className="text-page-title font-semibold">Findings</h1>
        {/* v5.267.0 — one line; the vocabulary (v5.225.0, the three
            populations named the same way everywhere) sits on the (i). */}
        <p className="flex flex-wrap items-center gap-xxs text-metadata text-muted-foreground">
          The issues triaged in this project — the record reports are built from.
          <InfoTip
            label="What the statuses mean"
            text={
              <>
                Scanner observations are what the scanners reported, not yet judged (Inventory → Scanner
                observations); promote one to make it a finding. Open is under investigation; Confirmed is validated; False positive, Accepted risk and
                Remediated are closed. A finding&apos;s status is the issue&apos;s; each affected host keeps its own
                state on the finding page — still present, awaiting a retest, remediated or false positive — and
                the Hosts filter lists the findings with a host in one of them.
              </>
            }
          />
        </p>
        <ProposedFindingsLine />
      </div>
      <FindingsList />
    </div>
  );
};

const FindingsList: React.FC = () => {
  const toast = useToast();
  const projectId = useProjectId();
  const navigate = useNavigate();
  const { user } = useAuth();
  // Viewers may read findings but not dispose/select; analyst+ may triage.
  const { canWrite: canManage } = useProjectRole();
  const [confirmDialog, confirm] = useConfirm();
  const queryClient = useQueryClient();

  // Filters are URL-backed so a dashboard drill-down (e.g. "active critical,
  // unowned") is shareable/bookmarkable and the page restores it. Default
  // status is 'active' — terminal findings shouldn't bury current work
  // (§15). An absent param means the default, so a clean URL stays clean.
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = (searchParams.get('status') as StatusFilterValue | null) ?? 'active';
  const severityFilter = (searchParams.get('severity') as FindingSeverity | null) ?? 'all';
  const sourceFilter = (searchParams.get('source') as FindingSource | null) ?? 'all';
  const ownerFilter = ownerFilterOf(searchParams.get('owner'), user?.id);
  // What a finding still needs before the report (`utils/findingNeeds`).
  const needsFilter = needsFilterOf(searchParams.get('needs'));
  // A host of the finding in this state (5.395.0): `retest` is the project's
  // list of pending retests, everyone's (owner, 2026-10-14).
  const hostStateParam = searchParams.get('host_state');
  const hostStateFilter: FindingHostStatus | 'all' =
    (ENDPOINT_STATES as string[]).includes(hostStateParam ?? '') ? (hostStateParam as FindingHostStatus) : 'all';
  // The search is the address's too (`search.value`, what the list is asked
  // for); the box shows `search.draft`, which is committed a moment after the
  // typing stops — replacing the entry, from the first page — and follows the
  // address when it changes from elsewhere (Back, a link).
  const search = useUrlSearchDraft('search');
  const searchValue = search.value;

  // One writer for every URL-backed param: a default value clears the key so
  // a clean URL stays clean. Filter keys (membership) also reset `page`, so
  // we never sit on an out-of-range page after the result set shrinks —
  // done here rather than in an effect so a deep link to page 3 survives
  // mount.
  const setUrlParam = useCallback(
    (key: string, value: string, defaultValue: string, resetsPage = false) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value === defaultValue) next.delete(key);
        else next.set(key, value);
        if (resetsPage) next.delete('page');
        return next;
      }, { replace: true });
    },
    [setSearchParams],
  );
  const setFilterParam = useCallback(
    (key: string, value: string, defaultValue: string) => setUrlParam(key, value, defaultValue, true),
    [setUrlParam],
  );
  const setStatusFilter = (v: StatusFilterValue) => setFilterParam('status', v, 'active');
  const setSeverityFilter = (v: FindingSeverity | 'all') => setFilterParam('severity', v, 'all');
  const setSourceFilter = (v: FindingSource | 'all') => setFilterParam('source', v, 'all');
  const setOwnerFilter = (v: OwnerFilterValue) => setFilterParam('owner', v, 'any');
  const setNeedsFilter = (v: FindingNeedsFilter | 'all') => setFilterParam('needs', v, 'all');
  const setHostStateFilter = (v: FindingHostStatus | 'all') => setFilterParam('host_state', v, 'all');

  // Page + sort live in the URL too (UX review M1): the detail page hands
  // this exact URL back on its "Findings" action, so an analyst working a
  // narrowed, sorted, paged queue lands on the same row set, not page 1 of
  // everything.  The page is `usePagedList`'s, below (`?page=`, read by the
  // one reading of a page in an address): a filter, sort or page-size control
  // starts from the first page by leaving `page` out of its ONE address write.
  const pageSize = (() => {
    const n = Number(searchParams.get('page_size'));
    return [25, 50, 100, 200].includes(n) ? n : 50;
  })();
  // Column sort (server-side — sorting only the current page would mislead
  // under pagination). With no sort in the URL the list is worst first, then
  // newest (v5.288.0) — newest-first alone interleaved the severities.  The
  // server's severity sort breaks ties by id descending, i.e. newest.
  const sortBy: FindingSortField = (searchParams.get('sort') as FindingSortField | null) ?? 'severity';
  const sortDir: 'asc' | 'desc' = searchParams.get('dir') === 'desc' ? 'desc' : 'asc';
  const setPageSize = useCallback((n: number) => setUrlParam('page_size', String(n), '50', true), [setUrlParam]);
  const setSort = useCallback((field: FindingSortField | null, dir: 'asc' | 'desc') => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (field) { next.set('sort', field); next.set('dir', dir); } else { next.delete('sort'); next.delete('dir'); }
      next.delete('page');
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  // Bulk triage — selected finding ids. Survives sort + pagination (see the
  // membership guard below), so this set can span pages.
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [summaryPrompt, setSummaryPrompt] = useState<SummaryPrompt | null>(null);
  // What the one "why?" dialog asks — one object per opening (its key).
  const summaryRequest = useMemo<StatusReasonRequest | null>(() => (
    summaryPrompt === null
      ? null
      : summaryPrompt.kind === 'bulk'
        ? { status: summaryPrompt.status, bulk: { count: summaryPrompt.ids.length, offscreen: summaryPrompt.offscreen } }
        : { status: summaryPrompt.status }
  ), [summaryPrompt]);
  // Project roster for bulk owner assignment. Failure is non-fatal — the
  // status controls keep working, and the assign menu says it could not load.
  const roster = useProjectRoster();
  const members = roster.members;

  const hasActiveFilters = statusFilter !== 'all' || severityFilter !== 'all'
    || sourceFilter !== 'all' || ownerFilter !== 'any' || needsFilter !== 'all'
    || hostStateFilter !== 'all' || searchValue !== '';
  // The page opens on status 'active', so `hasActiveFilters` is true on a
  // clean URL. The empty state must not blame filters the analyst never set.
  const onlyDefaultFilter = statusFilter === 'active' && severityFilter === 'all'
    && sourceFilter === 'all' && ownerFilter === 'any' && needsFilter === 'all'
    && hostStateFilter === 'all' && searchValue === '';

  // Bulk-selection safety — mirrors the guard in Hosts.tsx. The selected set is
  // only meaningful for the result set it was made against: a filter change
  // swaps which findings exist, so keeping the selection would let a bulk
  // disposition act on rows the operator can no longer see (and never chose
  // under the new filter). Sort and pagination don't change membership, so
  // they're deliberately excluded and selection survives them — the same
  // convention Hosts uses.
  const membershipSignature = useMemo(
    () => JSON.stringify([statusFilter, severityFilter, sourceFilter, ownerFilter, needsFilter, hostStateFilter, searchValue]),
    [statusFilter, severityFilter, sourceFilter, ownerFilter, needsFilter, hostStateFilter, searchValue],
  );
  const prevMembership = useRef<string | null>(null);
  useEffect(() => {
    if (prevMembership.current === null) {
      prevMembership.current = membershipSignature; // seed; don't clear on mount
      return;
    }
    if (prevMembership.current !== membershipSignature) {
      prevMembership.current = membershipSignature;
      setSelected(new Set());
    }
  }, [membershipSignature]);

  // What the list is asked for, without the page (the list hook's).
  const filters = useMemo<FindingFilters>(() => {
    const f: FindingFilters = {};
    if (statusFilter !== 'all') f.status = statusFilter;
    if (severityFilter !== 'all') f.severity = severityFilter;
    if (sourceFilter !== 'all') f.source = sourceFilter;
    if (ownerFilter === 'unowned') f.unowned = true;
    else if (ownerFilter === 'me') { if (user?.id != null) f.owner_id = user.id; }
    else if (ownerFilter !== 'any') f.owner_id = Number(ownerFilter);
    if (needsFilter !== 'all') f.needs = needsFilter;
    if (hostStateFilter !== 'all') f.host_state = hostStateFilter;
    if (searchValue) f.search = searchValue;
    if (sortBy) { f.sort = sortBy; f.dir = sortDir; }
    return f;
  }, [statusFilter, severityFilter, sourceFilter, ownerFilter, needsFilter, hostStateFilter, searchValue,
    user?.id, sortBy, sortDir]);

  // Per-field default direction (worst/most-relevant first); a repeat click
  // toggles. Mirrors the backend's per-field default.
  const SORT_DEFAULT_DIR: Record<FindingSortField, 'asc' | 'desc'> = {
    severity: 'asc', host_count: 'desc', title: 'asc', status: 'asc', source: 'asc', created_at: 'desc', owner: 'asc',
  };
  const handleSort = (field: FindingSortField) => {
    if (sortBy === field) setSort(field, sortDir === 'asc' ? 'desc' : 'asc');
    else setSort(field, SORT_DEFAULT_DIR[field]);
  };
  const SortHead: React.FC<{ field: FindingSortField; label: string; className?: string }> = ({ field, label, className }) => (
    <TableHead className={className}
      aria-sort={sortBy === field ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" onClick={() => handleSort(field)}
        className="inline-flex items-center gap-xxs rounded text-inherit hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {label}
        {sortBy === field
          ? (sortDir === 'asc' ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />)
          : <ArrowUpDown className="size-3 opacity-40" aria-hidden />}
      </button>
    </TableHead>
  );

  // The filters are the query's key: a slow response for filter set A can
  // never land under B's active filters, nor can A's failure replace B's
  // error state.  The previous filter's (or page's) rows stay on screen,
  // dimmed, while the next ones load — `previousRows` — so the table, the
  // selection's "not on this page" count and the pagination do not blink.
  // They are not this filter's rows: nothing on them can be ticked or
  // changed, and the keyboard cursor does not open one.
  //
  // One page at a time on `usePagedList`, its page the ADDRESS's
  // (`keepAddressPage`: a link or Back names a filter and a page together and
  // opens that page).  The hook owns "a page past the end steps back to the
  // last one that exists" — the page read `?page=` by hand and had no such
  // step, so closing the only finding of the last page, or an old link to
  // `?page=9`, said "No active findings" over findings that exist (review
  // 2026-10-10, H15).
  const list = usePagedList<Finding, FindingListResponse>(
    'listFindings',
    ({ offset, limit, signal }) => listFindings(projectId, { ...filters, limit, offset }, signal),
    [projectId, filters],
    { pageSize, page: useUrlPage(), keepAddressPage: true, within: projectId, errorMessage: LOAD_FAILED },
  );
  const { page, setPage } = list;
  /** This address's own answer; `null` until it is in. */
  const ownAnswer = list.response;
  // The rows of the filter or page before, while this one's first read is out.
  const previousRows = ownAnswer === null && list.error === null && list.lastResponse !== null;
  const answer = ownAnswer ?? (previousRows ? list.lastResponse : null);
  // A row whose status was just changed carries the change's answer until the
  // list is read again; one that left the status filter by it is not shown
  // (`matchesStatusFilter` is the server's rule), so the view stays truthful.
  const findings = useMemo<Finding[]>(() => {
    if (ownAnswer) return ownAnswer.items.filter((f) => matchesStatusFilter(f.status, statusFilter));
    return answer?.items ?? [];
  }, [ownAnswer, answer, statusFilter]);
  // The Owner filter offers the people who OWN one of the listed findings,
  // each with how many (5.357.0) — the server's `owner_counts`, taken under
  // every filter but the owner, so each number is the size of the list its
  // choice opens and everyone stays listed while one is chosen.  (It first
  // offered every project member: on a real project that is mostly people
  // who own nothing.)  The counts of "Assigned to me" and "Unowned" come from
  // the same answer; `null` = not known (still loading, or an older server).
  const ownerCounts = answer?.owner_counts ?? null;
  const countOf = (ownerId: number | null): number | null => (
    ownerCounts ? ownerCounts.find((c) => c.owner_id === ownerId)?.count ?? 0 : null
  );
  const withCount = (label: string, n: number | null) => (n == null ? label : `${label} (${n.toLocaleString()})`);
  const ownerOptions = useMemo(() => {
    const options = (ownerCounts ?? [])
      .filter((c) => c.owner_id != null && c.owner_id !== user?.id)
      .map((c) => ({
        value: String(c.owner_id),
        label: `${c.owner_name || `User ${c.owner_id}`} (${c.count.toLocaleString()})`,
      }));
    // The owner the address names stays offered when nothing listed is theirs
    // (another filter left them none) — named from the roster — so the select
    // is never blank over a filtered list.
    if (ownerFilter !== 'any' && ownerFilter !== 'me' && ownerFilter !== 'unowned'
      && !options.some((o) => o.value === ownerFilter)) {
      const member = members.find((m) => String(m.user_id) === ownerFilter);
      const name = member?.full_name || member?.username
        || findings.find((f) => String(f.owner_id) === ownerFilter)?.owner_name || `User ${ownerFilter}`;
      options.push({ value: ownerFilter, label: ownerCounts ? `${name} (0)` : name });
    }
    return options;
  }, [ownerCounts, members, user?.id, ownerFilter, findings]);
  const total = answer?.total ?? 0;
  const sevCounts: Partial<Record<FindingSeverity, number>> = answer?.severity_counts ?? {};
  // This filter's answer is not here yet (the first load, a new filter), or a
  // failed load is being asked for again.  A re-read after a change is
  // neither: the rows on screen are this filter's and stay as they are.
  // A failed read is one of two things (owner decision 2026-10-10).  With no
  // rows of THIS filter (the first load, a new filter) it is a failed LOAD:
  // the table says so and shows nothing — "a failed Findings load must not
  // read 0 findings".  With this filter's rows already on screen (the re-read
  // after a change, which keeps `data` beside `error`) it is a failed REFRESH:
  // the rows stay, usable, and a line above them says they may be out of date.
  const ownRows = ownAnswer !== null;
  const readFailure = list.error;
  const loading = !ownRows && (readFailure === null || list.loading);
  const error = ownRows || loading ? null : readFailure;
  const refreshError = ownRows && !list.loading ? readFailure : null;
  // A failed load is said in the table and as a toast.
  useEffect(() => { if (error) toast.error(error); }, [error, toast]);
  // The page has no row to show although the list has some: the rows were
  // dealt with and the list is being read again, or the page is past the end
  // and the hook is stepping back.  Neither is an empty list.
  const pageEmptied = findings.length === 0 && total > 0 && (list.loading || page > 0);

  // A change's answer is put onto the rows on screen at once; the list is
  // then read again (`listChanged`), because a row that left the filter also
  // changed the total, the severity counts and what the next page holds.
  const patchFindings = list.mapRows;
  const listChanged = () => { void invalidateReads(queryClient, 'listFindings'); };

  const statusChange = useMutation({
    mutationFn: (v: { findingId: number; status: FindingStatus; title: string; summary?: string }) =>
      setFindingStatus(projectId, v.findingId, v.status, v.summary),
    onSuccess: (updated, { findingId, status, title }) => {
      // A row that no longer matches the status filter leaves the view by
      // `findings`' own rule.
      patchFindings((f) => (f.id === findingId ? updated : f));
      listChanged();
      // The status trail gained a line.
      void queryClient.invalidateQueries({ queryKey: ['getFindingHistory', projectId, findingId] });
      const short = title.length > 40 ? `${title.slice(0, 40)}…` : title;
      toast.success(`${short} → ${STATUS_LABEL[status]}`, { autoHideMs: 2500 });
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to update finding status.')),
  });
  const applyStatus = (findingId: number, status: FindingStatus, title: string, summary?: string) =>
    statusChange.mutate({ findingId, status, title, summary });

  const toggleSelected = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });

  // The actual bulk apply, with one optional rationale applied to every row's
  // history (the terminal-disposition audit trail).
  // v5.135.0 — one request, server-side. Previously this fired N independent
  // PATCHes via Promise.allSettled: unbounded, half-appliable, and with the
  // terminal-justification rule enforced per call rather than per batch.
  const bulkStatus = useMutation({
    mutationFn: (v: { ids: number[]; status: FindingStatus; summary?: string }) =>
      bulkSetFindingStatus(projectId, v.ids, v.status, v.summary),
    onSuccess: (result, { ids, status }) => {
      const changed = new Set(ids.filter((id) => !result.skipped_ids.includes(id)));
      patchFindings((f) => (changed.has(f.id) ? { ...f, status } : f));
      listChanged();
      void queryClient.invalidateQueries({ queryKey: ['getFindingHistory'] });
      setSelected(new Set());
      if (result.skipped_ids.length === 0) {
        toast.success(
          `${result.affected} finding${result.affected === 1 ? '' : 's'} → ${STATUS_LABEL[status]}`,
          { autoHideMs: 2500 },
        );
      } else {
        toast.warning(`${result.affected}/${result.requested} updated; ${result.skipped_ids.length} skipped`);
      }
    },
    onError: (err) => toast.error(formatApiError(err, 'Bulk update failed — no findings were changed.')),
  });
  const runBulk = (ids: number[], status: FindingStatus, summary?: string) =>
    bulkStatus.mutate({ ids, status, summary });

  const bulkAssign = useMutation({
    mutationFn: (v: { ids: number[]; assigneeId: number | null }) => bulkAssignFindings(projectId, v.ids, v.assigneeId),
    onSuccess: (result, { ids, assigneeId }) => {
      const name = assigneeId === null
        ? null
        : members.find((m) => m.user_id === assigneeId)?.username ?? 'user';
      const assigned = new Set(ids);
      patchFindings((f) => (assigned.has(f.id) ? { ...f, owner_id: assigneeId, owner_name: name } : f));
      // Each finding's history gained an "Owner" line.
      void queryClient.invalidateQueries({ queryKey: ['getFindingHistory'] });
      // Under "Assigned to me" / "Unowned" a row may have left the filter.
      listChanged();
      setSelected(new Set());
      toast.success(
        assigneeId === null
          ? `${result.affected} finding${result.affected === 1 ? '' : 's'} unassigned`
          : `${result.affected} finding${result.affected === 1 ? '' : 's'} → ${name}`,
        { autoHideMs: 2500 },
      );
    },
    onError: (err) => toast.error(formatApiError(err, 'Bulk assignment failed — no findings were changed.')),
  });
  const runBulkAssign = (assigneeId: number | null) => {
    const ids = [...selected];
    if (ids.length > 0) bulkAssign.mutate({ ids, assigneeId });
  };
  const bulkApplying = bulkStatus.isPending || bulkAssign.isPending;

  // How many selected findings aren't on the page in front of the operator.
  // Selection intentionally spans pages, so the confirm has to say so — a bare
  // count reads as "these rows here" and hides the real blast radius.
  const offscreenSelectedCount = useMemo(
    () => [...selected].filter((id) => !findings.some((f) => f.id === id)).length,
    [selected, findings],
  );
  const offscreenNote = (n: number) =>
    n > 0
      ? ` ${n} of them ${n === 1 ? 'is' : 'are'} not on this page.`
      : '';

  const applyBulkStatus = async (status: FindingStatus) => {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (TERMINAL_STATUSES.has(status)) {
      // Terminal bulk disposition: collect ONE rationale (applied to every
      // history row) + show the count — same audit discipline as the
      // single-finding flow.
      setTimeout(
        () => setSummaryPrompt({ kind: 'bulk', status, ids, offscreen: offscreenSelectedCount }),
        0,
      );
      return;
    }
    // Non-terminal bulk move: confirm the blast radius before applying.
    const ok = await confirm({
      title: `Set ${ids.length} finding${ids.length === 1 ? '' : 's'} to ${STATUS_LABEL[status]}?`,
      body: `This applies to every selected finding.${offscreenNote(offscreenSelectedCount)}`,
      severity: 'warning',
      confirmLabel: 'Apply',
    });
    if (ok) runBulk(ids, status);
  };

  const handleStatusChange = (findingId: number, status: FindingStatus, title: string) => {
    // Terminal dispositions get a "why" prompt (the summary is the audit
    // rationale on the history trail); non-terminal moves apply immediately.
    if (TERMINAL_STATUSES.has(status)) {
      // Defer the dialog open to the next tick: opening a modal Dialog inside
      // a Radix Select's onValueChange races the Select's dismiss layer, which
      // can leave the body pointer-events:none so the dialog never appears.
      setTimeout(() => setSummaryPrompt({ kind: 'single', findingId, status, title }), 0);
    } else {
      applyStatus(findingId, status, title);
    }
  };

  // j/k (↓/↑) move a row cursor, Enter opens the finding — as on Hosts.
  const { cursorRowProps } = useListCursor(
    loading || error ? 0 : findings.length,   // never a previous filter's row
    (i) => navigate(findingDetailHref(findings[i].id, searchParams.toString())),
    // Anchored to the finding: a status change re-reads the list, and the
    // cursor stays on its row instead of on whatever took its index.
    { resetKey: searchParams.toString(), getId: (i) => findings[i]?.id },
  );

  return (
    <div>
      {/* v5.294.0 (UX review) — the shared filter row: search, then one
          unlabelled select per dimension whose first option names it, the
          count on the right. It was five labelled fields on two rows. */}
      <ListFilterBar
        className="mb-md"
        summary={
          <span role="status" aria-live="polite">
            {/* A count that could not be read is not "0 findings" (owner,
                2026-10-09: "easily misinterpreted"). */}
            {loading ? 'Loading findings…'
              : error ? 'Findings could not be loaded'
                : `${total.toLocaleString()} finding${total === 1 ? '' : 's'}`}
          </span>
        }
      >
        <ListFilterSearch
          value={search.draft}
          onChange={search.setDraft}
          placeholder="Search finding titles…"
          label="Search findings"
        />
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as StatusFilterValue)}>
          {/* Wide enough for its longest label, "Active (open / confirmed)",
              to read whole (U4: at w-52 the default was cut). */}
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-[17rem]')} aria-label="Status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="active">Active (open / confirmed)</SelectItem>
            <SelectItem value="resolved">Resolved (terminal)</SelectItem>
            <SelectItem value="all">All statuses</SelectItem>
            {(Object.keys(STATUS_LABEL) as FindingStatus[]).map((s) => (
              <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={severityFilter} onValueChange={(v) => setSeverityFilter(v as FindingSeverity | 'all')}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-36')} aria-label="Severity">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All severities</SelectItem>
            {(['critical', 'high', 'medium', 'low', 'info'] as FindingSeverity[]).map((s) => (
              <SelectItem key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sourceFilter} onValueChange={(v) => setSourceFilter(v as FindingSource | 'all')}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-32')} aria-label="Source">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All sources</SelectItem>
            {SOURCE_OPTIONS.map((s) => (
              <SelectItem key={s} value={s}>{SOURCE_LABEL[s]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={ownerFilter} onValueChange={(v) => setOwnerFilter(v as OwnerFilterValue)}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-40')} aria-label="Owner">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">Any owner</SelectItem>
            <SelectItem value="me">{withCount('Assigned to me', user?.id != null ? countOf(user.id) : null)}</SelectItem>
            <SelectItem value="unowned">{withCount('Unowned', countOf(null))}</SelectItem>
            {ownerOptions.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                <span className="block max-w-[16rem] truncate">{o.label}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={needsFilter} onValueChange={(v) => setNeedsFilter(v as FindingNeedsFilter | 'all')}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-52')} aria-label="Still needed before the report">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Report readiness: any</SelectItem>
            {NEED_FILTER_OPTIONS.map(([value, label]) => (
              <SelectItem key={value} value={value}>{label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={hostStateFilter} onValueChange={(v) => setHostStateFilter(v as FindingHostStatus | 'all')}>
          <SelectTrigger className={cn(FILTER_TRIGGER_CLASS, 'w-52')} aria-label="Hosts">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Hosts: any state</SelectItem>
            {ENDPOINT_STATES.map((state) => (
              <SelectItem key={state} value={state}>{HOST_STATE_FILTER_LABEL[state]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </ListFilterBar>

      {/* Severity rollup — "how bad is this scope" at a glance (respects the
          status/source filters, ignores severity + pagination). Informational is
          excluded from the bar, so gate on actionable severities too. */}
      {(['critical', 'high', 'medium', 'low'] as FindingSeverity[]).some((s) => sevCounts[s]) && (
        <div className={cn('mb-md max-w-2xl', previousRows && 'opacity-50')}>
          <SeverityBar counts={sevCounts} variant="inline" />
        </div>
      )}

      {/* Bulk triage bar — appears when rows are selected. Applies one
          disposition to all selected (no per-item summary prompt in bulk). */}
      {selected.size > 0 && (
        <div className="mb-sm flex flex-wrap items-center gap-sm rounded-control border border-border bg-muted/30 p-sm">
          {/* Selection spans pages (it survives sort/pagination), so a bare
              count reads as "these rows here" and understates the blast
              radius. Say up front how much of it the operator can't see. */}
          <span className="text-metadata font-medium">
            {selected.size} selected
            {offscreenSelectedCount > 0 && (
              <span className="ml-xxs font-normal text-warning">
                · {offscreenSelectedCount} not on this page
              </span>
            )}
          </span>
          <Select onValueChange={(v) => void applyBulkStatus(v as FindingStatus)} disabled={bulkApplying}>
            <SelectTrigger className="h-8 w-48 text-caption" aria-label="Set status for selected findings">
              <SelectValue placeholder={bulkApplying ? 'Applying…' : 'Set status…'} />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(STATUS_LABEL) as FindingStatus[]).map((s) => (
                <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* Owner assignment was previously reachable only by opening each
              finding's detail page — one round trip per finding for a large
              unowned queue. */}
          <Select
            onValueChange={(v) => runBulkAssign(v === '__unassign' ? null : Number(v))}
            disabled={bulkApplying}
          >
            <SelectTrigger className="h-8 w-48 text-caption" aria-label="Assign selected findings to a user">
              <SelectValue placeholder="Assign owner…" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__unassign">Unassigned</SelectItem>
              {members.map((m) => (
                <SelectItem key={m.user_id} value={String(m.user_id)}>
                  {m.full_name || m.username}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {roster.status === 'error' && <MembersLoadError onRetry={roster.retry} />}
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
        </div>
      )}

      {/* v5.267.0 — the table sits on the page, not in a Card (UI_STYLE_GUIDE
          §7). overflow-x-auto per the Table primitive's documented usage —
          keeps the fixed-width columns from forcing page-level overflow. */}
      <section aria-label="Findings" aria-busy={previousRows || undefined}>
          {/* The re-read after a change failed: the rows below are as last
              read (with the change's own answer), not replaced by an error. */}
          {refreshError && (
            <p role="alert" className="mb-xs break-words text-metadata text-destructive">
              The list could not be refreshed, so it may be out of date.
              {refreshError !== LOAD_FAILED && ` ${refreshError}`}{' '}
              <button type="button" className="text-info hover:underline" onClick={() => void list.reload()}>
                Retry
              </button>
            </p>
          )}
          <div className="overflow-x-auto">
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  {canManage && (
                    <Checkbox
                      aria-label="Select all findings on this page"
                      // Not while the rows shown are a previous filter's.
                      disabled={previousRows}
                      // Some of this page's rows = the dash, not an empty box.
                      checked={selectAllState(findings.filter((f) => selected.has(f.id)).length, findings.length)}
                      // Union/subtract THIS page rather than replacing the set.
                      // Selection deliberately spans pages, so replacing it
                      // made select-all on page 2 silently drop page 1's work,
                      // and unchecking wiped everything — contradicting both
                      // the label and the cross-page count beside it.
                      onCheckedChange={(v) =>
                        setSelected((prev) => {
                          const next = new Set(prev);
                          findings.forEach((f) => (v ? next.add(f.id) : next.delete(f.id)));
                          return next;
                        })
                      }
                    />
                  )}
                </TableHead>
                {/* The title (with its first host beneath) takes the width;
                    the rest are compact values. Source is a filter, not a
                    column — it rarely decides what to work on next. */}
                <SortHead field="severity" label="Severity" className="w-24" />
                <SortHead field="title" label="Title" />
                {/* U4: Status and Owner are as wide as their content needs
                    ("False positive" + chevron; a name, truncated with its
                    title) so the spare width is the Title's. */}
                <SortHead field="status" label="Status" className="w-32" />
                <SortHead field="host_count" label="Hosts" className="w-20" />
                <SortHead field="owner" label="Owner" className="w-28" />
                <SortHead field="created_at" label="Age" className="w-16" />
              </TableRow>
            </TableHeader>
            <TableBody className={previousRows ? 'opacity-50' : undefined}>
              {/* Only blank the table on the INITIAL load (and while a failed
                  load is retried); a filter refetch keeps the prior rows on
                  screen, dimmed and inert, and a re-read after a change keeps
                  the rows as they are (no full-table flash). */}
              {((loading && (findings.length === 0 || readFailure !== null)) || (!loading && !error && pageEmptied)) && (
                <TableRow>
                  <TableCell colSpan={7} className="py-xl text-center text-muted-foreground">
                    <Loader2 className="mx-auto size-5 animate-spin" aria-hidden />
                  </TableCell>
                </TableRow>
              )}
              {!loading && error && (
                <TableRow>
                  <TableCell colSpan={7} role="alert" className="py-lg text-center text-destructive">
                    <AlertTriangle className="mx-auto mb-xs size-5" aria-hidden />
                    <p>The findings could not be loaded. This is not an empty list.</p>
                    {error !== LOAD_FAILED && <p className="break-words text-caption">{error}</p>}
                    <Button variant="outline" size="sm" className="mt-sm" onClick={() => void list.reload()}>
                      Retry
                    </Button>
                  </TableCell>
                </TableRow>
              )}
              {!loading && !error && findings.length === 0 && !pageEmptied && (
                <TableRow>
                  <TableCell colSpan={7} className="py-xl text-center text-muted-foreground">
                    {onlyDefaultFilter ? (
                      <>
                        No active findings.{' '}
                        <button type="button" className="text-primary hover:underline" onClick={() => setStatusFilter('all')}>
                          Include closed ones
                        </button>
                        {' · '}
                        <Link to={OBSERVATIONS_TO_JUDGE_PATH} className="text-primary hover:underline">
                          Promote from scanner observations
                        </Link>
                      </>
                    ) : hasActiveFilters
                      ? 'No findings match these filters. Clear them to see all.'
                      : 'No findings yet. Promote a scanner observation or a test result, or add one by hand, to record it here.'}
                  </TableCell>
                </TableRow>
              )}
              {!error && findings.map((f, i) => (
                <TableRow
                  key={f.id}
                  data-state={selected.has(f.id) ? 'selected' : undefined}
                  {...(canManage ? { 'aria-selected': selected.has(f.id) } : {})}
                  {...cursorRowProps(i)}
                >
                  <TableCell>
                    {canManage && (
                      <Checkbox
                        aria-label={`Select ${f.title}`}
                        checked={selected.has(f.id)}
                        // A bulk action acts only on rows of the CURRENT
                        // filter: a previous filter's row cannot be ticked.
                        disabled={previousRows}
                        onCheckedChange={() => toggleSelected(f.id)}
                      />
                    )}
                  </TableCell>
                  <TableCell>
                    <SeverityBadge severity={f.severity} />
                  </TableCell>
                  <TableCell>
                    {/* The note thread is the finding's evidence — link the
                        title to it (deep-links to the thread on the host, with
                        all its replies/discussion) so promote isn't a one-way
                        trip that drops the context. */}
                    <Link
                      to={findingDetailHref(f.id, searchParams.toString())}
                      // Two lines before it is cut (U4: on one line 23 of 50
                      // titles were truncated at a 1,126px window).
                      className="line-clamp-2 break-words text-info hover:underline"
                      title={`${f.title} — open finding`}
                    >
                      {f.title}
                    </Link>
                    {/* Caption line: where it is — the first host, "+N" more,
                        and (v5.225.0) the endpoint states when they are not
                        all open, so the finding's status never stands for
                        every host. */}
                    {f.hosts.length > 0 && (
                      <span
                        className="block truncate text-caption text-muted-foreground"
                        data-testid={`finding-hosts-${f.id}`}
                        title={endpointPreviewTitle(f.hosts, f.host_count)}
                      >
                        <Link
                          to={`/hosts/${f.hosts[0].host_id}`}
                          className="font-mono hover:text-foreground hover:underline"
                        >
                          {safeFallback(f.hosts[0].ip_address)}
                        </Link>
                        {f.hosts[0].hostname && <span> {f.hosts[0].hostname}</span>}
                        {/* `hosts` is a preview of at most five; the total is
                            `host_count` (C2) — never hosts.length. */}
                        {f.host_count > 1 && <span> +{(f.host_count - 1).toLocaleString()} more</span>}
                        {describeEndpointStates(f.endpoint_status_counts, f.host_count) && (
                          <span> · {describeEndpointStates(f.endpoint_status_counts, f.host_count)}</span>
                        )}
                      </span>
                    )}
                    {/* What it still needs before the report, in the
                        server's words; each opens the section of the finding
                        where it is met.  Nothing owed = no line. */}
                    {rowNeeds(f.needs).length > 0 && (
                      <span
                        className="block break-words text-caption text-warning"
                        data-testid={`finding-needs-${f.id}`}
                      >
                        {rowNeeds(f.needs).map((n, at) => (
                          <React.Fragment key={n.kind}>
                            {at > 0 && <span className="text-muted-foreground"> · </span>}
                            <Link
                              to={`${findingDetailHref(f.id, searchParams.toString())}#${NEED_SECTION[n.kind]}`}
                              className="hover:underline"
                            >
                              {n.text}
                            </Link>
                          </React.Fragment>
                        ))}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {/* Status reads as quiet text with a chevron; clicking it
                        opens the picker (terminal choices still prompt for
                        the "why").  The history lives on the finding page.
                        v5.288.0: the dotted underline sat 4px below a
                        line-clamped (overflow-hidden) span, so row by row it
                        was clipped or not with sub-pixel position — some
                        cells looked editable and some did not.  The chevron
                        and the "Change status" label say it on every row. */}
                    {canManage ? (
                      <Select
                        value={f.status}
                        disabled={previousRows}
                        onValueChange={(v) => handleStatusChange(f.id, v as FindingStatus, f.title)}
                      >
                        <SelectTrigger
                          className="h-auto w-auto max-w-full justify-start gap-xxs border-0 bg-transparent p-0 text-caption text-foreground shadow-none hover:text-primary [&>svg]:size-3"
                          aria-label={`Change status for ${f.title}`}
                          title="Change status"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(Object.keys(STATUS_LABEL) as FindingStatus[]).map((s) => (
                            <SelectItem key={s} value={s}>{STATUS_LABEL[s]}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <span className="block truncate text-caption">{STATUS_LABEL[f.status]}</span>
                    )}
                  </TableCell>
                  <TableCell className="text-caption tabular-nums text-muted-foreground">
                    {f.host_count > 0 ? f.host_count.toLocaleString() : '—'}
                  </TableCell>
                  <TableCell className="text-caption">
                    <span
                      className={f.owner_name ? 'block truncate' : 'block truncate text-muted-foreground'}
                      title={safeFallback(f.owner_name, 'Unassigned')}
                    >
                      {safeFallback(f.owner_name, 'Unassigned')}
                    </span>
                  </TableCell>
                  <TableCell className="text-caption tabular-nums text-muted-foreground">
                    <span className="block truncate" title={formatTimestamp(f.created_at, undefined)}>
                      {compactAge(f.created_at)}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          </div>
          {!error && total > 0 && (
            <div className="border-t border-border py-xs">
              <DataTablePagination
                pageIndex={page}
                pageSize={pageSize}
                totalCount={total}
                onPageChange={setPage}
                onPageSizeChange={(s) => { setPageSize(s); }}
                pageSizeOptions={[25, 50, 100, 200]}
                leftLabel={null}
              />
            </div>
          )}
      </section>

      {/* Terminal-disposition "why" prompt — the summary lands on the
          finding's history trail as the audit rationale. */}
      <StatusReasonDialog
        request={summaryRequest}
        onCancel={() => setSummaryPrompt(null)}
        onSave={(summary) => {
          const p = summaryPrompt;
          setSummaryPrompt(null);
          if (!p) return;
          if (p.kind === 'single') applyStatus(p.findingId, p.status, p.title, summary);
          else runBulk(p.ids, p.status, summary);
        }}
      />
      {confirmDialog}
    </div>
  );
};

export default Findings;
