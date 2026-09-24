import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  ChevronLeft,
  ChevronRight,
  Code,
  Computer,
  Download,
  ExternalLink,
  Loader2,
  RefreshCw,
  SkipForward,
  Star,
  X,
} from 'lucide-react';
import { RowSelectionState } from '@tanstack/react-table';
import {
  getHosts,
  getHostFilterData,
  followHost,
  unfollowHost,
  listHostFilterViews,
  createHostFilterView,
  deleteHostFilterView,
  getProjectDefaultView,
  promoteProjectDefaultView,
  clearProjectDefaultView,
} from '../services/api';
import type {
  Host,
  FollowStatus,
  HostFollowInfo,
  HostFilterView,
  HostFilterData,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { formatApiError } from '../utils/apiErrors';
import { useLatestRequest } from '../hooks/useLatestRequest';
import {
  HOST_BUILT_IN_VIEWS,
  HostFilterOptions,
  activeFilterPresetId,
} from '../components/HostFilters';
import HostCommandBar from '../components/hosts/HostCommandBar';
import HostFilterPopover from '../components/hosts/HostFilterPopover';
import { fieldForChip } from '../components/hosts/hostFilterFields';
import HostViewPicker, { type BuiltInHostView } from '../components/hosts/HostViewPicker';
import {
  FOLLOW_STATUS_OPTIONS,
  useHostColumns,
  type HostFilterPivot,
} from '../components/hosts/useHostColumns';
import ReportsDialog from '../components/ReportsDialog';
import ToolReadyOutput from '../components/ToolReadyOutput';
import { ListPageSkeleton } from '../components/PageSkeleton';
import { InlineLoader } from '../components/ui/inline-loader';
import { projectScopedKey } from '../utils/scopedStorage';
import { cn } from '../utils/cn';
import { copyToClipboard } from '../utils/clipboard';
import { stickyBelowChrome } from '../utils/uiStyles';
import { exposureChips } from '../utils/portsOfInterest';
import { endpointMatchCriteria } from '../utils/endpointMatch';
import { useConfirm } from '../hooks/useConfirm';
import { hostConditionChips } from '../utils/hostConditionChips';
import {
  hostFiltersFromUrl,
  type HostSortOption,
  type SavedHostFilterState,
} from '../utils/hostFiltersFromUrl';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  DataTablePagination,
  DataTableShell,
  useDataTable,
  selectionColumn,
} from '../components/ui/data-table';
import HostBulkBar from '../components/hosts/HostBulkBar';
import {
  SideSheet,
  SideSheetBody,
  SideSheetContent,
  SideSheetHeader,
  SideSheetTitle,
} from '../components/ui/side-sheet';
import HostInspector from '../components/HostInspector';

// v2.43.0 — MONO-1: FOLLOW_STATUS_OPTIONS, the column-render helpers
// (getLatestDiscovery / getTopServices / getScanLabel /
// formatRelativeLastViewed), the FollowMenu component, and the 166-line
// columns useMemo all moved to ../components/hosts/useHostColumns.tsx.
// Helpers re-exported here so anything in this file
// that still needs them keeps working.

// The preset list lives in HostFilters.tsx (`HOST_FILTER_PRESETS`), split into
// the port groups the filter panel offers and the built-in views the View
// picker offers.  followFilter + onlyWithNotes are part of HostFilterOptions,
// so the page's filter state is a single object.

type HostQueryContext = {
  state?: string;
  search?: string;
  ports?: string;
  services?: string;
  port_states?: string;
  has_open_ports?: boolean;
  os_filter?: string;
  subnets?: string;
  has_critical_vulns?: boolean;
  has_high_vulns?: boolean;
  has_medium_vulns?: boolean;
  has_low_vulns?: boolean;
  has_exploit_available?: boolean;
  has_test_execution?: boolean;
  out_of_scope_only?: boolean;
  follow_status?: string;
  scan_ids?: string;
  first_seen_in_scan?: boolean;
  with_notes_only?: boolean;
  has_web_interface?: boolean;
  tech?: string;
  tags?: string;
  // v2.86.0 — comma-separated subnet-label IDs round-tripped to the API.
  subnet_labels?: string;
  sites?: string;
  assigned_to?: string;
  // RDAP attribution — arrays → repeated query params (comma-safe org names).
  orgs?: string[];
  asns?: string[];
  countries?: string[];
  // v5.0.0 — boolean query DSL; ANDs with the structured params above.
  q?: string;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
};

// FollowMenu moved to ../components/hosts/useHostColumns.tsx (v2.43.0 MONO-1).

// Conditions shown in the sticky toolbar before "Show all conditions (N)".
const MAX_STICKY_CHIPS = 8;

export default function Hosts() {
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const { hasPermission } = useAuth();
  // Gates the "set as project default" affordance. Backend allows project
  // admins too, but the per-project role isn't surfaced here, so we gate the
  // UI on global admin (the common case) — non-admins simply don't see it.
  const canSetProjectDefault = hasPermission('admin');
  // Name of the project-default view currently applied (drives the banner);
  // null when none.  Persisted to session storage so the banner survives a page
  // refresh (the restored filters ARE the default) — without it an analyst on a
  // restored session saw a filtered list with no hint a default was hiding hosts.
  const [appliedProjectDefault, setAppliedProjectDefault] = useState<string | null>(null);
  // The project default view itself, kept for the whole visit.  It is usually
  // an admin's view, so it is NOT in this user's saved list: holding it here is
  // what lets "Back to default view" and the picker reach it again after the
  // filters were cleared (they used to be the only path, and clearing emptied it).
  const [projectDefaultView, setProjectDefaultView] = useState<HostFilterView | null>(null);
  // Set the banner AND persist it (or clear both). The init effect restores it.
  const setProjectDefaultBanner = (name: string | null) => {
    setAppliedProjectDefault(name);
    try {
      const key = projectScopedKey('projectDefaultName');
      if (name) sessionStorage.setItem(key, name);
      else sessionStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  };
  const [hosts, setHosts] = useState<Host[]>([]);
  const [totalHosts, setTotalHosts] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<HostFilterOptions>({});
  const [filterData, setFilterData] = useState<HostFilterData | null>(null);
  // Surfaced inline near the filter panel when the cascading filter
  // metadata call fails — previously the failure was console-only, so
  // users interacted with partially-stale dropdowns with no signal.
  // We keep the last-known-good `filterData` so the dropdowns degrade
  // gracefully rather than emptying out.
  const [filterDataError, setFilterDataError] = useState<string | null>(null);
  // True while facet options are in flight. Facets load AFTER the host list
  // (see the deferred fetch below), so without this flag an analyst can't tell
  // a still-loading combobox ("No ports seen yet.") from genuinely empty data.
  const [filterDataLoading, setFilterDataLoading] = useState(true);
  const [reportsDialogOpen, setReportsDialogOpen] = useState(false);
  // `?reports=1` (the report-finished notification's deep link) opens the
  // export tray; the job itself is listed there from the API, so no id
  // plumbing is needed beyond opening the dialog.
  useEffect(() => {
    if (new URLSearchParams(location.search).get('reports') === '1') {
      setReportsDialogOpen(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search]);
  const [toolReadyDialogOpen, setToolReadyDialogOpen] = useState(false);
  const [updatingHostId, setUpdatingHostId] = useState<number | null>(null);
  // v4.51.0 — followFilter + onlyWithNotes now live inside `filters`
  // (see HostFilterOptions).  Reads use `filters.followFilter ?? 'all'`
  // and `filters.onlyWithNotes === true`; the review chips write through
  // setFollowFilter below.
  const followFilter: 'all' | 'none' | FollowStatus = filters.followFilter ?? 'all';
  const onlyWithNotes = filters.onlyWithNotes === true;
  const setFollowFilter = useCallback((next: 'all' | 'none' | FollowStatus) => {
    setFilters((previous) => {
      const updated = { ...previous };
      if (next === 'all') {
        delete updated.followFilter;
      } else {
        updated.followFilter = next;
      }
      return updated;
    });
  }, [setFilters]);
  const [isInitialized, setIsInitialized] = useState(false);
  // v5.290.0 — the filters a bare /hosts visit restored from the session.  The
  // notice shows only while `filters` is still that very object: any change
  // (a chip, a view, Clear) replaces it and the notice is gone for the visit.
  const [restoredFilters, setRestoredFilters] = useState<HostFilterOptions | null>(null);
  const showRestoredNotice = restoredFilters !== null && filters === restoredFilters;
  const [sortBy, setSortBy] = useState<HostSortOption>('critical_desc');
  const [vulnError, setVulnError] = useState(false);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const rowSelectionRef = useRef(rowSelection);
  rowSelectionRef.current = rowSelection;

  // Saved Hosts page filter views (per-user, per-project).
  const [savedViews, setSavedViews] = useState<HostFilterView[]>([]);
  const [savedViewsError, setSavedViewsError] = useState<boolean>(false);
  const [saveViewDialogOpen, setSaveViewDialogOpen] = useState(false);
  const [saveViewName, setSaveViewName] = useState('');
  const [saveViewBusy, setSaveViewBusy] = useState(false);
  const [activeViewId, setActiveViewId] = useState<number | null>(null);
  const [chipsExpanded, setChipsExpanded] = useState(false);
  // The last view applied (saved or built-in) and how to apply it again.
  const [baseView, setBaseView] = useState<{ name: string; reapply: () => void } | null>(null);
  const [confirmEl, confirm] = useConfirm();

  const scanLookup = useMemo(() => {
    const map = new Map<string, { label: string }>();
    if (filterData?.scans) {
      filterData.scans.forEach((scan) => {
        const key = scan.id?.toString();
        if (!key) return;
        const labelBase = scan.filename || `Scan #${scan.id}`;
        const tool = scan.tool_name ? ` • ${scan.tool_name}` : '';
        map.set(key, { label: `${labelBase}${tool}` });
      });
    }
    return map;
  }, [filterData?.scans]);

  const clearAllFilters = useCallback(() => {
    setFilters({});
    setPage(0);
    // An explicit "show everything" has to survive a refresh: with no filters
    // left the auto-apply would otherwise bring the project default straight
    // back, and the operator's cleared list would silently be filtered again.
    try {
      sessionStorage.setItem(projectScopedKey('projectDefaultDismissed'), '1');
    } catch { /* ignore */ }
  }, [setFilters, setPage]);

  const buildHostQueryContext = useCallback((): HostQueryContext => {
    const params: HostQueryContext = {};
    if (filters.search) params.search = filters.search;
    if (filters.state) params.state = filters.state;
    if (filters.ports?.length) params.ports = filters.ports.join(',');
    if (filters.services?.length) params.services = filters.services.join(',');
    if (filters.portStates?.length) params.port_states = filters.portStates.join(',');
    if (filters.hasOpenPorts !== undefined) params.has_open_ports = filters.hasOpenPorts;
    if (filters.osFilter) params.os_filter = filters.osFilter;
    if (filters.subnets?.length) params.subnets = filters.subnets.join(',');
    if (filters.hasCriticalVulns !== undefined) params.has_critical_vulns = filters.hasCriticalVulns;
    if (filters.hasHighVulns !== undefined) params.has_high_vulns = filters.hasHighVulns;
    if (filters.hasMediumVulns !== undefined) params.has_medium_vulns = filters.hasMediumVulns;
    if (filters.hasLowVulns !== undefined) params.has_low_vulns = filters.hasLowVulns;
    if (filters.hasExploitAvailable !== undefined) params.has_exploit_available = filters.hasExploitAvailable;
    if (filters.hasTestExecution !== undefined) params.has_test_execution = filters.hasTestExecution;
    if (filters.outOfScopeOnly) params.out_of_scope_only = filters.outOfScopeOnly;
    if (followFilter !== 'all') params.follow_status = followFilter;
    if (filters.scanIds?.length) params.scan_ids = filters.scanIds.join(',');
    if (filters.firstSeenInSelectedScans && filters.scanIds?.length)
      params.first_seen_in_scan = filters.firstSeenInSelectedScans;
    if (onlyWithNotes) params.with_notes_only = true;
    if (filters.hasWebInterface !== undefined) params.has_web_interface = filters.hasWebInterface;
    if (filters.tech?.length) params.tech = filters.tech.join(',');
    if (filters.tags?.length) params.tags = filters.tags.join(',');
    if (filters.subnetLabels?.length) params.subnet_labels = filters.subnetLabels.join(',');
    if (filters.sites?.length) params.sites = filters.sites.join(',');
    // RDAP attribution — passed as arrays (repeated params), not comma-joined,
    // since org names contain commas.
    if (filters.orgs?.length) params.orgs = filters.orgs;
    if (filters.asns?.length) params.asns = filters.asns;
    if (filters.countries?.length) params.countries = filters.countries;
    if (filters.assignedToMe) params.assigned_to = 'me';
    if (filters.query?.trim()) params.q = filters.query.trim();
    params.sort_by = ({
      critical_desc: 'critical_vulns',
      exploitable_desc: 'exploitable_vulns',
      open_ports_desc: 'open_ports',
      notes_desc: 'note_count',
      discoveries_desc: 'discovery_count',
      ip_asc: 'ip_address',
      hostname_asc: 'hostname',
    } as const)[sortBy];
    params.sort_order = sortBy.endsWith('_asc') ? 'asc' : 'desc';
    return params;
  }, [filters, sortBy]);

  const buildFilterParams = useCallback(
    () => ({
      ...buildHostQueryContext(),
      skip: page * rowsPerPage,
      limit: rowsPerPage,
      include_total: true,
    }),
    [buildHostQueryContext, page, rowsPerPage],
  );

  // The filter-scope params for facet (dropdown-option) requests — the same
  // context as the host list minus pagination/sort. EVERY fetchFilterData call
  // must use this so facet options/counts always agree with the filtered table
  // (initial load, cascading refresh, Retry, visibility, post-bulk). Returns
  // undefined when no filter is active (request the full, unscoped facet set).
  const buildFacetParams = useCallback(() => {
    const { skip: _s, limit: _l, include_total: _t, sort_by: _sb, sort_order: _so, ...filterOnly } =
      buildFilterParams();
    return Object.keys(filterOnly).length > 0 ? filterOnly : undefined;
  }, [buildFilterParams]);
  // The visibilitychange listener is registered once (deps []), so it reads the
  // builder through a ref to avoid a stale closure scoping facets to the wrong
  // (initial) filter set.
  const buildFacetParamsRef = useRef(buildFacetParams);
  buildFacetParamsRef.current = buildFacetParams;

  // Bulk-selection safety: the selected-set (and any "select all matching")
  // is only meaningful for the result set it was made against. When the
  // filter/query signature changes the membership, clear the selection so a
  // bulk action can't silently retarget a different set or act on stale,
  // now-invisible row IDs. Sort + pagination don't change membership, so
  // they're excluded from the signature (selection survives them).
  const filterSignature = useMemo(() => {
    const { sort_by: _sb, sort_order: _so, ...membership } = buildHostQueryContext();
    return JSON.stringify(membership);
  }, [buildHostQueryContext]);
  const prevFilterSignature = useRef<string | null>(null);
  useEffect(() => {
    if (!isInitialized) return;
    if (prevFilterSignature.current === null) {
      prevFilterSignature.current = filterSignature; // seed on first settle
      return;
    }
    if (prevFilterSignature.current !== filterSignature) {
      prevFilterSignature.current = filterSignature;
      // Say so: a bulk bar that vanishes without a word reads as a lost action.
      const dropped = Object.keys(rowSelectionRef.current).length;
      if (dropped > 0) {
        toast.info(
          `Selection cleared (${dropped} host${dropped === 1 ? '' : 's'}) — the filters changed, so the rows are a different set.`,
          { autoHideMs: 4000 },
        );
      }
      setRowSelection({});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- toast is stable; the selection is read through a ref
  }, [filterSignature, isInitialized]);

  // Two independent request lanes (rows vs filter facets) — see
  // useLatestRequest: each aborts its own predecessor, never the other's.
  const runHostsRequest = useLatestRequest();
  const runFilterDataRequest = useLatestRequest();
  // True once the first host fetch has completed.  Gates the full-page
  // skeleton so it only shows on initial load — never on a refetch whose
  // current result happens to be empty (e.g. toggling a filter that matches
  // 0 hosts, or rapid toggling), which would otherwise replace the whole
  // page and snap the scroll to the top.
  const hasFetchedOnceRef = useRef(false);

  const fetchHosts = async () => {
    setLoading(true);
    setError(null);
    const params = buildFilterParams();
    const r = await runHostsRequest((signal) => getHosts(params, signal));
    if (r.stale) return;
    if (r.ok) {
      setHosts(r.value.items);
      setTotalHosts(r.value.total ?? 0);
      setVulnError(r.value.vulnerability_error ?? false);
    } else {
      console.error('Error fetching hosts:', r.error);
      setError(formatApiError(r.error, 'Failed to fetch hosts. Please try again.'));
    }
    setLoading(false);
    hasFetchedOnceRef.current = true;
  };

  const fetchFilterData = async (
    params?: Record<string, string | boolean | number | string[] | undefined>,
  ) => {
    setFilterDataLoading(true);
    const r = await runFilterDataRequest((signal) => getHostFilterData(params, signal));
    if (r.stale) return;
    if (r.ok) {
      setFilterData(r.value);
      setFilterDataError(null);
    } else {
      console.error('Error fetching filter data:', r.error);
      setFilterDataError(
        formatApiError(r.error, 'Filter options failed to refresh — dropdowns may be stale.'),
      );
    }
    setFilterDataLoading(false);
  };

  // v2.86.5 — defer the initial filter-facets fetch until AFTER the
  // host list has resolved.  Pre-fix this fired on mount, racing the
  // /hosts/ request and contending for the same workers; the
  // facets-data query is the heavier of the two (it aggregates across
  // every host's ports / services / OS / scans / tags / subnet labels /
  // technologies).  Now: wait for `loading` (the host list) to flip to
  // false, then fetch.  This makes the table paint perceptibly faster
  // since the chrome + table appear before the filter combobox options
  // arrive.  The combobox controls show "Loading…" until filterData
  // resolves, which is the existing behaviour for cascading refreshes.
  useEffect(() => {
    if (loading) return;
    if (filterData !== null) return;
    fetchFilterData(buildFacetParams());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot post-load fetch
  }, [loading]);

  // The "+ Add filter" popover: open/closed, and which field's editor it shows
  // (null = the catalog).  Lives above the cascading-facet effect, which gates
  // on it.
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterFieldId, setFilterFieldId] = useState<string | null>(null);
  const openFilterEditor = useCallback((fieldId: string | null) => {
    setFilterFieldId(fieldId);
    setFilterOpen(true);
  }, []);

  // Cascading refresh: when filters change (debounced 400ms), refetch
  // facet counts so the combobox trailing-count chips reflect the new
  // result set.  Pre-audit (H18) this depended on the whole
  // `buildFilterParams` callback, whose identity changed on sort, page,
  // and rowsPerPage edits — none of which should invalidate the
  // dropdown options.  Now depends only on the actual filter-shape
  // inputs.  Also gated on `filterData` having already loaded once, so
  // the initial post-load fetch above isn't double-fired.
  //
  // #49 — those cascading counts only render inside the filter editors, so
  // only refetch while the popover is open.  When it's closed (filtering via
  // the review chips / query bar), a filter change no longer fires a heavy
  // facet query; opening the popover re-runs this effect and refreshes the
  // counts.  Chip labels rely on the one-shot initial load (names don't change
  // with filters), so they're unaffected.
  useEffect(() => {
    if (filterData === null || !filterOpen) return;
    const timer = setTimeout(() => {
      fetchFilterData(buildFacetParams());
    }, 400);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional narrowing per audit H18
  }, [filters, filterOpen]);

  useEffect(() => {
    if (isInitialized) return;
    const urlParams = new URLSearchParams(location.search);

    // URL first, saved session only on a bare /hosts visit — the rules live
    // in utils/hostFiltersFromUrl.ts, where they are tested.
    let savedState: SavedHostFilterState | null = null;
    try {
      const raw = sessionStorage.getItem(projectScopedKey('hostFiltersState'));
      savedState = raw ? JSON.parse(raw) : null;
    } catch {
      savedState = null;
    }
    const {
      filters: initialFilters,
      sortBy: restoredSort,
      restoredFromSession,
    } = hostFiltersFromUrl(urlParams, savedState);
    if (restoredSort) setSortBy(restoredSort);

    // Re-show the "project default applied" banner after a refresh: the restored
    // filters ARE the default, so set skipActiveClearRef so the filters-change
    // effect doesn't clear it on this (non-user) restore.
    let restoredDefault: string | null = null;
    try {
      restoredDefault = sessionStorage.getItem(projectScopedKey('projectDefaultName'));
      if (restoredDefault && Object.keys(initialFilters).length > 0) {
        skipActiveClearRef.current = true;
        setAppliedProjectDefault(restoredDefault);
      } else {
        restoredDefault = null;
      }
    } catch {
      /* ignore */
    }

    // v5.290.0 — a nav link to a bare /hosts reopens the session's filters;
    // say so, unless the project-default banner already explains them.
    if (restoredFromSession && !restoredDefault) setRestoredFilters(initialFilters);
    setFilters(initialFilters);
    setIsInitialized(true);
  }, [isInitialized, location.search]);

  useEffect(() => {
    if (!isInitialized) return;
    fetchHosts();
  }, [buildFilterParams, isInitialized]);

  useEffect(() => {
    listHostFilterViews()
      .then((views) => {
        setSavedViews(views);
        setSavedViewsError(false);
      })
      .catch((err) => {
        console.warn('Could not load saved Hosts views:', err);
        setSavedViewsError(true);
      });
  }, []);

  const handleSaveView = async () => {
    const name = saveViewName.trim();
    if (!name) return;
    setSaveViewBusy(true);
    try {
      // v4.51.0 — keep the legacy filter_json shape on the wire so
      // older saved blobs and older frontends interoperate.  Internal
      // state now folds followFilter/onlyWithNotes into `filters`; we
      // split them back out at the persistence boundary.
      const { followFilter: ff, onlyWithNotes: own, ...filtersOnly } = filters;
      const created = await createHostFilterView(name, {
        filters: filtersOnly,
        followFilter: ff ?? 'all',
        onlyWithNotes: own === true,
      });
      setSavedViews((prev) => [created, ...prev.filter((v) => v.id !== created.id)]);
      setActiveViewId(created.id);
      setSaveViewDialogOpen(false);
      setSaveViewName('');
      toast.success(`Saved view "${name}"`);
    } catch (err: unknown) {
      console.error('Failed to save view:', err);
      toast.error(formatApiError(err, 'Failed to save view.'));
    } finally {
      setSaveViewBusy(false);
    }
  };

  // Set when handleApplyView fires so the next clear-on-filter-change
  // effect knows to skip itself.
  const skipActiveClearRef = useRef(false);

  const applyViewFilters = useCallback((view: HostFilterView, opts?: { quiet?: boolean }) => {
    const blob = view.filter_json || {};
    skipActiveClearRef.current = true;
    // v4.51.0 — fold the legacy top-level keys into the combined
    // filters shape on apply.  Newer saves go through the same
    // converter so old + new blobs round-trip identically.
    // Guard the persisted shape: filter_json is Record<string, any>, so a
    // corrupted/legacy blob could store `filters` as a string or array.
    // Only spread it when it's a plain object, else start from empty.
    const rawFilters = blob.filters;
    const safeFilters: HostFilterOptions =
      rawFilters && typeof rawFilters === 'object' && !Array.isArray(rawFilters)
        ? (rawFilters as HostFilterOptions)
        : {};
    const next: HostFilterOptions = { ...safeFilters };
    const ff = blob.followFilter as 'all' | 'none' | FollowStatus | undefined;
    if (ff && ff !== 'all') next.followFilter = ff;
    if (blob.onlyWithNotes === true) next.onlyWithNotes = true;
    setFilters(next);
    setActiveViewId(view.id);
    setBaseView({ name: view.name, reapply: () => applyViewFilters(view) });
    setPage(0);
    // Explicitly applying a view supersedes any auto-applied project default,
    // so the "project default applied" chip would be stale — drop it. The
    // quiet path IS the auto-apply, which sets the chip itself afterwards.
    if (!opts?.quiet) {
      setProjectDefaultBanner(null);
      toast.info(`Applied view "${view.name}"`, { autoHideMs: 2000 });
    }
  }, [toast]);

  const handleApplyView = (view: HostFilterView) => applyViewFilters(view);

  // A built-in view replaces the applied filters exactly as a saved one does.
  const handleApplyBuiltIn = (view: BuiltInHostView) => {
    skipActiveClearRef.current = true;
    setFilters({ ...view.filters });
    setActiveViewId(null);
    setBaseView({ name: view.name, reapply: () => handleApplyBuiltIn(view) });
    setPage(0);
    setProjectDefaultBanner(null);
  };

  const handleDeleteView = async (view: HostFilterView) => {
    const ok = await confirm({
      title: 'Delete saved view',
      body: 'You can recreate this view at any time by re-applying the filters and clicking Save view.',
      resourceName: view.name,
      severity: 'warning',
      confirmLabel: 'Delete',
    });
    if (!ok) return;
    try {
      await deleteHostFilterView(view.id);
      setSavedViews((prev) => prev.filter((v) => v.id !== view.id));
      if (activeViewId === view.id) setActiveViewId(null);
      toast.info(`Deleted view "${view.name}"`, { autoHideMs: 2000 });
    } catch (err: unknown) {
      console.error('Failed to delete view:', err);
      toast.error(formatApiError(err, 'Failed to delete view.'));
    }
  };

  // Promote a saved view as the project default (admin), or clear it.
  const handleToggleProjectDefault = async (view: HostFilterView) => {
    try {
      if (view.is_project_default) {
        await clearProjectDefaultView();
        setSavedViews((prev) => prev.map((v) => ({ ...v, is_project_default: false })));
        setProjectDefaultView(null);
        toast.info('Cleared the project default view.', { autoHideMs: 2000 });
      } else {
        await promoteProjectDefaultView(view.id);
        setSavedViews((prev) => prev.map((v) => ({ ...v, is_project_default: v.id === view.id })));
        setProjectDefaultView({ ...view, is_project_default: true });
        toast.success(`"${view.name}" is now the project default.`, { autoHideMs: 2500 });
      }
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update the project default.'));
    }
  };

  // Load the project default once per mount and keep it; auto-apply it only on
  // a bare /hosts visit (no URL/saved-session filter, not dismissed this session).
  const defaultCheckedRef = useRef(false);
  useEffect(() => {
    if (!isInitialized || defaultCheckedRef.current) return;
    defaultCheckedRef.current = true;
    // Only when the user has no filter context of their own.
    let autoApply = Object.keys(filters).length === 0;
    try {
      if (sessionStorage.getItem(projectScopedKey('projectDefaultDismissed')) === '1') autoApply = false;
    } catch { /* ignore */ }
    getProjectDefaultView()
      .then((view) => {
        if (!view || !view.filter_json) return;
        setProjectDefaultView(view);
        if (autoApply) {
          applyViewFilters(view, { quiet: true });
          setProjectDefaultBanner(view.name);
        }
      })
      .catch(() => { /* non-fatal */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isInitialized]);

  // Choosing the default again (after clearing or editing): apply it as the
  // auto-apply does, and lift this session's "show everything" so a refresh
  // keeps it.
  const applyProjectDefault = () => {
    if (!projectDefaultView) return;
    applyViewFilters(projectDefaultView, { quiet: true });
    setProjectDefaultBanner(projectDefaultView.name);
    try {
      sessionStorage.removeItem(projectScopedKey('projectDefaultDismissed'));
    } catch { /* ignore */ }
  };
  const projectDefaultActive = projectDefaultView !== null
    && (appliedProjectDefault !== null || activeViewId === projectDefaultView.id);

  useEffect(() => {
    if (skipActiveClearRef.current) {
      skipActiveClearRef.current = false;
      return;
    }
    // The view is no longer what is shown, but it is still where the operator
    // started ("<name> · Modified") — until nothing of it is left.
    setActiveViewId(null);
    if (Object.keys(filters).length === 0) setBaseView(null);
    // A manual filter edit means the auto-applied project default no longer
    // describes what's shown — clear the chip so it can't go stale.
    setProjectDefaultBanner(null);
  }, [filters]);

  useEffect(() => {
    if (!isInitialized) return;
    if (typeof window !== 'undefined') {
      // Persist in the legacy 3-key shape so older sessions / older
      // frontends still load these blobs cleanly (see v4.51.0 note).
      const { followFilter: ff, onlyWithNotes: own, ...filtersOnly } = filters;
      const stateToPersist = {
        filters: filtersOnly,
        followFilter: ff ?? 'all',
        onlyWithNotes: own === true,
      };
      sessionStorage.setItem(projectScopedKey('hostFiltersState'), JSON.stringify(stateToPersist));
    }
  }, [filters, isInitialized]);

  // v5.0.0 — URL write-sync (the previously-missing write side, so links
  // are shareable).  Serializes the active query context into the URL,
  // debounced, replace-only.  One-directional: the restore effect is
  // mount-only (gated on isInitialized) and the fetch effect keys on
  // buildFilterParams (filters state), not location.search — so writing
  // the URL never triggers a refetch or a restore loop.
  useEffect(() => {
    if (!isInitialized) return;
    const ctx = buildHostQueryContext();
    const sp = new URLSearchParams();
    Object.entries(ctx).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return;
      // Arrays (orgs/asns/countries) become repeated params, not a comma-joined
      // scalar — an org name contains commas, so joining would corrupt it.
      if (Array.isArray(value)) {
        value.forEach((v) => sp.append(key, String(v)));
      } else {
        sp.set(key, String(value));
      }
    });
    const search = sp.toString();
    const timer = setTimeout(() => {
      navigate({ search: search ? `?${search}` : '' }, { replace: true });
    }, 400);
    return () => clearTimeout(timer);
  }, [buildHostQueryContext, isInitialized, navigate]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (!document.hidden) fetchFilterData(buildFacetParamsRef.current());
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, []);

  const handleFiltersChange = (newFilters: HostFilterOptions) => {
    setFilters(newFilters);
    setPage(0);
  };

  // v5.0.0 — command-bar query handlers.

  const setQuery = useCallback((q: string) => {
    setFilters((previous) => {
      const updated = { ...previous };
      if (q) updated.query = q;
      else delete updated.query;
      return updated;
    });
    setPage(0);
  }, [setFilters, setPage]);

  // Build the shareable URL from the live query context rather than
  // window.location.href, which lags behind by the URL write-sync debounce.
  // The command bar passes its current draft so a just-typed query is
  // reflected immediately (its commit to filters.query is also debounced).
  const handleCopyLink = useCallback((draftQuery?: string) => {
    const ctx = buildHostQueryContext();
    if (draftQuery !== undefined) {
      const trimmed = draftQuery.trim();
      if (trimmed) ctx.q = trimmed;
      else delete ctx.q;
    }
    const sp = new URLSearchParams();
    Object.entries(ctx).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return;
      if (Array.isArray(value)) {
        value.forEach((v) => sp.append(key, String(v)));
      } else {
        sp.set(key, String(value));
      }
    });
    const query = sp.toString();
    const url = `${window.location.origin}${window.location.pathname}${query ? `?${query}` : ''}`;
    void copyToClipboard(url).then((ok) =>
      ok
        ? toast.info('Link copied to clipboard', { autoHideMs: 2000 })
        : toast.error('Could not copy link'),
    );
  }, [buildHostQueryContext, toast]);

  // Pin the current query as a saved view.  Commit the passed draft into
  // filters first so the saved blob reflects what's in the box now, not the
  // last debounced commit (handleSaveView reads the live filters.query).
  const handlePinQuery = useCallback((q: string) => {
    setQuery(q);
    setSaveViewName('');
    setSaveViewDialogOpen(true);
  }, [setQuery]);

  // Facet values for the command-bar autocomplete, keyed by the DSL
  // value_source.  Tag/label suggest by NAME (the DSL resolves them by
  // name), unlike the id-based panel.
  const queryValueSuggestions = useMemo(() => {
    const map: Record<string, string[]> = {};
    if (filterData?.common_ports) map.port = filterData.common_ports.map((p) => String(p.port));
    if (filterData?.services) map.service = filterData.services.map((s) => s.name);
    if (filterData?.operating_systems) map.os = filterData.operating_systems.map((o) => o.name);
    if (filterData?.technologies) map.tech = filterData.technologies.map((t) => t.name);
    if (filterData?.tags) map.tag = filterData.tags.map((t) => t.name);
    if (filterData?.subnet_labels) map.label = filterData.subnet_labels.map((l) => l.name);
    if (filterData?.sites) map.site = filterData.sites.map((s) => s.name);
    if (filterData?.scans) map.scan = filterData.scans.map((s) => String(s.id));
    return map;
  }, [filterData]);

  // `scan:` takes a numeric id, which is the one thing an operator does NOT
  // know — they know the file they uploaded. Labelling each suggested id with
  // its filename (and tool) makes the autocomplete the lookup table, and
  // HostCommandBar matches on the label too, so typing `scan:openvas` finds
  // the id and inserts it.
  const queryValueLabels = useMemo(() => {
    if (!filterData?.scans) return undefined;
    const scan: Record<string, string> = {};
    for (const s of filterData.scans) {
      scan[String(s.id)] = s.tool_name ? `${s.filename} (${s.tool_name})` : s.filename;
    }
    return { scan };
  }, [filterData]);

  // Row click opens the side-sheet instead of navigating away from the
  // list — operators keep their place in the filtered set while
  // drilling into a host.  The full standalone page at /hosts/:id stays
  // reachable via the "Open standalone" link inside the sheet, or by
  // bookmark / deep link.
  const [inspectedHostId, setInspectedHostId] = useState<number | null>(null);
  // UX review C1: the inspector reports whether its note composer holds an
  // unsaved draft; every queue transition (Previous/Next/Next unreviewed,
  // close, Open standalone, vuln pivot) asks before discarding it.
  const inspectorDirtyRef = useRef(false);
  const confirmDiscardDraft = async (): Promise<boolean> => {
    if (!inspectorDirtyRef.current) return true;
    const ip = hosts.find((h) => h.id === inspectedHostId)?.ip_address ?? 'this host';
    return confirm({
      title: 'Discard unsaved work?',
      body: `What you started for ${ip} — a note, pasted screenshots, a reply or a test summary — has not been saved. Leave anyway?`,
      severity: 'warning',
      confirmLabel: 'Discard',
    });
  };

  // Keyboard row cursor for the list (1c).  -1 = no cursor yet.  j/k (and
  // arrows) move it and Enter opens the host; when the inspector is already
  // open, j/k advance IT instead (reusing stepInspector), so an operator can
  // fly through hosts reviewing each in the side-sheet without the mouse.
  const [cursorIndex, setCursorIndex] = useState(-1);

  const openInspector = (hostId: number) => {
    if (typeof window !== 'undefined') {
      const { followFilter: ff, onlyWithNotes: own, ...filtersOnly } = filters;
      const stateToPersist = {
        filters: filtersOnly,
        followFilter: ff ?? 'all',
        onlyWithNotes: own === true,
      };
      sessionStorage.setItem(projectScopedKey('hostFiltersState'), JSON.stringify(stateToPersist));
    }
    setInspectedHostId(hostId);
  };

  // "Open standalone" inside the side-sheet — passes the same navState
  // that the old direct-navigate flow used, so the standalone page's
  // back / prev / next chrome still works.
  //
  // Audit FRX·M3: state-only nav is lost on refresh / share, so the
  // standalone page also receives a `?from=hosts&filter=<base64>`
  // query string that captures just the prev/next list context (page
  // + absolute index + the filter that defined the list).  HostDetail
  // reads this when location.state is missing.  Encoded minimally —
  // the filter shape is the same compact object the API already
  // accepts, base64'd so a stray `&` in a search term doesn't fight
  // the query parser.
  const navigateToStandalone = (hostId: number) => {
    if (typeof window !== 'undefined') {
      const { followFilter: ff, onlyWithNotes: own, ...filtersOnly } = filters;
      const stateToPersist = {
        filters: filtersOnly,
        followFilter: ff ?? 'all',
        onlyWithNotes: own === true,
      };
      sessionStorage.setItem(projectScopedKey('hostFiltersState'), JSON.stringify(stateToPersist));
    }
    const returnTo = `${location.pathname}${location.search}` || '/hosts';
    const hostIds = hosts.map((h) => h.id);
    const currentIndex = hostIds.indexOf(hostId);
    const absoluteIndex = page * rowsPerPage + currentIndex;
    const queryContext = buildHostQueryContext();
    let filterParam = '';
    try {
      const compact = {
        f: queryContext,
        i: absoluteIndex,
        t: totalHosts,
      };
      // btoa accepts only Latin-1; encodeURIComponent first guards
      // against non-ASCII characters in search/hostname filters.
      filterParam = btoa(unescape(encodeURIComponent(JSON.stringify(compact))));
    } catch {
      // Encoding failures fall back to state-only nav — no functional
      // regression vs the pre-fix behaviour.
    }
    const search = filterParam ? `?from=hosts&filter=${filterParam}` : '';
    navigate(`/hosts/${hostId}${search}`, {
      state: {
        fromHosts: returnTo,
        hostIds,
        currentIndex,
        totalHosts,
        absoluteIndex,
        queryContext,
      },
    });
  };

  // Guided review queue (§6): the open side-sheet walks the WHOLE filtered
  // result set, not just the loaded page.  At a page edge, stepping turns the
  // page (which the list effect refetches) and `pendingInspectorEdgeRef` tells
  // the post-load effect which host to open — so prev/next/next-unreviewed
  // continue across pagination instead of dead-ending at the window.
  const inspectedIndex =
    inspectedHostId !== null ? hosts.findIndex((h) => h.id === inspectedHostId) : -1;
  const lastPageIndex = Math.max(0, Math.ceil(totalHosts / rowsPerPage) - 1);
  // Position within the full result set (queue progress), not just the page.
  const inspectedAbsoluteIndex = inspectedIndex >= 0 ? page * rowsPerPage + inspectedIndex : -1;
  // What to open once the next page loads. A ref (not state) so turning the
  // page doesn't add a render and the post-load effect reads the latest intent.
  const pendingInspectorEdgeRef = useRef<null | 'first' | 'last' | 'first-unreviewed'>(null);
  // A filter edit or a page turn can take the open host out of the rows shown.
  // The inspector keeps it (never a silent switch, never a lost note draft) and
  // says so; Next then walks the queue that is on screen NOW, from its top.
  const inspectedOutsideRows =
    inspectedHostId !== null && inspectedIndex < 0 && !loading && pendingInspectorEdgeRef.current === null;
  const hasInspectorPrev = inspectedAbsoluteIndex > 0;
  const hasInspectorNext = inspectedOutsideRows
    ? hosts.length > 0
    : inspectedAbsoluteIndex >= 0 && inspectedAbsoluteIndex < totalHosts - 1;
  // "Unreviewed" = nobody has started it: skip both Reviewed AND In Review
  // (a teammate is already on the in-review ones). Untouched / legacy-watching
  // hosts are the queue targets.
  const hostNeedsReview = (h: Host) => {
    const status = h.follow?.status ?? 'none';
    return status !== 'reviewed' && status !== 'in_review';
  };

  const stepInspector = async (delta: 1 | -1) => {
    if (inspectedIndex < 0) {
      // Outside the rows shown: there is no "previous", and "next" is the top
      // of the current queue.
      if (delta !== 1 || !inspectedOutsideRows || hosts.length === 0) return;
      if (await confirmDiscardDraft()) setInspectedHostId(hosts[0].id);
      return;
    }
    if (!(await confirmDiscardDraft())) return;
    const target = hosts[inspectedIndex + delta];
    if (target) { setInspectedHostId(target.id); return; }
    // Crossed the page boundary — turn the page and open its near edge.
    if (delta === 1 && page < lastPageIndex) {
      pendingInspectorEdgeRef.current = 'first';
      setPage((p) => p + 1);
    } else if (delta === -1 && page > 0) {
      pendingInspectorEdgeRef.current = 'last';
      setPage((p) => p - 1);
    }
  };

  // Jump to the next host that still needs review, scanning forward across
  // pages and skipping ones already Reviewed.
  const stepToNextUnreviewed = async () => {
    // From outside the rows shown the scan starts at the top (index -1 + 1).
    if (inspectedIndex < 0 && !inspectedOutsideRows) return;
    if (!(await confirmDiscardDraft())) return;
    for (let i = inspectedIndex + 1; i < hosts.length; i += 1) {
      if (hostNeedsReview(hosts[i])) { setInspectedHostId(hosts[i].id); return; }
    }
    if (page < lastPageIndex) {
      pendingInspectorEdgeRef.current = 'first-unreviewed';
      setPage((p) => p + 1);
    } else {
      toast.info('No more unreviewed hosts in this queue', { autoHideMs: 2000 });
    }
  };

  // Consume a pending cross-page jump once the new page has loaded.
  useEffect(() => {
    const edge = pendingInspectorEdgeRef.current;
    if (!edge || loading || hosts.length === 0) return;
    if (edge === 'first') {
      pendingInspectorEdgeRef.current = null;
      setInspectedHostId(hosts[0].id);
    } else if (edge === 'last') {
      pendingInspectorEdgeRef.current = null;
      setInspectedHostId(hosts[hosts.length - 1].id);
    } else if (edge === 'first-unreviewed') {
      const target = hosts.find(hostNeedsReview);
      if (target) {
        pendingInspectorEdgeRef.current = null;
        setInspectedHostId(target.id);
      } else if (page < lastPageIndex) {
        setPage((p) => p + 1); // keep scanning forward
      } else {
        pendingInspectorEdgeRef.current = null;
        toast.info('No more unreviewed hosts in this queue', { autoHideMs: 2000 });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hosts, loading]);

  // A fresh result set (page turn) invalidates the row cursor.
  useEffect(() => {
    setCursorIndex(-1);
  }, [page]);

  // Keep the cursor row in view as it moves below/above the fold.  The
  // cursor row carries a `host-cursor-row` marker class (see getRowClassName)
  // so we can find it without threading a ref through DataTableShell.
  useEffect(() => {
    if (cursorIndex < 0 || typeof document === 'undefined') return;
    document.querySelector('.host-cursor-row')?.scrollIntoView({ block: 'nearest' });
  }, [cursorIndex]);

  // List keyboard navigation (1c).  Global listener with an input guard so
  // it never hijacks typing in the search / command bar / note composer, and
  // an Enter guard so it doesn't double-fire on a focused button/link.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t?.isContentEditable) return;
      if ((tag === 'BUTTON' || tag === 'A') && (e.key === 'Enter' || e.key === ' ')) return;
      if (loading || hosts.length === 0) return;

      // Inspector open → j/k advance the side-sheet; arrows stay free to
      // scroll its content.
      if (inspectedHostId !== null) {
        if (e.key === 'j') {
          e.preventDefault();
          stepInspector(1);
        } else if (e.key === 'k') {
          e.preventDefault();
          stepInspector(-1);
        } else if (e.key === 'n') {
          e.preventDefault();
          stepToNextUnreviewed();
        }
        return;
      }

      // List cursor.
      if (e.key === 'j' || e.key === 'ArrowDown') {
        e.preventDefault();
        setCursorIndex((c) => Math.min((c < 0 ? -1 : c) + 1, hosts.length - 1));
      } else if (e.key === 'k' || e.key === 'ArrowUp') {
        e.preventDefault();
        setCursorIndex((c) => (c <= 0 ? 0 : c - 1));
      } else if (e.key === 'Enter' && cursorIndex >= 0 && cursorIndex < hosts.length) {
        e.preventDefault();
        openInspector(hosts[cursorIndex].id);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // The nav handlers are recreated each render but close over hosts /
    // inspectedHostId / page / totalHosts; re-attaching on those keeps the
    // cross-page queue stepping fresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hosts, inspectedHostId, loading, cursorIndex, page, totalHosts, rowsPerPage]);

  const applyFollowUpdate = (hostId: number, followInfo: HostFollowInfo | null) => {
    setHosts((previous) =>
      previous.map((host) => (host.id === hostId ? { ...host, follow: followInfo } : host)),
    );
  };

  const handleFollowChange = async (hostId: number, status: FollowStatus | 'none') => {
    setUpdatingHostId(hostId);
    try {
      if (status === 'none') {
        await unfollowHost(hostId);
        applyFollowUpdate(hostId, null);
        toast.info('Review status cleared', { autoHideMs: 2000 });
      } else {
        const response = await followHost(hostId, status);
        applyFollowUpdate(hostId, response);
        const label = status === 'in_review' ? 'In Review' : 'Reviewed';
        toast.success(`Marked as ${label}`, { autoHideMs: 2000 });
      }
      setError(null);
    } catch (err) {
      console.error('Error updating review status:', err);
      const message = formatApiError(err, 'Unable to update review status. Please try again.');
      setError(message);
      toast.error(message);
    } finally {
      setUpdatingHostId(null);
    }
  };

  // -------------------------------------------------------------------------
  // Active-filter chips (derived from current filter state).
  // -------------------------------------------------------------------------
  // One chip per CONDITION (utils/hostConditionChips.ts) — the strip reads as
  // "matching all of", so values that are alternatives share a chip.  Every
  // consumer that asks "is anything applied?" keys on this list too.
  const activeFilterChips = useMemo(
    () =>
      hostConditionChips(filters, {
        scan: (id) => scanLookup.get(id)?.label,
        // The filter state holds ids; the chip shows the name once facets load.
        tag: (id) => filterData?.tags?.find((t) => String(t.id) === id)?.name,
        subnetLabel: (id) => filterData?.subnet_labels?.find((l) => String(l.id) === id)?.name,
        asn: (asn) => filterData?.asns?.find((a) => String(a.asn) === asn)?.as_name ?? undefined,
        followStatus: (value) => FOLLOW_STATUS_OPTIONS.find((o) => o.value === value)?.label,
      }).map((chip) => ({
        ...chip,
        fieldId: fieldForChip(chip.key, filters)?.id,
        onDelete: () => {
          // Removal is immediate, so it is reversible: a condition can be
          // several values that took a while to pick.
          const before = filters;
          setFilters((previous) => {
            const updated = { ...previous } as Record<string, unknown>;
            chip.clearKeys.forEach((key) => delete updated[key]);
            return updated as HostFilterOptions;
          });
          setPage(0);
          toast.info(`Removed: ${chip.label}`, {
            autoHideMs: 6000,
            action: { label: 'Undo', onClick: () => { setFilters(before); setPage(0); } },
          });
        },
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- toast is stable
    [filters, scanLookup, filterData],
  );

  // The filters as they were before the last change — what "Undo last change"
  // on an empty result goes back to.  Not set by the initial restore.
  const [previousFilters, setPreviousFilters] = useState<HostFilterOptions | null>(null);
  const lastFiltersRef = useRef<HostFilterOptions | null>(null);
  useEffect(() => {
    if (!isInitialized) return;
    if (lastFiltersRef.current !== null && lastFiltersRef.current !== filters) {
      setPreviousFilters(lastFiltersRef.current);
    }
    lastFiltersRef.current = filters;
  }, [filters, isInitialized]);


  useEffect(() => {
    const maxPage = Math.max(Math.ceil(totalHosts / rowsPerPage) - 1, 0);
    if (page > maxPage) setPage(maxPage);
  }, [page, rowsPerPage, totalHosts]);

  // -------------------------------------------------------------------------
  // DataTable columns — extracted to useHostColumns hook (v2.43.0 — MONO-1).
  // -------------------------------------------------------------------------

  // Pivot-from-cell: clicking a tag / service / OS value in a row narrows
  // the list to hosts sharing it, by merging into the structured filters the
  // HostFilters panel already drives (so the new filter shows up there too).
  const handleAddFilter = useCallback((pivot: HostFilterPivot) => {
    setFilters((prev) => {
      if (pivot.kind === 'tag') {
        if (prev.tags?.includes(pivot.value)) return prev;
        return { ...prev, tags: [...(prev.tags ?? []), pivot.value] };
      }
      if (pivot.kind === 'service') {
        if (prev.services?.includes(pivot.value)) return prev;
        return { ...prev, services: [...(prev.services ?? []), pivot.value] };
      }
      // OS is a single-value filter — replace.
      if (prev.osFilter === pivot.value) return prev;
      return { ...prev, osFilter: pivot.value };
    });
    setPage(0);
    toast.info(`Filtered to ${pivot.kind === 'os' ? 'OS' : pivot.kind} "${pivot.value}"`, { autoHideMs: 2000 });
  }, [setFilters, setPage, toast]);

  // The active port / service / version conditions, so each row can name the
  // endpoint that made it match (the Exposure chips are a risk read and may
  // not include it).
  const endpointMatch = useMemo(
    () => endpointMatchCriteria({
      ports: filters.ports,
      services: filters.services,
      portStates: filters.portStates,
      hasOpenPorts: filters.hasOpenPorts,
      query: filters.query,
    }),
    [filters.ports, filters.services, filters.portStates, filters.hasOpenPorts, filters.query],
  );

  const baseColumns = useHostColumns({
    updatingHostId,
    onFollowChange: handleFollowChange,
    // v2.44.1 (UX review #2): the keyboard path for the row-level
    // "open host inspector" action lives in the IP cell as a real
    // <button>.  The row's onRowClick still fires for mouse users
    // (DataTableShell convenience), but the row itself is no longer
    // focusable — semantically wrong as a link.
    onOpen: openInspector,
    onAddFilter: handleAddFilter,
    endpointMatch,
  });

  // v2.71.0 — prepend a checkbox column to drive the bulk-action bar.
  const columns = useMemo(
    () => [selectionColumn<Host>({ ariaLabel: (row) => `Select ${row.original.ip_address}` }), ...baseColumns],
    [baseColumns],
  );

  const selectedIds = useMemo(
    () => Object.keys(rowSelection).filter((id) => rowSelection[id]).map(Number),
    [rowSelection],
  );

  // IPs for the explicitly-checked rows (resolved against the loaded page),
  // for the bulk "Copy IPs" target-list action.  Page-scoped on purpose:
  // "select all matching" spans rows we haven't fetched, so the bulk bar
  // disables Copy IPs in that mode and points to the Tool-Ready export.
  const selectedIps = useMemo(() => {
    const idSet = new Set(selectedIds);
    return hosts.filter((h) => idSet.has(h.id)).map((h) => h.ip_address);
  }, [selectedIds, hosts]);

  const table = useDataTable<Host>({
    data: hosts,
    columns,
    getRowId: (host) => host.id.toString(),
    rowSelection,
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
    manualSorting: true,
    manualPagination: true,
    pageCount: Math.max(Math.ceil(totalHosts / rowsPerPage), 1),
  });

  // Memoize: buildHostQueryContext() returns a fresh object each call, so
  // calling it inline on every render handed ReportsDialog / ToolReadyOutput
  // / HostBulkBar a new prop reference every render, breaking their memo and
  // re-running their effects.  One stable reference per filter/page change.
  const exportQueryContext = useMemo(buildHostQueryContext, [buildHostQueryContext]);

  // After a failed refetch we keep the previous rows visible (to preserve scroll
  // position), but they may no longer match the active filters/query — so mark
  // them stale and pause actions that would act on possibly-mismatched rows
  // (bulk operations + export) until a fetch succeeds.
  const showingStaleResults = error !== null && hosts.length > 0;

  if (loading && !hosts.length && !hasFetchedOnceRef.current) {
    return <ListPageSkeleton titleWidth={180} actionCount={3} tableProps={{ rows: 10, columns: 6 }} />;
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  // Whether any row on this page shows a port-guessed service ("SSH?").
  const hasGuessedServices = hosts.some((h) => exposureChips(h.ports).some((c) => !c.detected));

  return (
    <div className="space-y-md">
      {/* Page header */}
      <div className="flex flex-col gap-md lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-page-title">Hosts</h1>
        </div>
        {/* Both exports are secondary: the page's work is triage in the
            table, so no header button is filled as the primary action. */}
        <div className="flex flex-col gap-xs sm:flex-row sm:items-center">
          <Button
            variant="outline"
            onClick={() => setToolReadyDialogOpen(true)}
            disabled={loading || totalHosts === 0 || showingStaleResults}
          >
            <Code className="size-4" aria-hidden />
            Export targets
          </Button>
          <Button
            variant="outline"
            onClick={() => setReportsDialogOpen(true)}
            disabled={loading || totalHosts === 0 || showingStaleResults}
          >
            <Download className="size-4" aria-hidden />
            Export hosts
          </Button>
        </div>
      </div>

      {filterDataError && (
        <Alert variant="warning">
          <AlertDescription className="flex items-center justify-between gap-sm">
            <span>{filterDataError}</span>
            <div className="flex items-center gap-xs">
              <Button
                variant="outline"
                size="sm"
                disabled={filterDataLoading}
                onClick={() => fetchFilterData(buildFacetParams())}
              >
                <RefreshCw className={`size-3.5 ${filterDataLoading ? 'animate-spin' : ''}`} aria-hidden />
                Retry
              </Button>
              <Button
                variant="ghost"
                size="sm"
                aria-label="Dismiss filter data warning"
                onClick={() => setFilterDataError(null)}
              >
                <X className="size-3.5" aria-hidden />
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      {filters.outOfScopeOnly && (
        <Alert variant="warning">
          <AlertDescription>
            Showing only hosts outside the configured scope: not in any scope subnet and not
            reachable through an in-scope name.
          </AlertDescription>
        </Alert>
      )}

      <HostCommandBar
        value={filters.query ?? ''}
        onChange={setQuery}
        onPin={handlePinQuery}
        onCopyLink={handleCopyLink}
        valueSuggestions={queryValueSuggestions}
        valueLabels={queryValueLabels}
      />

      {/* One toolbar (5.249.0) — view, filters, the single result count and the
          sort, then the applied conditions.  It replaced a sticky CARD that
          stacked a count, review chips, a default banner, a saved-view row and
          the filter chips: five control surfaces before the first host.  No
          card chrome, and the strip stays two or three lines tall. */}
      <div
        className="sticky z-10 space-y-xs border-b border-border bg-background py-xs"
        style={stickyBelowChrome}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-xs">
          <HostViewPicker
            builtInViews={HOST_BUILT_IN_VIEWS}
            savedViews={savedViews}
            savedViewsError={savedViewsError}
            activeViewId={activeViewId}
            activeBuiltInId={activeFilterPresetId(filters)}
            baseViewName={appliedProjectDefault ?? baseView?.name ?? null}
            hasConditions={activeFilterChips.length > 0}
            projectDefaultApplied={appliedProjectDefault !== null}
            canSetProjectDefault={canSetProjectDefault}
            projectDefaultName={projectDefaultView?.name ?? null}
            projectDefaultActive={projectDefaultActive}
            onApplyProjectDefault={applyProjectDefault}
            onAllHosts={clearAllFilters}
            onApplyBuiltIn={handleApplyBuiltIn}
            onApplyView={handleApplyView}
            onReset={baseView && activeViewId === null ? baseView.reapply : undefined}
            onSaveView={() => {
              setSaveViewName('');
              setSaveViewDialogOpen(true);
            }}
            onDeleteView={handleDeleteView}
            onToggleProjectDefault={handleToggleProjectDefault}
          />
          <HostFilterPopover
            open={filterOpen}
            onOpenChange={(open) => {
              setFilterOpen(open);
              // Always reopen on the catalog; a chip sets the field itself.
              if (!open) setFilterFieldId(null);
            }}
            fieldId={filterFieldId}
            onFieldChange={setFilterFieldId}
            filters={filters}
            onApply={handleFiltersChange}
            data={filterData}
            optionsLoading={filterDataLoading}
            optionsError={filterDataError !== null}
          />
          {/* v5.270.0 — team review status, the one filter used often enough
              to stay out of the catalog: a compact select in the toolbar
              (was its own row of chips). */}
          <Select
            value={followFilter}
            onValueChange={(value) => {
              setFollowFilter(value as 'all' | 'none' | FollowStatus);
              setPage(0);
            }}
          >
            <SelectTrigger
              className={cn('h-8 w-auto gap-xs text-caption', followFilter !== 'all' && 'border-primary text-foreground')}
              aria-label="Review status filter"
            >
              <span className="text-muted-foreground">Review:</span>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any</SelectItem>
              {/* Nobody on the team has this host In Review or Reviewed
                  (team-shared, follow:none). */}
              <SelectItem value="none">Not started</SelectItem>
              {FOLLOW_STATUS_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* The one result count: the listing's own total, so it always agrees
              with the table and the exports. */}
          <p className="ml-auto shrink-0 text-metadata text-muted-foreground" aria-live="polite">
            <strong className="tabular-nums text-foreground">{totalHosts.toLocaleString()}</strong>{' '}
            {activeFilterChips.length > 0 ? 'matching ' : ''}host{totalHosts === 1 ? '' : 's'}
          </p>
          <div className="flex shrink-0 items-center gap-xs">
            {/* Sort is a display choice, never a filter — it has no chip. */}
            <Label htmlFor="hosts-sort" className="text-metadata text-muted-foreground">Sort</Label>
                <Select
                  value={sortBy}
                  onValueChange={(value) => {
                    setSortBy(value as HostSortOption);
                    setPage(0);
                  }}
                >
                  <SelectTrigger id="hosts-sort" className="h-8 w-[15rem] text-caption">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="critical_desc">Critical scanner observations</SelectItem>
                    <SelectItem value="exploitable_desc">Exploit reported first</SelectItem>
                    <SelectItem value="open_ports_desc">Open ports</SelectItem>
                    <SelectItem value="discoveries_desc">Most discoveries</SelectItem>
                    <SelectItem value="notes_desc">Most notes</SelectItem>
                    <SelectItem value="ip_asc">IP address</SelectItem>
                    <SelectItem value="hostname_asc">Hostname</SelectItem>
                  </SelectContent>
                </Select>
          </div>
        </div>

        {/* A default the operator did not choose must never hide hosts
            silently; the way out is one click. */}
        {appliedProjectDefault && (
          <div className="flex min-w-0 items-center gap-xs text-caption text-muted-foreground">
            <Star className="size-3.5 shrink-0 fill-current text-warning" aria-hidden />
            <span className="truncate">
              Project default view applied: <strong className="text-foreground">{appliedProjectDefault}</strong>
            </span>
            <Button variant="ghost" size="sm" className="h-6 shrink-0" onClick={clearAllFilters}>
              Show all hosts
            </Button>
          </div>
        )}
        {/* …and the way back is one click too, once the filters were cleared
            or changed. */}
        {projectDefaultView && !projectDefaultActive && (
          <div className="flex min-w-0 items-center gap-xs text-caption text-muted-foreground">
            <Star className="size-3.5 shrink-0 text-warning" aria-hidden />
            <span className="truncate">
              Project default view: <strong className="text-foreground">{projectDefaultView.name}</strong>
            </span>
            <Button variant="ghost" size="sm" className="h-6 shrink-0" onClick={applyProjectDefault}>
              Back to default view
            </Button>
          </div>
        )}

        {/* v5.290.0 — a bare /hosts visit reopens the session's filters (by
            design); a nav link landing on a filtered list must say why. */}
        {showRestoredNotice && (
          <div
            className="flex min-w-0 items-center gap-xs text-caption text-muted-foreground"
            data-testid="hosts-restored-notice"
          >
            <span className="truncate">Restored your last filters</span>
            <span aria-hidden>·</span>
            <Button variant="ghost" size="sm" className="h-6 shrink-0" onClick={clearAllFilters}>
              Clear
            </Button>
          </div>
        )}

        {/* Applied conditions.  Capped while the strip is sticky — an unbounded
            chip list would grow the pinned area over the table. */}
        {activeFilterChips.length > 0 && (
            <div className="flex flex-wrap items-center gap-xs">
              <span className="text-caption text-muted-foreground" title="A host must match every condition. Within one condition, any of its values matches.">
                Matching all of:
              </span>
              {(chipsExpanded ? activeFilterChips : activeFilterChips.slice(0, MAX_STICKY_CHIPS)).map((chip) => (
                <span
                  key={chip.key}
                  className="inline-flex max-w-full items-center gap-xxs rounded-chip border border-border bg-card px-sm py-px text-caption font-medium"
                >
                  {/* The label edits, the × removes — the same editor "+ Add
                      filter" opens.  The query / text-search / host-state chips
                      have no structured editor and stay plain text. */}
                  {chip.fieldId ? (
                    <button
                      type="button"
                      className="truncate rounded-sm text-left hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      title={`${chip.title ?? chip.label} — click to edit`}
                      onClick={() => openFilterEditor(chip.fieldId!)}
                    >
                      {chip.label}
                    </button>
                  ) : (
                    <span className="truncate" title={chip.title ?? chip.label}>{chip.label}</span>
                  )}
                  {chip.onDelete && (
                    <button
                      type="button"
                      onClick={chip.onDelete}
                      aria-label={`Clear filter: ${chip.label}`}
                      className="inline-flex size-6 shrink-0 items-center justify-center rounded-sm hover:bg-accent"
                    >
                      <X className="size-3" aria-hidden />
                    </button>
                  )}
                </span>
              ))}
              {activeFilterChips.length > MAX_STICKY_CHIPS && (
                <Button variant="ghost" size="sm" className="h-6" onClick={() => setChipsExpanded((v) => !v)}>
                  {chipsExpanded ? 'Show fewer' : `Show all conditions (${activeFilterChips.length})`}
                </Button>
              )}
              <Button variant="ghost" size="sm" className="h-6" onClick={clearAllFilters}>
                Clear filters
              </Button>
            </div>
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-sm">
            <span>
              {error}
              {showingStaleResults &&
                ' Showing previous results — they may not match the current filters; bulk actions and export are paused until a refresh succeeds.'}
            </span>
            <Button variant="outline" size="sm" onClick={() => fetchHosts()}>
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {vulnError && !error && (
        <Alert variant="warning">
          <AlertDescription>
            Vulnerability data could not be loaded. Vulnerability counts shown below may be incomplete
            or missing.
          </AlertDescription>
        </Alert>
      )}

      {/* v2.86.10 — only show the inline loader on the INITIAL fetch
          (when we have no hosts yet).  Pre-fix any filter change called
          ``setLoading(true)``, which collapsed the table out for a
          small centered spinner — content under the user's scroll
          position vanished and the browser snapped to the top.  By
          keeping the previous table visible during refetch, the
          scroll position is preserved; the table content updates in
          place when the new data arrives.  The initial-mount
          PageSkeleton (line 1275) is already gated on the same
          ``!hosts.length`` condition. */}
      {loading && hosts.length === 0 ? (
        <InlineLoader label="Loading hosts…" centered />
      ) : hosts.length === 0 && activeFilterChips.length > 0 ? (
        <div className="space-y-xs py-xl text-center">
          <Computer className="mx-auto size-12 text-muted-foreground" aria-hidden />
          <h2 className="text-section-title text-muted-foreground">
            No hosts match the current filters
          </h2>
          {/* No guess at WHICH condition emptied the list — only the ways back. */}
          <p className="text-metadata text-muted-foreground">
            {activeFilterChips.length} condition{activeFilterChips.length === 1 ? '' : 's'} applied, and a host
            must match all of them. Edit one from its chip above, go back a step, or clear them.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-xs">
            {previousFilters && (
              <Button
                onClick={() => {
                  setFilters(previousFilters);
                  setPage(0);
                }}
              >
                Undo last change
              </Button>
            )}
            <Button variant={previousFilters ? 'outline' : 'default'} onClick={clearAllFilters}>
              Clear filters
            </Button>
          </div>
        </div>
      ) : hosts.length === 0 ? (
        <div className="space-y-xs py-xl text-center">
          <Computer className="mx-auto size-12 text-muted-foreground" aria-hidden />
          <h2 className="text-section-title text-muted-foreground">No hosts found</h2>
          <p className="text-metadata text-muted-foreground">
            Upload a scan to discover hosts on your network.
          </p>
          <Button variant="outline" onClick={() => navigate('/scans')}>
            Go to Scans
          </Button>
        </div>
      ) : (
        <>
          {/* Bulk-action bar — shown once one or more rows are selected.
              Hidden while results are stale (a failed refetch): acting on rows
              that may not match the active query is the trap this guards. */}
          {/* v5.290.0 — the bar's slot is always rendered at a fixed height:
              inserting the bar on the first tick pushed every row ~40px down,
              so the second checkbox click landed on the wrong host. */}
          <div className="h-11 min-w-0" role="region" aria-label="Bulk actions" data-testid="hosts-bulk-slot">
            {selectedIds.length > 0 && !showingStaleResults ? (
              <HostBulkBar
                selectedIds={selectedIds}
                selectedIps={selectedIps}
                totalMatching={totalHosts}
                queryContext={exportQueryContext}
                onClear={() => setRowSelection({})}
                onApplied={() => {
                  setRowSelection({});
                  fetchHosts();
                  fetchFilterData(buildFacetParams());
                }}
              />
            ) : (
              <p className="flex h-full min-w-0 items-center rounded-control border border-dashed border-border px-sm text-caption text-muted-foreground">
                <span className="truncate">
                  {showingStaleResults
                    ? 'Bulk actions are paused until a refresh succeeds.'
                    : 'Select rows to act on them — copy IPs, tag, assign, review or plan.'}
                </span>
              </p>
            )}
          </div>
          {/* Host table — sole renderer (desktop-only product; horizontal
              scroll handles narrow widths, no separate mobile card view).
              Dimmed (not interaction-blocked) while stale: drill-down into a
              single host is harmless + refetches, only bulk/export are paused. */}
          {/* v5.270.0 — the "?" on a service chip, said once for the page. */}
          {/* The row keys, said once for the page: the state dot beside each
              IP, and (when present) the "?" on a service chip. */}
          <p className="flex flex-wrap items-center gap-x-md gap-y-xxs text-caption text-muted-foreground" data-testid="hosts-legend">
            <span className="inline-flex items-center gap-xxs">
              <span className="inline-block size-2 shrink-0 rounded-full bg-success" aria-hidden />
              up
            </span>
            <span className="inline-flex items-center gap-xxs">
              <span className="inline-block size-2 shrink-0 rounded-full bg-destructive" aria-hidden />
              down
            </span>
            <span className="inline-flex items-center gap-xxs">
              <span className="inline-block size-2 shrink-0 rounded-full border border-muted-foreground/50" aria-hidden />
              state unknown (liveness not confirmed, e.g. masscan / naabu / DNS)
            </span>
            {hasGuessedServices && (
              <span>
                <span className="rounded-chip border border-dashed border-border px-xs py-px text-foreground">SSH?</span>{' '}
                a service with “?” was guessed from its port number; no scanner probed it.
              </span>
            )}
          </p>
          <div
            className={cn(showingStaleResults && 'opacity-60 transition-opacity')}
            aria-busy={showingStaleResults || undefined}
          >
            <DataTableShell<Host>
              table={table}
              onRowClick={(host) => openInspector(host.id)}
              // v4.46.0 — the expandable sub-row was removed.  It predated the
              // side-sheet inspector (which superseded it as the drill-down)
              // and had become a third competing interaction on every row:
              // most of its content already appeared in the collapsed columns,
              // and everything unique to it (web links, discovery chips, the
              // latest note) lives in the inspector in richer form.
              // v5.270.0 — the test-workflow state is a word beside the IP
              // (useHostColumns.testWorkState), not a coloured left border
              // explained only by a hover title.  The keyboard cursor row (1c)
              // gets a highlight + a `host-cursor-row` marker class the
              // scroll-into-view effect keys off.
              getRowClassName={(row) => (
                row.index === cursorIndex
                  ? 'host-cursor-row bg-accent ring-1 ring-inset ring-ring'
                  : undefined
              )}
              bare
              // The four sized columns take 790px; the floor keeps the Host
              // column (the only unsized one) at ~270px in a narrowed window,
              // where the wrapper scrolls sideways instead of crushing it.
              tableClassName="table-fixed min-w-[1060px]"
            />
          </div>

          <div className="border-t border-border pt-xs">
            <DataTablePagination<Host>
              pageIndex={page}
              pageSize={rowsPerPage}
              totalCount={totalHosts}
              onPageChange={setPage}
              onPageSizeChange={(size) => {
                setRowsPerPage(size);
                setPage(0);
              }}
            />
          </div>
        </>
      )}

      <ReportsDialog
        open={reportsDialogOpen}
        onClose={() => setReportsDialogOpen(false)}
        filters={exportQueryContext}
        totalHosts={totalHosts}
      />

      <ToolReadyOutput
        open={toolReadyDialogOpen}
        onClose={() => setToolReadyDialogOpen(false)}
        filters={exportQueryContext}
        totalHosts={totalHosts}
        selectedCount={selectedIds.length}
      />

      <Dialog
        open={saveViewDialogOpen}
        onOpenChange={(open) => {
          if (!open && !saveViewBusy) setSaveViewDialogOpen(false);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save current view</DialogTitle>
            <DialogDescription>
              Save the current filter set as a named view you can re-apply with one click later.
              Views are personal — only you see them.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-xxs">
            <Label htmlFor="hosts-save-view-name">View name</Label>
            <Input
              id="hosts-save-view-name"
              autoFocus
              placeholder="e.g. Critical web hosts"
              value={saveViewName}
              maxLength={120}
              disabled={saveViewBusy}
              onChange={(event) => setSaveViewName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && saveViewName.trim() && !saveViewBusy) {
                  handleSaveView();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setSaveViewDialogOpen(false)}
              disabled={saveViewBusy}
            >
              Cancel
            </Button>
            <Button
              onClick={handleSaveView}
              disabled={!saveViewName.trim() || saveViewBusy}
            >
              {saveViewBusy ? (
                <>
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                  Saving…
                </>
              ) : (
                'Save view'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Host inspector — opens when a list row is clicked.  Non-modal
          slide-over (modal={false}) keeps the list interactive behind
          so operators can scroll / filter without losing the open
          inspector.  "Open standalone" deep-links to /hosts/:id with
          the same navState the old direct-navigate flow built. */}
      <SideSheet
        open={inspectedHostId !== null}
        onOpenChange={(open) => {
          if (open) return;
          void confirmDiscardDraft().then((ok) => {
            if (ok) setInspectedHostId(null);
          });
        }}
      >
        <SideSheetContent width="xl">
          <SideSheetHeader>
            <div className="flex items-center justify-between gap-sm pr-xl">
              <SideSheetTitle>
                Host inspector
                {inspectedHostId !== null && totalHosts > 0 && inspectedAbsoluteIndex >= 0 && (
                  <span className="ml-xs text-caption font-normal text-muted-foreground">
                    {inspectedAbsoluteIndex + 1} of {totalHosts} in this queue
                  </span>
                )}
                {inspectedOutsideRows && (
                  <span
                    className="ml-xs text-caption font-normal text-warning"
                    title="A filter change or a page turn moved this host out of the rows shown. It stays open until you move on; Next opens the first host in the list as it is now."
                  >
                    outside the rows shown
                  </span>
                )}
              </SideSheetTitle>
              <div className="flex items-center gap-xxs">
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Previous host (k)"
                  disabled={!hasInspectorPrev}
                  onClick={() => stepInspector(-1)}
                >
                  <ChevronLeft className="size-4" aria-hidden />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Next host (j)"
                  disabled={!hasInspectorNext}
                  onClick={() => stepInspector(1)}
                >
                  <ChevronRight className="size-4" aria-hidden />
                </Button>
                {/* Walk the queue: jump to the next host still needing review,
                    skipping Reviewed ones and crossing pages (key: n). */}
                <Button
                  variant="outline"
                  size="sm"
                  aria-label="Next unreviewed host (n)"
                  title="Next host still needing review (n)"
                  disabled={!hasInspectorNext}
                  onClick={stepToNextUnreviewed}
                >
                  <SkipForward className="size-3.5" aria-hidden />
                  Next unreviewed
                </Button>
                {inspectedHostId !== null && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={async () => {
                      const id = inspectedHostId;
                      if (!(await confirmDiscardDraft())) return;
                      setInspectedHostId(null);
                      navigateToStandalone(id);
                    }}
                  >
                    <ExternalLink className="size-3.5" aria-hidden />
                    Open standalone
                  </Button>
                )}
              </div>
            </div>
          </SideSheetHeader>
          <SideSheetBody>
            {inspectedHostId !== null && (
              // Audit H17: dropping `key={inspectedHostId}` so the
              // SideSheet's HostInspector stays mounted across
              // prev/next.  Inspector already guards stale fetches
              // with fetchIdRef, so the previous host is visible
              // while the next loads instead of a "Loading host
              // details…" flash per click.
              <HostInspector
                hostId={inspectedHostId}
                density="sheet"
                onFollowChange={(id, follow) =>
                  setHosts((prev) => prev.map((h) => (h.id === id ? { ...h, follow } : h)))
                }
                onDirtyChange={(dirty) => {
                  inspectorDirtyRef.current = dirty;
                }}
                onNextUnreviewed={() => void stepToNextUnreviewed()}
                onQueryHosts={async (q) => {
                  if (!(await confirmDiscardDraft())) return;
                  // Close the sheet, then REPLACE the filter state with just
                  // this query. navigate() does NOT work here: the Hosts page
                  // is already mounted, so its URL→filter restore (init-only)
                  // won't re-parse the new ?q=, and the URL-write effect would
                  // clobber it straight back. Setting filter state is the
                  // in-page path — it drives the refetch, and the write effect
                  // syncs ?q= to the URL. Replacing (not merging) matches the
                  // standalone fresh-view behavior.
                  setInspectedHostId(null);
                  handleFiltersChange({ query: q });
                }}
              />
            )}
          </SideSheetBody>
        </SideSheetContent>
      </SideSheet>

      {confirmEl}
    </div>
  );
}
