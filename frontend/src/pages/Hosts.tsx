import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  Bookmark,
  BookmarkPlus,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Code,
  Computer,
  Download,
  ExternalLink,
  Eye,
  Loader2,
  Network,
  RefreshCw,
  Server,
  Shield,
  SkipForward,
  SlidersHorizontal,
  StickyNote,
  Crosshair,
  Star,
  Users,
  Wand2,
  X,
} from 'lucide-react';
import { ColumnDef, ExpandedState, Row, RowSelectionState } from '@tanstack/react-table';
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
  HostDiscovery,
  HostFilterView,
  HostFilterData,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { asAxiosError, formatApiError } from '../utils/apiErrors';
import HostFilters, { HostFilterOptions } from '../components/HostFilters';
import HostCommandBar from '../components/hosts/HostCommandBar';
import { dslFromFilters, type DslConversion } from '../components/hosts/dslFromFilters';
import { ConfirmDialog } from '../components/ui/confirm-dialog';
import {
  FOLLOW_STATUS_OPTIONS,
  FollowMenu,
  formatRelativeLastViewed,
  getLatestDiscovery,
  getScanLabel,
  getTopServices,
  useHostColumns,
  type HostFilterPivot,
} from '../components/hosts/useHostColumns';
import ReportsDialog from '../components/ReportsDialog';
import ToolReadyOutput from '../components/ToolReadyOutput';
import { ListPageSkeleton } from '../components/PageSkeleton';
import { InlineLoader } from '../components/ui/inline-loader';
import { PORTS_OF_INTEREST_SET, PORTS_OF_INTEREST } from '../utils/portsOfInterest';
import { getHostWebLinks } from '../utils/webLinks';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { projectScopedKey } from '../utils/scopedStorage';
import { cn } from '../utils/cn';
import { copyToClipboard } from '../utils/clipboard';
import { stickyBelowChrome } from '../utils/uiStyles';
import { useConfirm } from '../hooks/useConfirm';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Checkbox } from '../components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../components/ui/dropdown-menu';
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
// Helpers re-exported here so anything in this file or HostExpandedRow
// that still needs them keeps working.

type HostSortOption =
  | 'critical_desc'
  | 'exploitable_desc'
  | 'open_ports_desc'
  | 'notes_desc'
  | 'discoveries_desc'
  | 'ip_asc'
  | 'hostname_asc';

// v4.51.0 — QuickViewPreset / QUICK_VIEW_PRESETS / matchesQuickViewPreset
// retired.  Preset list + active-preset detection now lives in
// HostFilters.tsx (`HOST_FILTER_PRESETS`, `activeFilterPresetId`) since
// both preset surfaces were consolidated into the HostFilters card.
// followFilter + onlyWithNotes were folded into HostFilterOptions in
// the same pass, so the page state is a single object instead of
// three useStates.

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
  // v5.0.0 — boolean query DSL; ANDs with the structured params above.
  q?: string;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
};

// Local helper kept here — not column-specific, used only in scan-meta tooltips.
const formatDateTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString() : 'Unknown date';

const stateBadgeClass = (state: string | null): string => {
  switch (state) {
    case 'up':
      return 'bg-success text-success-foreground border-transparent';
    case 'down':
      return 'bg-destructive text-destructive-foreground border-transparent';
    default:
      return 'border-border text-muted-foreground';
  }
};

// Mirrors the canonical severity tokens (utils/severity SEVERITY_HSL):
// critical=destructive, high=warning, medium=info, low=success, info=muted.
// (Previously medium read amber and low read blue — off from every other
// severity surface in the app.)
const severityChipClasses: Record<'critical' | 'high' | 'medium' | 'low' | 'info', string> = {
  critical: 'bg-destructive text-destructive-foreground',
  high: 'bg-warning text-warning-foreground',
  medium: 'bg-info text-info-foreground',
  low: 'bg-success text-success-foreground',
  info: 'bg-muted text-muted-foreground',
};

// FollowMenu moved to ../components/hosts/useHostColumns.tsx (v2.43.0 MONO-1).

/**
 * Body of the expandable per-row detail section — shared between the
 * desktop DataTable sub-row and the mobile card collapse.
 */
const HostExpandedRow: React.FC<{
  host: Host;
  vulnError: boolean;
  onOpenScan: (scanId: number) => void;
}> = ({ host, vulnError, onOpenScan }) => {
  const openPorts = host.ports?.filter((port) => port.state === 'open') || [];
  const portsOfInterest = openPorts.filter((port) => PORTS_OF_INTEREST_SET.has(port.port_number));
  const webLinks = getHostWebLinks(host);
  const discoveries = host.discoveries ?? [];
  const visibleDiscoveries = discoveries.slice(0, 6);
  const latestNote = host.notes && host.notes.length > 0 ? host.notes[0] : undefined;
  const latestNotePreview = latestNote?.body
    ? `${latestNote.body.slice(0, 220)}${latestNote.body.length > 220 ? '…' : ''}`
    : null;
  const relativeViewed = formatRelativeLastViewed(host.follow?.last_viewed_at);
  const noteCount = host.note_count ?? host.notes?.length ?? 0;
  const vulnSummary = host.vulnerability_summary;

  return (
    <div className="grid gap-md lg:grid-cols-2">
      <div className="min-w-0 space-y-xs">
        <h4 className="text-metadata font-semibold uppercase tracking-wider text-muted-foreground">
          Host context
        </h4>
        {/* State is the only true categorical signal here, so it's
            the only chip; counts and metadata (open ports, notes, OS,
            last viewed) collapse to a muted subtitle so the row reads
            like a row, not a chip cluster. */}
        <div className="flex flex-wrap items-center gap-xs">
          {host.state && (
            <Badge variant="outline" className={cn(stateBadgeClass(host.state))}>
              {host.state}
            </Badge>
          )}
          <span className="text-caption text-muted-foreground">
            {openPorts.length} open port{openPorts.length === 1 ? '' : 's'}
            {' · '}
            {noteCount} note{noteCount === 1 ? '' : 's'}
            {host.os_name && <> · {host.os_name}</>}
            {relativeViewed && <> · viewed {relativeViewed}</>}
          </span>
        </div>
        {latestNotePreview && (
          <p className="text-metadata italic text-muted-foreground">“{latestNotePreview}”</p>
        )}
        {webLinks.length > 0 && (
          <div className="flex flex-wrap gap-xs">
            {webLinks.slice(0, 3).map((link) => (
              <a
                key={`${host.id}-${link.protocol}-${link.port}`}
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(event) => event.stopPropagation()}
                className="inline-flex items-center gap-xxs rounded-chip border border-border px-xs py-px text-micro font-semibold uppercase tracking-wider text-foreground hover:bg-accent"
              >
                {link.protocol.toUpperCase()} {link.port}
              </a>
            ))}
          </div>
        )}
        {portsOfInterest.length > 0 && (
          <div className="flex flex-wrap gap-xs">
            {portsOfInterest.map((port) => {
              const definition = PORTS_OF_INTEREST.find(
                (entry) => entry.port === port.port_number,
              );
              return (
                <Badge
                  key={`${host.id}-poi-${port.port_number}`}
                  variant="warning"
                  title={definition?.label || 'High-value port'}
                >
                  {port.port_number}/{port.service_name || 'unknown'}
                </Badge>
              );
            })}
          </div>
        )}
      </div>

      <div className="min-w-0 space-y-sm">
        <div>
          <h4 className="mb-xs text-metadata font-semibold uppercase tracking-wider text-muted-foreground">
            Discoveries
          </h4>
          {visibleDiscoveries.length > 0 ? (
            <div className="flex flex-wrap gap-xs">
              {visibleDiscoveries.map((discovery) => (
                <button
                  key={`${host.id}-scan-${discovery.scan_id}`}
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpenScan(discovery.scan_id);
                  }}
                  title={`${getScanLabel(discovery)} • ${formatDateTime(discovery.discovered_at)}${
                    discovery.scan_type ? ` • ${discovery.scan_type}` : ''
                  }`}
                  className="inline-flex max-w-[16rem] items-center gap-xxs rounded-chip border border-border px-xs py-px text-micro font-semibold uppercase tracking-wider text-foreground hover:bg-accent"
                >
                  <span className="truncate">{getScanLabel(discovery)}</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="text-metadata text-muted-foreground">No discovery history available.</p>
          )}
        </div>

        <div>
          <h4 className="mb-xs text-metadata font-semibold uppercase tracking-wider text-muted-foreground">
            Vulnerabilities
          </h4>
          {vulnSummary && vulnSummary.total_vulnerabilities > 0 ? (
            <div className="flex flex-wrap gap-xs">
              {vulnSummary.critical > 0 && (
                <Badge className={severityChipClasses.critical}>
                  {vulnSummary.critical} Critical
                </Badge>
              )}
              {vulnSummary.high > 0 && (
                <Badge className={severityChipClasses.high}>{vulnSummary.high} High</Badge>
              )}
              {vulnSummary.medium > 0 && (
                <Badge className={severityChipClasses.medium}>
                  {vulnSummary.medium} Medium
                </Badge>
              )}
              {vulnSummary.low > 0 && (
                <Badge className={severityChipClasses.low}>{vulnSummary.low} Low</Badge>
              )}
              {vulnSummary.info > 0 && (
                <Badge className={severityChipClasses.info}>{vulnSummary.info} Info</Badge>
              )}
            </div>
          ) : vulnError ? (
            <p className="text-metadata text-warning">
              Vulnerability data unavailable — the vulnerability subsystem encountered an error.
            </p>
          ) : (
            <p className="text-metadata text-muted-foreground">
              No vulnerability findings recorded for this host.
            </p>
          )}
        </div>
      </div>
    </div>
  );
};

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
  const [toolReadyDialogOpen, setToolReadyDialogOpen] = useState(false);
  const [updatingHostId, setUpdatingHostId] = useState<number | null>(null);
  // v4.51.0 — followFilter + onlyWithNotes now live inside `filters`
  // (see HostFilterOptions).  Reads use `filters.followFilter ?? 'all'`
  // and `filters.onlyWithNotes === true`; writes go through
  // setFollowFilter/setOnlyWithNotes helpers below so the chip
  // handlers and saved-view restore paths still feel like setters.
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
  const setOnlyWithNotes = useCallback((next: boolean) => {
    setFilters((previous) => {
      const updated = { ...previous };
      if (next) {
        updated.onlyWithNotes = true;
      } else {
        delete updated.onlyWithNotes;
      }
      return updated;
    });
  }, [setFilters]);
  const [isInitialized, setIsInitialized] = useState(false);
  const [sortBy, setSortBy] = useState<HostSortOption>('critical_desc');
  const [vulnError, setVulnError] = useState(false);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const [expanded, setExpanded] = useState<ExpandedState>({});
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // Saved Hosts page filter views (per-user, per-project).
  const [savedViews, setSavedViews] = useState<HostFilterView[]>([]);
  const [savedViewsError, setSavedViewsError] = useState<boolean>(false);
  const [saveViewDialogOpen, setSaveViewDialogOpen] = useState(false);
  const [saveViewName, setSaveViewName] = useState('');
  const [saveViewBusy, setSaveViewBusy] = useState(false);
  const [activeViewId, setActiveViewId] = useState<number | null>(null);
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

  // v2.86.0 — extended to include 'tags' and 'subnetLabels' so the
  // per-chip ✕ delete button can remove individual tag/label values.
  // Pre-v2.86.0 the type listed only the legacy six array filters; the
  // chip-render block at :973 already drew tag chips but ✕ was a no-op
  // because removeListFilterValue couldn't accept the key.
  type ArrayFilterKeys =
    | 'ports' | 'services' | 'portStates' | 'subnets' | 'scanIds' | 'tech'
    | 'tags' | 'subnetLabels' | 'sites';

  const clearFilterKey = useCallback(
    (key: keyof HostFilterOptions) => {
      setFilters((previous) => {
        if (previous[key] === undefined) return previous;
        const updated = { ...previous } as HostFilterOptions;
        delete (updated as any)[key];
        return updated;
      });
    },
    [setFilters],
  );

  const removeListFilterValue = useCallback(
    (key: ArrayFilterKeys, value: string) => {
      setFilters((previous) => {
        const current = (previous[key] as string[] | undefined) ?? [];
        if (!current.includes(value)) return previous;
        const nextList = current.filter((item) => item !== value);
        const updated = { ...previous } as HostFilterOptions;
        if (nextList.length > 0) {
          (updated as any)[key] = nextList;
        } else {
          delete (updated as any)[key];
        }
        return updated;
      });
    },
    [setFilters],
  );

  const clearAllFilters = useCallback(() => {
    setFilters({});
    setPage(0);
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
      setRowSelection({});
    }
  }, [filterSignature, isInitialized]);

  const hostsAbortRef = useRef<AbortController | null>(null);
  const filterAbortRef = useRef<AbortController | null>(null);
  // True once the first host fetch has completed.  Gates the full-page
  // skeleton so it only shows on initial load — never on a refetch whose
  // current result happens to be empty (e.g. toggling a filter that matches
  // 0 hosts, or rapid toggling), which would otherwise replace the whole
  // page and snap the scroll to the top.
  const hasFetchedOnceRef = useRef(false);

  const fetchHosts = async () => {
    hostsAbortRef.current?.abort();
    const controller = new AbortController();
    hostsAbortRef.current = controller;
    try {
      setLoading(true);
      setError(null);
      const data = await getHosts(buildFilterParams(), controller.signal);
      if (!controller.signal.aborted) {
        setHosts(data.items);
        setTotalHosts(data.total ?? 0);
        setVulnError(data.vulnerability_error ?? false);
      }
    } catch (err: unknown) {
      { const e = asAxiosError(err); if (e.name === 'CanceledError' || e.code === 'ERR_CANCELED') return; }
      console.error('Error fetching hosts:', err);
      setError(formatApiError(err, 'Failed to fetch hosts. Please try again.'));
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
        hasFetchedOnceRef.current = true;
      }
    }
  };

  const fetchFilterData = async (
    params?: Record<string, string | boolean | number | undefined>,
  ) => {
    filterAbortRef.current?.abort();
    const controller = new AbortController();
    filterAbortRef.current = controller;
    setFilterDataLoading(true);
    try {
      const data = await getHostFilterData(params, controller.signal);
      if (!controller.signal.aborted) {
        setFilterData(data);
        setFilterDataError(null);
      }
    } catch (err: unknown) {
      { const e = asAxiosError(err); if (e.name === 'CanceledError' || e.code === 'ERR_CANCELED') return; }
      console.error('Error fetching filter data:', err);
      if (!controller.signal.aborted) {
        setFilterDataError(
          formatApiError(err, 'Filter options failed to refresh — dropdowns may be stale.'),
        );
      }
    } finally {
      if (!controller.signal.aborted) setFilterDataLoading(false);
    }
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

  // Panel-open state lives up here (above the cascading-facet effect) so the
  // effect can gate on it.
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Cascading refresh: when filters change (debounced 400ms), refetch
  // facet counts so the combobox trailing-count chips reflect the new
  // result set.  Pre-audit (H18) this depended on the whole
  // `buildFilterParams` callback, whose identity changed on sort, page,
  // and rowsPerPage edits — none of which should invalidate the
  // dropdown options.  Now depends only on the actual filter-shape
  // inputs.  Also gated on `filterData` having already loaded once, so
  // the initial post-load fetch above isn't double-fired.
  //
  // #49 — those cascading counts only render inside the filter panel's
  // comboboxes, so only refetch while the panel is open.  When it's closed
  // (filtering via the sticky review chips / query bar), a filter change no
  // longer fires a heavy facet query; opening the panel re-runs this effect
  // and refreshes the counts.  Chip labels rely on the one-shot initial load
  // (names don't change with filters), so they're unaffected.
  useEffect(() => {
    if (filterData === null || !advancedOpen) return;
    const timer = setTimeout(() => {
      fetchFilterData(buildFacetParams());
    }, 400);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional narrowing per audit H18
  }, [filters, advancedOpen]);

  useEffect(() => {
    if (isInitialized) return;
    const urlParams = new URLSearchParams(location.search);

    // A shared link must reproduce the SENDER's result set, not blend with the
    // recipient's previous session filters. So if the URL carries any recognized
    // host parameter, treat it as authoritative and ignore sessionStorage
    // entirely; fall back to the saved session only on a bare /hosts visit.
    const HOST_URL_PARAMS = [
      'search', 'q', 'state', 'os_filter', 'subnets', 'ports', 'services',
      'port_states', 'scan_ids', 'tags', 'subnet_labels', 'sites', 'out_of_scope_only',
      'out_of_scope', 'has_open_ports', 'first_seen_in_scan', 'has_critical_vulns',
      'has_high_vulns', 'has_medium_vulns', 'has_low_vulns',
      'has_exploit_available', 'has_test_execution',
      'has_web_interface', 'tech', 'follow_status', 'follow',
      'with_notes_only', 'with_notes', 'assigned_to', 'sort_by', 'sort_order',
    ];
    const urlIsAuthoritative = HOST_URL_PARAMS.some((p) => urlParams.has(p));

    let savedState: {
      filters?: HostFilterOptions;
      followFilter?: 'all' | 'none' | FollowStatus;
      onlyWithNotes?: boolean;
    } | null = null;
    if (!urlIsAuthoritative && typeof window !== 'undefined') {
      try {
        const raw = sessionStorage.getItem(projectScopedKey('hostFiltersState'));
        savedState = raw ? JSON.parse(raw) : null;
      } catch {
        savedState = null;
      }
    }

    const initialFilters: HostFilterOptions = savedState?.filters ? { ...savedState.filters } : {};

    const applyListParam = (param: string, key: keyof HostFilterOptions) => {
      if (urlParams.has(param)) {
        const raw = urlParams.get(param) || '';
        if (raw) {
          const values = raw
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean);
          (initialFilters as any)[key] = values;
        } else {
          delete (initialFilters as any)[key];
        }
      }
    };

    const applyStringParam = (param: string, key: keyof HostFilterOptions) => {
      if (urlParams.has(param)) {
        const value = urlParams.get(param);
        if (value) {
          (initialFilters as any)[key] = value;
        } else {
          delete (initialFilters as any)[key];
        }
      }
    };

    applyStringParam('search', 'search');
    applyStringParam('q', 'query');
    applyStringParam('state', 'state');
    applyStringParam('os_filter', 'osFilter');
    applyListParam('subnets', 'subnets');
    applyListParam('ports', 'ports');
    applyListParam('services', 'services');
    applyListParam('port_states', 'portStates');
    applyListParam('scan_ids', 'scanIds');
    applyListParam('tags', 'tags');
    applyListParam('subnet_labels', 'subnetLabels');
    applyListParam('sites', 'sites');

    if (urlParams.has('out_of_scope_only') || urlParams.has('out_of_scope')) {
      initialFilters.outOfScopeOnly =
        (urlParams.get('out_of_scope_only') ?? urlParams.get('out_of_scope')) === 'true';
    } else if (savedState?.filters?.outOfScopeOnly) {
      initialFilters.outOfScopeOnly = true;
    }

    if (urlParams.has('has_open_ports')) {
      initialFilters.hasOpenPorts = urlParams.get('has_open_ports') === 'true';
    } else if (savedState?.filters?.hasOpenPorts !== undefined) {
      initialFilters.hasOpenPorts = savedState.filters.hasOpenPorts;
    }

    if (urlParams.has('first_seen_in_scan')) {
      initialFilters.firstSeenInSelectedScans = urlParams.get('first_seen_in_scan') === 'true';
    } else if (savedState?.filters?.firstSeenInSelectedScans) {
      initialFilters.firstSeenInSelectedScans = true;
    }

    if (urlParams.has('has_critical_vulns')) {
      initialFilters.hasCriticalVulns = urlParams.get('has_critical_vulns') === 'true';
    } else if (savedState?.filters?.hasCriticalVulns !== undefined) {
      initialFilters.hasCriticalVulns = savedState.filters.hasCriticalVulns;
    }

    if (urlParams.has('has_high_vulns')) {
      initialFilters.hasHighVulns = urlParams.get('has_high_vulns') === 'true';
    } else if (savedState?.filters?.hasHighVulns !== undefined) {
      initialFilters.hasHighVulns = savedState.filters.hasHighVulns;
    }

    if (urlParams.has('has_medium_vulns')) {
      initialFilters.hasMediumVulns = urlParams.get('has_medium_vulns') === 'true';
    } else if (savedState?.filters?.hasMediumVulns !== undefined) {
      initialFilters.hasMediumVulns = savedState.filters.hasMediumVulns;
    }

    if (urlParams.has('has_low_vulns')) {
      initialFilters.hasLowVulns = urlParams.get('has_low_vulns') === 'true';
    } else if (savedState?.filters?.hasLowVulns !== undefined) {
      initialFilters.hasLowVulns = savedState.filters.hasLowVulns;
    }

    if (urlParams.has('has_exploit_available')) {
      initialFilters.hasExploitAvailable = urlParams.get('has_exploit_available') === 'true';
    } else if (savedState?.filters?.hasExploitAvailable !== undefined) {
      initialFilters.hasExploitAvailable = savedState.filters.hasExploitAvailable;
    }

    if (urlParams.has('has_test_execution')) {
      initialFilters.hasTestExecution = urlParams.get('has_test_execution') === 'true';
    } else if (savedState?.filters?.hasTestExecution !== undefined) {
      initialFilters.hasTestExecution = savedState.filters.hasTestExecution;
    }

    if (urlParams.has('has_web_interface')) {
      initialFilters.hasWebInterface = urlParams.get('has_web_interface') === 'true';
    } else if (savedState?.filters?.hasWebInterface !== undefined) {
      initialFilters.hasWebInterface = savedState.filters.hasWebInterface;
    }
    if (urlParams.has('tech')) {
      const techList = (urlParams.get('tech') || '')
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
      if (techList.length) initialFilters.tech = techList;
    } else if (savedState?.filters?.tech?.length) {
      initialFilters.tech = savedState.filters.tech;
    }

    // v4.51.0 — followFilter + onlyWithNotes fold into initialFilters
    // directly.  Saved-view shape kept legacy-compatible: pre-v4.51.0
    // saved blobs stored these as top-level keys ({followFilter,
    // onlyWithNotes}) alongside `filters`; we honour those AND any
    // copies that already live inside the new combined `filters`
    // (newer writes).
    const followParam = urlParams.get('follow_status') ?? urlParams.get('follow');
    const legacyFollow = savedState?.followFilter;
    if (followParam && ['watching', 'in_review', 'reviewed', 'none'].includes(followParam)) {
      initialFilters.followFilter = followParam as 'none' | FollowStatus;
    } else if (legacyFollow && legacyFollow !== 'all') {
      initialFilters.followFilter = legacyFollow as 'none' | FollowStatus;
    }

    const notesParam = urlParams.get('with_notes_only') ?? urlParams.get('with_notes');
    if (notesParam === 'true') {
      initialFilters.onlyWithNotes = true;
    } else if (notesParam === 'false') {
      delete initialFilters.onlyWithNotes;
    } else if (savedState?.onlyWithNotes === true && !initialFilters.onlyWithNotes) {
      initialFilters.onlyWithNotes = true;
    }

    // v5.0.x — assignment + sort are written to the URL by the write-sync
    // effect, so they must round-trip on restore too (a copied link should
    // reopen with the same assignee filter and sort, not the defaults).
    if (urlParams.get('assigned_to') === 'me') {
      initialFilters.assignedToMe = true;
    } else if (savedState?.filters?.assignedToMe) {
      initialFilters.assignedToMe = true;
    }

    const sortByParam = urlParams.get('sort_by');
    if (sortByParam) {
      // Reverse of the API-key map in buildHostQueryContext; direction is
      // encoded in the HostSortOption itself, so sort_by alone is enough.
      const reverseSort: Record<string, HostSortOption> = {
        critical_vulns: 'critical_desc',
        exploitable_vulns: 'exploitable_desc',
        open_ports: 'open_ports_desc',
        note_count: 'notes_desc',
        discovery_count: 'discoveries_desc',
        ip_address: 'ip_asc',
        hostname: 'hostname_asc',
      };
      const restored = reverseSort[sortByParam];
      if (restored) setSortBy(restored);
    }

    // Re-show the "project default applied" banner after a refresh: the restored
    // filters ARE the default, so set skipActiveClearRef so the filters-change
    // effect doesn't clear it on this (non-user) restore.
    try {
      const restoredDefault = sessionStorage.getItem(projectScopedKey('projectDefaultName'));
      if (restoredDefault && Object.keys(initialFilters).length > 0) {
        skipActiveClearRef.current = true;
        setAppliedProjectDefault(restoredDefault);
      }
    } catch {
      /* ignore */
    }

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
        toast.info('Cleared the project default view.', { autoHideMs: 2000 });
      } else {
        await promoteProjectDefaultView(view.id);
        setSavedViews((prev) => prev.map((v) => ({ ...v, is_project_default: v.id === view.id })));
        toast.success(`"${view.name}" is now the project default.`, { autoHideMs: 2500 });
      }
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update the project default.'));
    }
  };

  // Auto-apply the project default on a bare /hosts visit (no URL/saved-session
  // filter, not dismissed this session). One-shot per mount.
  const defaultCheckedRef = useRef(false);
  useEffect(() => {
    if (!isInitialized || defaultCheckedRef.current) return;
    defaultCheckedRef.current = true;
    // Only when the user has no filter context of their own.
    if (Object.keys(filters).length > 0) return;
    let dismissed = false;
    try {
      dismissed = sessionStorage.getItem(projectScopedKey('projectDefaultDismissed')) === '1';
    } catch { /* ignore */ }
    if (dismissed) return;
    getProjectDefaultView()
      .then((view) => {
        if (view && view.filter_json) {
          applyViewFilters(view, { quiet: true });
          setProjectDefaultBanner(view.name);
        }
      })
      .catch(() => { /* non-fatal */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isInitialized]);

  const dismissProjectDefault = () => {
    setProjectDefaultBanner(null);
    clearAllFilters();
    try {
      sessionStorage.setItem(projectScopedKey('projectDefaultDismissed'), '1');
    } catch { /* ignore */ }
  };

  useEffect(() => {
    if (skipActiveClearRef.current) {
      skipActiveClearRef.current = false;
      return;
    }
    setActiveViewId(null);
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
      sp.set(key, String(value));
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
      sp.set(key, String(value));
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

  // A lossy conversion the user has been warned about and may still apply.
  const [pendingConversion, setPendingConversion] = useState<DslConversion | null>(null);

  const applyConversion = useCallback((conversion: DslConversion) => {
    setFilters((previous) => {
      const updated = { ...previous } as HostFilterOptions;
      conversion.consumedKeys.forEach((key) => { delete (updated as any)[key]; });
      updated.query = conversion.dsl;
      return updated;
    });
    setPage(0);
    toast.info('Converted filters into a query', { autoHideMs: 2000 });
  }, [setFilters, setPage, toast]);

  // One-way panel → DSL: serialize the representable panel filters into the
  // query string and clear exactly the keys that were moved (id-based
  // tag/label selections and out-of-scope / first-seen stay in the panel).
  // When the conversion would silently change results (≥2 port dimensions
  // combined — the panel matches them on one port row, the DSL can't), we
  // confirm first instead of producing a wrong query.
  const handleConvertFiltersToQuery = useCallback(() => {
    const conversion = dslFromFilters(filters);
    if (!conversion.dsl) {
      toast.info('No filters to convert into a query', { autoHideMs: 2000 });
      return;
    }
    if (conversion.lossy) {
      setPendingConversion(conversion);
      return;
    }
    applyConversion(conversion);
  }, [filters, applyConversion, toast]);

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

  // Row click opens the side-sheet instead of navigating away from the
  // list — operators keep their place in the filtered set while
  // drilling into a host.  The full standalone page at /hosts/:id stays
  // reachable via the "Open standalone" link inside the sheet, or by
  // bookmark / deep link.
  const [inspectedHostId, setInspectedHostId] = useState<number | null>(null);

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
  const hasInspectorPrev = inspectedAbsoluteIndex > 0;
  const hasInspectorNext =
    inspectedAbsoluteIndex >= 0 && inspectedAbsoluteIndex < totalHosts - 1;
  // What to open once the next page loads. A ref (not state) so turning the
  // page doesn't add a render and the post-load effect reads the latest intent.
  const pendingInspectorEdgeRef = useRef<null | 'first' | 'last' | 'first-unreviewed'>(null);
  // "Unreviewed" = nobody has started it: skip both Reviewed AND In Review
  // (a teammate is already on the in-review ones). Untouched / legacy-watching
  // hosts are the queue targets.
  const hostNeedsReview = (h: Host) => {
    const status = h.follow?.status ?? 'none';
    return status !== 'reviewed' && status !== 'in_review';
  };

  const stepInspector = (delta: 1 | -1) => {
    if (inspectedIndex < 0) return;
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
  const stepToNextUnreviewed = () => {
    if (inspectedIndex < 0) return;
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
  const activeFilterChips = useMemo(() => {
    const chips: Array<{ key: string; label: string; onDelete?: () => void }> = [];
    const titleCase = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);

    // The DSL query narrows results like any other filter, so it must be part
    // of the active-filter model — otherwise a query-only view is described as
    // the full inventory, its zero-result state shows the onboarding prompt,
    // and Save view stays disabled (every consumer keys on activeFilterChips).
    if (filters.query?.trim())
      chips.push({
        key: 'query',
        label: `Query: ${filters.query.trim()}`,
        onDelete: () => clearFilterKey('query'),
      });
    if (filters.search)
      chips.push({
        key: 'search',
        label: `Search: ${filters.search}`,
        onDelete: () => clearFilterKey('search'),
      });
    if (filters.state)
      chips.push({
        key: 'state',
        label: `State: ${titleCase(filters.state)}`,
        onDelete: () => clearFilterKey('state'),
      });
    filters.ports?.forEach((portValue) => {
      chips.push({
        key: `port-${portValue}`,
        label: `Port: ${portValue}`,
        onDelete: () => removeListFilterValue('ports', portValue),
      });
    });
    filters.services?.forEach((service) => {
      chips.push({
        key: `service-${service}`,
        label: `Service: ${service}`,
        onDelete: () => removeListFilterValue('services', service),
      });
    });
    filters.portStates?.forEach((state) => {
      chips.push({
        key: `port-state-${state}`,
        label: `Port state: ${titleCase(state)}`,
        onDelete: () => removeListFilterValue('portStates', state),
      });
    });
    if (filters.hasOpenPorts !== undefined)
      chips.push({
        key: 'hasOpenPorts',
        label: filters.hasOpenPorts ? 'Has open ports' : 'No open ports',
        onDelete: () => clearFilterKey('hasOpenPorts'),
      });
    if (filters.osFilter)
      chips.push({
        key: 'osFilter',
        label: `OS: ${filters.osFilter}`,
        onDelete: () => clearFilterKey('osFilter'),
      });
    if (filters.hasWebInterface !== undefined)
      chips.push({
        key: 'hasWebInterface',
        label: filters.hasWebInterface ? 'Has web interface' : 'No web interface',
        onDelete: () => clearFilterKey('hasWebInterface'),
      });
    filters.tech?.forEach((techValue) => {
      chips.push({
        key: `tech-${techValue}`,
        label: `Tech: ${techValue}`,
        onDelete: () => removeListFilterValue('tech', techValue),
      });
    });
    filters.subnets?.forEach((subnet) => {
      chips.push({
        key: `subnet-${subnet}`,
        label: `Subnet: ${subnet}`,
        onDelete: () => removeListFilterValue('subnets', subnet),
      });
    });
    filters.scanIds?.forEach((scanId) => {
      const display = scanLookup.get(scanId)?.label || `Scan #${scanId}`;
      chips.push({
        key: `scan-${scanId}`,
        label: `Scan: ${display}`,
        onDelete: () => removeListFilterValue('scanIds', scanId),
      });
    });
    // v2.86.0 — tag + subnet-label chips.  Look up the display name
    // from filterData so the chip reads "Tag: prod" instead of "Tag:
    // 5" (which is what the URL/filter state actually holds).  Empty
    // filterData (loading) falls back to the raw id — the chip stays
    // useful, just terse.
    filters.tags?.forEach((tagId) => {
      const name = filterData?.tags?.find((t) => String(t.id) === tagId)?.name;
      chips.push({
        key: `tag-${tagId}`,
        label: `Tag: ${name ?? tagId}`,
        onDelete: () => removeListFilterValue('tags', tagId),
      });
    });
    filters.subnetLabels?.forEach((labelId) => {
      const name = filterData?.subnet_labels?.find((l) => String(l.id) === labelId)?.name;
      chips.push({
        key: `subnet-label-${labelId}`,
        label: `Subnet label: ${name ?? labelId}`,
        onDelete: () => removeListFilterValue('subnetLabels', labelId),
      });
    });
    filters.sites?.forEach((site) => {
      chips.push({
        key: `site-${site}`,
        label: `Site: ${site}`,
        onDelete: () => removeListFilterValue('sites', site),
      });
    });
    if (filters.firstSeenInSelectedScans)
      chips.push({
        key: 'firstSeenInSelectedScans',
        label: 'First discovered in selected scans',
        onDelete: () => clearFilterKey('firstSeenInSelectedScans'),
      });
    if (filters.hasCriticalVulns)
      chips.push({
        key: 'hasCriticalVulns',
        label: 'Critical vulnerabilities',
        onDelete: () => clearFilterKey('hasCriticalVulns'),
      });
    if (filters.hasHighVulns)
      chips.push({
        key: 'hasHighVulns',
        label: 'High vulnerabilities',
        onDelete: () => clearFilterKey('hasHighVulns'),
      });
    if (filters.hasMediumVulns)
      chips.push({
        key: 'hasMediumVulns',
        label: 'Medium vulnerabilities',
        onDelete: () => clearFilterKey('hasMediumVulns'),
      });
    if (filters.hasLowVulns)
      chips.push({
        key: 'hasLowVulns',
        label: 'Low vulnerabilities',
        onDelete: () => clearFilterKey('hasLowVulns'),
      });
    if (filters.hasExploitAvailable)
      chips.push({
        key: 'hasExploitAvailable',
        label: 'Has PoC / exploit available',
        onDelete: () => clearFilterKey('hasExploitAvailable'),
      });
    if (filters.hasTestExecution)
      chips.push({
        key: 'hasTestExecution',
        label: 'Has been tested',
        onDelete: () => clearFilterKey('hasTestExecution'),
      });
    if (filters.outOfScopeOnly)
      chips.push({
        key: 'outOfScopeOnly',
        label: 'Out-of-scope hosts',
        onDelete: () => clearFilterKey('outOfScopeOnly'),
      });
    if (followFilter !== 'all') {
      const followLabel =
        followFilter === 'none'
          ? 'Not reviewed'
          : FOLLOW_STATUS_OPTIONS.find((option) => option.value === followFilter)?.label ??
            titleCase(followFilter);
      chips.push({
        key: 'followFilter',
        label: `Review: ${followLabel}`,
        onDelete: () => setFollowFilter('all'),
      });
    }
    if (onlyWithNotes)
      chips.push({
        key: 'onlyWithNotes',
        label: 'With notes only',
        onDelete: () => setOnlyWithNotes(false),
      });
    if (filters.assignedToMe)
      chips.push({
        key: 'assignedToMe',
        label: 'Assigned to me',
        onDelete: () => clearFilterKey('assignedToMe'),
      });
    return chips;
  }, [filters, clearFilterKey, removeListFilterValue, scanLookup, filterData]);

  useEffect(() => {
    setExpanded((previous) => {
      // ExpandedState is `true | Record<string, boolean>`.  `true` means
      // every row is expanded — we never set that here, so we only need
      // to prune the per-row map down to ids still present in hosts.
      if (previous === true || typeof previous !== 'object') return previous;
      const next: Record<string, boolean> = {};
      Object.entries(previous).forEach(([key, value]) => {
        if (value && hosts.some((host) => host.id.toString() === key)) {
          next[key] = true;
        }
      });
      return next;
    });
  }, [hosts]);

  useEffect(() => {
    const maxPage = Math.max(Math.ceil(totalHosts / rowsPerPage) - 1, 0);
    if (page > maxPage) setPage(maxPage);
  }, [page, rowsPerPage, totalHosts]);

  const visibleReviewStats = useMemo(() => {
    const viewed = hosts.filter((host) => Boolean(host.follow?.last_viewed_at)).length;
    const followed = hosts.filter((host) => Boolean(host.follow?.status)).length;
    return {
      viewed,
      pending: Math.max(hosts.length - viewed, 0),
      followed,
    };
  }, [hosts]);

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
    expanded,
    onExpandedChange: setExpanded,
    getRowCanExpand: () => true,
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

  const renderFollowChip = (label: string, value: 'all' | 'none' | FollowStatus, badgeClass?: string) => {
    const active = followFilter === value;
    return (
      <button
        key={value}
        type="button"
        onClick={() => {
          setFollowFilter(value);
          setPage(0);
        }}
        aria-pressed={active}
        className={cn(
          'inline-flex items-center gap-xxs rounded-chip border px-sm py-px text-caption font-medium transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
          active
            ? badgeClass
              ? cn(badgeClass, 'ring-1 ring-inset ring-foreground/30')
              : 'border-transparent bg-primary text-primary-foreground ring-1 ring-inset ring-primary-foreground/30'
            : 'border-border bg-card text-foreground hover:bg-accent',
        )}
      >
        {active && <Check className="size-3" aria-hidden />}
        {label}
      </button>
    );
  };

  return (
    <div className="space-y-md">
      {/* Page header */}
      <div className="flex flex-col gap-md lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-page-title">Discovered Hosts</h1>
          <div className="mt-xxs flex items-center gap-xs text-metadata text-muted-foreground">
            <Computer className="size-4" aria-hidden />
            <span>
              <strong className="text-foreground">{totalHosts}</strong>{' '}
              host{totalHosts === 1 ? '' : 's'} — dense inventory view for triage, filtering, and drill-down.
            </span>
          </div>
        </div>
        <div className="flex flex-col gap-xs sm:flex-row sm:items-center">
          <Button
            onClick={() => setToolReadyDialogOpen(true)}
            disabled={loading || totalHosts === 0 || showingStaleResults}
          >
            <Code className="size-4" aria-hidden />
            Tool Ready Output
          </Button>
          <Button
            variant="outline"
            onClick={() => setReportsDialogOpen(true)}
            disabled={loading || totalHosts === 0 || showingStaleResults}
          >
            <Download className="size-4" aria-hidden />
            Export Report
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
            Showing only hosts that are not mapped to any configured scope.
          </AlertDescription>
        </Alert>
      )}

      <HostCommandBar
        value={filters.query ?? ''}
        onChange={setQuery}
        onPin={handlePinQuery}
        onCopyLink={handleCopyLink}
        valueSuggestions={queryValueSuggestions}
      />

      <div className="flex flex-wrap items-center gap-xs">
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={advancedOpen}
          onClick={() => setAdvancedOpen((open) => !open)}
        >
          <SlidersHorizontal className="size-4" aria-hidden />
          Advanced filters
          {advancedOpen ? <ChevronUp className="size-4" aria-hidden /> : <ChevronDown className="size-4" aria-hidden />}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Convert the structured filters into a query"
          onClick={handleConvertFiltersToQuery}
        >
          <Wand2 className="size-4" aria-hidden />
          Convert filters → query
        </Button>
      </div>

      {advancedOpen && (
        <HostFilters
          filters={filters}
          onFiltersChange={handleFiltersChange}
          availableData={filterData}
          optionsLoading={filterDataLoading}
          notesToggleVisible
        />
      )}

      <Card className="sticky z-10 mb-md" style={stickyBelowChrome}>
        <CardContent className="space-y-sm pt-md">
          <div className="flex flex-col gap-sm lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0 space-y-xxs">
              <div className="flex items-center gap-sm">
                <span
                  className={cn(
                    'flex size-9 shrink-0 items-center justify-center rounded-control border transition-colors',
                    activeFilterChips.length > 0
                      ? 'border-primary/30 bg-primary/10 text-primary'
                      : 'border-border bg-muted text-muted-foreground',
                  )}
                  aria-hidden
                >
                  <Crosshair className="size-4" />
                </span>
                <p className="text-body">
                  {/* key remounts the number on change so the fade/zoom replays
                      — transform + opacity only, so it never shifts layout. */}
                  <span
                    key={totalHosts}
                    className={cn(
                      'inline-block text-section-title font-bold tabular-nums animate-in fade-in zoom-in-95 duration-300',
                      activeFilterChips.length > 0 ? 'text-primary' : 'text-foreground',
                    )}
                  >
                    {totalHosts.toLocaleString()}
                  </span>{' '}
                  <span className="text-muted-foreground">
                    host{totalHosts === 1 ? '' : 's'}{' '}
                    {activeFilterChips.length > 0 ? 'match the current filters' : 'in inventory'}
                  </span>
                </p>
              </div>
              <p className="text-metadata text-muted-foreground">
                Page {Math.min(page + 1, Math.max(Math.ceil(totalHosts / rowsPerPage), 1))} of{' '}
                {Math.max(Math.ceil(totalHosts / rowsPerPage), 1)}
              </p>
              <div className="flex flex-wrap gap-xs">
                <Badge variant={visibleReviewStats.viewed > 0 ? 'success' : 'outline'}>
                  <Eye className="size-3" aria-hidden />
                  {visibleReviewStats.viewed} viewed
                </Badge>
                <Badge
                  variant="outline"
                  className={
                    visibleReviewStats.pending > 0
                      ? 'border-warning/50 text-warning'
                      : undefined
                  }
                >
                  {visibleReviewStats.pending} pending review
                </Badge>
                <Badge
                  variant="outline"
                  className={
                    visibleReviewStats.followed > 0 ? 'border-info/50 text-info' : undefined
                  }
                >
                  <Bookmark className="size-3" aria-hidden />
                  {visibleReviewStats.followed} followed
                </Badge>
              </div>
            </div>
            <div className="flex flex-wrap items-end gap-sm">
              {/* v4.26.0 — "Only hosts with notes" relocated into
                  HostFilters' boolean panel where it sits with its
                  peers.  Sort stays here as a table control, not a
                  filter. */}
              <div className="flex flex-col gap-xxs">
                <Label htmlFor="hosts-sort">Sort by</Label>
                <Select
                  value={sortBy}
                  onValueChange={(value) => {
                    setSortBy(value as HostSortOption);
                    setPage(0);
                  }}
                >
                  <SelectTrigger id="hosts-sort" className="w-[14rem]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="critical_desc">Critical vulnerabilities</SelectItem>
                    <SelectItem value="exploitable_desc">Exploitable first</SelectItem>
                    <SelectItem value="open_ports_desc">Open ports</SelectItem>
                    <SelectItem value="discoveries_desc">Most discoveries</SelectItem>
                    <SelectItem value="notes_desc">Most notes</SelectItem>
                    <SelectItem value="ip_asc">IP address</SelectItem>
                    <SelectItem value="hostname_asc">Hostname</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          {/* v4.51.0 — Quick views chip row removed from the sticky
              bar; the canonical preset surface lives in the
              HostFilters card now.  See HOST_FILTER_PRESETS in
              HostFilters.tsx. */}

          {/* Review status */}
          <div
            className="flex flex-wrap items-center gap-xs"
            role="group"
            aria-label="Review status filter"
          >
            <span className="text-caption text-muted-foreground">Review status:</span>
            {renderFollowChip('All', 'all')}
            {/* "Not reviewed" = nobody on the team has this host In Review or
                Reviewed (team-shared, follow:none). */}
            {renderFollowChip('Not reviewed', 'none')}
            {FOLLOW_STATUS_OPTIONS.map((option) =>
              renderFollowChip(option.label, option.value, option.badgeClass),
            )}
          </div>

          {/* Project default applied — dismissible, never a trap */}
          {appliedProjectDefault && (
            <div className="flex flex-wrap items-center gap-xs rounded-control border border-primary/40 bg-accent/40 px-sm py-xxs text-caption">
              <Star className="size-3.5 shrink-0 fill-current text-warning" aria-hidden />
              <span className="text-foreground">
                Project default filter applied: <strong>{appliedProjectDefault}</strong>
              </span>
              <Button variant="ghost" size="sm" className="h-6" onClick={dismissProjectDefault}>
                Clear
              </Button>
            </div>
          )}

          {/* Saved views */}
          {(savedViews.length > 0 || activeFilterChips.length > 0 || savedViewsError) && (
            <div className="flex flex-wrap items-center gap-xs">
              <span className="text-caption text-muted-foreground">Saved views:</span>
              {savedViewsError && savedViews.length === 0 && (
                <p className="text-caption text-muted-foreground">
                  Couldn't load saved views — refresh to retry.
                </p>
              )}
              {!savedViewsError && savedViews.length === 0 && (
                <span className="text-caption text-muted-foreground">
                  none yet — apply some filters and click <strong>Save view</strong>
                </span>
              )}
              {savedViews.map((view) => {
                const isActive = activeViewId === view.id;
                return (
                  <span
                    key={view.id}
                    className={cn(
                      'inline-flex max-w-[14rem] items-center gap-xxs overflow-hidden whitespace-nowrap rounded-chip border px-sm py-px text-caption font-medium',
                      isActive
                        ? 'border-transparent bg-primary text-primary-foreground'
                        : 'border-border bg-card text-foreground',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => handleApplyView(view)}
                      aria-pressed={isActive}
                      className="truncate focus:outline-none focus:underline"
                    >
                      {view.name}
                    </button>
                    {view.is_project_default && (
                      <Star
                        className={cn('size-3 shrink-0 fill-current', isActive ? '' : 'text-warning')}
                        aria-label="Project default"
                      />
                    )}
                    {canSetProjectDefault && (
                      <button
                        type="button"
                        onClick={() => handleToggleProjectDefault(view)}
                        aria-label={view.is_project_default ? `Clear project default` : `Set "${view.name}" as project default`}
                        title={view.is_project_default ? 'Clear project default' : 'Set as project default'}
                        className={cn(
                          'inline-flex size-6 shrink-0 items-center justify-center rounded-sm',
                          isActive ? 'hover:bg-primary-foreground/20' : 'hover:bg-accent',
                        )}
                      >
                        <Star className={cn('size-3', view.is_project_default && 'fill-current')} aria-hidden />
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleDeleteView(view)}
                      aria-label={`Delete saved view ${view.name}`}
                      className={cn(
                        'inline-flex size-6 shrink-0 items-center justify-center rounded-sm',
                        isActive
                          ? 'hover:bg-primary-foreground/20'
                          : 'hover:bg-accent',
                      )}
                    >
                      <X className="size-3" aria-hidden />
                    </button>
                  </span>
                );
              })}
              <Button
                size="sm"
                variant="outline"
                disabled={activeFilterChips.length === 0}
                onClick={() => {
                  setSaveViewName('');
                  setSaveViewDialogOpen(true);
                }}
              >
                Save view
              </Button>
            </div>
          )}

          {activeFilterChips.length > 0 && (
            <div className="flex flex-wrap items-center gap-xs">
              {activeFilterChips.map((chip) => (
                <span
                  key={chip.key}
                  className="inline-flex max-w-full items-center gap-xxs rounded-chip border border-border bg-card px-sm py-px text-caption font-medium"
                >
                  <span className="truncate">{chip.label}</span>
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
              <Button variant="ghost" size="sm" onClick={clearAllFilters}>
                Clear all
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

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
          <p className="text-metadata text-muted-foreground">
            {activeFilterChips.length} filter{activeFilterChips.length === 1 ? '' : 's'} active — adjust
            them above or clear all to see every host.
          </p>
          <Button onClick={clearAllFilters}>Clear filters</Button>
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
          {selectedIds.length > 0 && !showingStaleResults && (
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
          )}
          {/* Host table — sole renderer (desktop-only product; horizontal
              scroll handles narrow widths, no separate mobile card view).
              Dimmed (not interaction-blocked) while stale: drill-down into a
              single host is harmless + refetches, only bulk/export are paused. */}
          <div
            className={cn(showingStaleResults && 'opacity-60 transition-opacity')}
            aria-busy={showingStaleResults || undefined}
          >
            <DataTableShell<Host>
              table={table}
              onRowClick={(host) => openInspector(host.id)}
              renderSubRow={(row: Row<Host>) => (
                <HostExpandedRow
                  host={row.original}
                  vulnError={vulnError}
                  onOpenScan={(scanId) => navigate(`/scans/${scanId}`)}
                />
              )}
              // v4.45.0 — left-border accent on rows that have had at
              // least one agentic test executed against them
              // (test_execution_count > 0). Hover surfaces the count
              // as a native title tooltip.  The keyboard cursor row (1c)
              // also gets a highlight + a `host-cursor-row` marker class
              // the scroll-into-view effect keys off.
              getRowClassName={(row) =>
                cn(
                  (row.original.test_execution_count ?? 0) > 0 && 'border-l-4 border-l-info',
                  row.index === cursorIndex &&
                    'host-cursor-row bg-accent ring-1 ring-inset ring-ring',
                ) || undefined
              }
              getRowTitle={(row) => {
                const n = row.original.test_execution_count ?? 0;
                return n > 0
                  ? `Tested · ${n} agentic test result${n === 1 ? '' : 's'} recorded`
                  : undefined;
              }}
              tableClassName="table-fixed"
            />
          </div>

          <Card>
            <CardContent className="py-xs">
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
            </CardContent>
          </Card>
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
          if (!open) setInspectedHostId(null);
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
                    onClick={() => {
                      const id = inspectedHostId;
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
                onQueryHosts={(q) => {
                  // Close the sheet so the freshly-filtered list is visible,
                  // then apply the vuln query through the normal URL-restore path.
                  setInspectedHostId(null);
                  navigate(buildHostsUrl({ q }));
                }}
              />
            )}
          </SideSheetBody>
        </SideSheetContent>
      </SideSheet>

      {/* Guard: converting combined port/service/state filters to a query
          changes their meaning (panel matches one port row; the query matches
          each clause independently).  Confirm before producing a wrong query. */}
      <ConfirmDialog
        open={pendingConversion !== null}
        onOpenChange={(open) => { if (!open) setPendingConversion(null); }}
        title="This conversion changes what you'll match"
        description={
          'Your panel combines port, service, or port-state filters, which match the SAME port '
          + '(e.g. "HTTP on port 22"). The query language matches each independently, so the '
          + 'converted query can return different hosts (e.g. "port 22 anywhere AND HTTP anywhere"). '
          + 'Convert anyway?'
        }
        confirmLabel="Convert anyway"
        confirmVariant="default"
        cancelLabel="Keep the filters"
        onConfirm={() => {
          if (pendingConversion) applyConversion(pendingConversion);
          setPendingConversion(null);
        }}
      />
      {confirmEl}
    </div>
  );
}
