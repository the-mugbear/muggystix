import React, { useEffect, useMemo, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronUp,
  Computer as ComputerIcon,
  Eye,
  FileText,
  Filter as FilterIcon,
  Globe,
  Network as NetworkIcon,
  ShieldAlert,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import type { FollowStatus, HostFilterData } from '../services/api';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent } from './ui/card';
import { Checkbox } from './ui/checkbox';
import {
  Combobox,
  type ComboboxOption,
} from './ui/combobox';
import { Input } from './ui/input';
import { Label } from './ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import { Switch } from './ui/switch';
import { cn } from '../utils/cn';

export interface HostFilterOptions {
  search?: string;
  state?: string;
  ports?: string[];
  services?: string[];
  portStates?: string[];
  hasOpenPorts?: boolean;
  osFilter?: string;
  subnets?: string[];
  hasCriticalVulns?: boolean;
  hasHighVulns?: boolean;
  hasExploitAvailable?: boolean;
  hasTestExecution?: boolean;
  minRiskScore?: number;
  outOfScopeOnly?: boolean;
  scanIds?: string[];
  firstSeenInSelectedScans?: boolean;
  // v2.12.1: web interface filters
  hasWebInterface?: boolean;
  tech?: string[];
  // v2.71.0: tag ids (string form) + "assigned to me" toggle.
  tags?: string[];
  // v2.86.0: subnet-label ids (string form) — host qualifies if it sits
  // in any subnet carrying any listed label.  Distinct from `tags`
  // because the vocabulary is separate; intersected with the tag group
  // (AND between groups, OR within each).
  subnetLabels?: string[];
  assignedToMe?: boolean;
  // v4.51.0 — followFilter + onlyWithNotes folded into HostFilterOptions
  // so the page state is a single object instead of three useStates.  The
  // sticky-bar "Follow status" chip group still writes here; absent
  // followFilter means "no follow-status filter" (the old 'all' sentinel),
  // absent onlyWithNotes means the toggle is off.  See Hosts.tsx for the
  // setter helpers that delete the keys when the user clears them so
  // `Object.keys(filters).length === 0` remains the canonical "nothing
  // filtered" check.
  followFilter?: 'none' | FollowStatus;
  onlyWithNotes?: boolean;
  // v5.0.0 — boolean query DSL string (the command bar's power input).
  // Lives in the filter blob so it round-trips through URL sync,
  // sessionStorage, and saved views with zero extra plumbing.  ANDs with
  // the structured panel filters server-side.
  query?: string;
}

export interface HostFiltersProps {
  filters: HostFilterOptions;
  onFiltersChange: (filters: HostFilterOptions) => void;
  // Shared shape from the API layer (HostFilterData).  Accepts null so the
  // Hosts page can pass its `HostFilterData | null` state directly while
  // facets are still loading.
  availableData?: HostFilterData | null;
  // True while the facet options are still being fetched (they load after the
  // host list). Lets the comboboxes say "Loading options…" instead of the
  // misleading "No X seen yet." for genuinely-empty data.
  optionsLoading?: boolean;
  // v4.51.0 — "Only hosts with notes" now lives inside `filters` (see
  // HostFilterOptions).  The `notesToggleVisible` prop lets the page
  // opt-in to rendering the toggle alongside the other booleans; absent
  // = don't show (preserves standalone-component back-compat).
  notesToggleVisible?: boolean;
}

// v4.51.0 — unified preset surface.  Pre-v4.51.0 there were TWO
// preset systems: this list (called "Quick presets", network-shape
// shortcuts, applied with MERGE semantics) and a separate "Quick
// views" chip strip on the Hosts page sticky bar (called Critical /
// Not Reviewed / etc., applied with REPLACE semantics).  The two
// disagreed on apply behaviour, lived in different surfaces, and
// each only covered half the catalogue.  Merged into a single list
// with one rule: applying a preset REPLACES the current filter
// state.  That matches "a preset is a canonical view, not an
// additive shortcut" — picking Critical means "I want the Critical
// view", not "add hasCriticalVulns to whatever I already had".
//
// Active-preset detection (the pressed-state on the matching button)
// is computed by deep-equals against `filters` — extra filters on
// top of a preset fail the match so the chip stays unlit.  This is
// the same exact-match rule the old activeQuickView used.
export const HOST_FILTER_PRESETS: Array<{
  id: string;
  name: string;
  Icon: typeof NetworkIcon;
  description: string;
  filters: HostFilterOptions;
}> = [
  // Workflow shortcuts (the old Quick views) come first because
  // these are the most common entry points operators reach for.
  {
    id: 'not_reviewed',
    name: 'Not Reviewed',
    Icon: Eye,
    description: 'Hosts you have not yet reviewed',
    filters: { followFilter: 'none' },
  },
  {
    id: 'critical',
    name: 'Critical',
    Icon: ShieldAlert,
    description: 'Hosts with critical vulnerabilities',
    filters: { hasCriticalVulns: true },
  },
  {
    id: 'high_value',
    name: 'High Value',
    Icon: ShieldCheck,
    description: 'Hosts with high-severity findings and open ports',
    filters: { hasHighVulns: true, hasOpenPorts: true },
  },
  {
    id: 'needs_review',
    name: 'Watching',
    Icon: Eye,
    description: 'Hosts on your watch list',
    filters: { followFilter: 'watching' },
  },
  {
    id: 'out_of_scope',
    name: 'Out of Scope',
    Icon: Globe,
    description: 'Hosts not mapped to any configured scope',
    filters: { outOfScopeOnly: true },
  },
  {
    id: 'with_notes',
    name: 'With Notes',
    Icon: FileText,
    description: 'Hosts that carry at least one note',
    filters: { onlyWithNotes: true },
  },
  // Network-shape shortcuts (the old Quick presets).
  {
    id: 'web_hosts',
    name: 'Web Hosts',
    Icon: Globe,
    description: 'Hosts with web services (HTTP/HTTPS)',
    filters: { services: ['http', 'https'], portStates: ['open'] },
  },
  {
    id: 'ssh',
    name: 'SSH Servers',
    Icon: Shield,
    description: 'Hosts with SSH access',
    filters: { ports: ['22'], portStates: ['open'] },
  },
  {
    id: 'database',
    name: 'Database Servers',
    Icon: ComputerIcon,
    description: 'Common database ports (3306/5432/1433/27017)',
    filters: { ports: ['3306', '5432', '1433', '27017'], portStates: ['open'] },
  },
  {
    id: 'windows',
    name: 'Windows Hosts',
    Icon: ComputerIcon,
    description: 'Windows-specific services (135/139/445)',
    filters: { ports: ['135', '139', '445'], portStates: ['open'] },
  },
  {
    id: 'legacy',
    name: 'Legacy Protocols',
    Icon: ShieldAlert,
    description: 'Legacy/insecure protocols (21/23/53/69/135/139)',
    filters: { ports: ['21', '23', '53', '69', '135', '139'], portStates: ['open'] },
  },
];

// Treat a key set to ``undefined`` as absent — callers sometimes
// set keys to undefined to "clear" instead of deleting outright.
const definedFilterKeys = (filters: HostFilterOptions): string[] =>
  Object.keys(filters).filter((k) => (filters as Record<string, unknown>)[k] !== undefined);

// Exact-match: the live `filters` deep-equals the preset's canonical
// state.  Arrays compared as multisets so order doesn't matter; extra
// keys on `filters` fail the match.  Same rule the old
// activeQuickView used — a preset chip lights up only when the
// preset's view is exactly what's active.
const matchesPreset = (preset: HostFilterOptions, current: HostFilterOptions): boolean => {
  const pKeys = definedFilterKeys(preset).sort();
  const cKeys = definedFilterKeys(current).sort();
  if (pKeys.length !== cKeys.length) return false;
  if (pKeys.some((k, i) => k !== cKeys[i])) return false;
  for (const k of pKeys) {
    const pv = (preset as Record<string, unknown>)[k];
    const cv = (current as Record<string, unknown>)[k];
    if (Array.isArray(pv)) {
      if (!Array.isArray(cv) || pv.length !== cv.length) return false;
      const sp = [...pv].sort();
      const sc = [...cv].sort();
      if (sp.some((v, i) => v !== sc[i])) return false;
    } else if (pv !== cv) {
      return false;
    }
  }
  return true;
};

export const activeFilterPresetId = (filters: HostFilterOptions): string | null => {
  if (definedFilterKeys(filters).length === 0) return 'all';
  for (const preset of HOST_FILTER_PRESETS) {
    if (matchesPreset(preset.filters, filters)) return preset.id;
  }
  return null;
};

const PORT_STATE_OPTIONS: ComboboxOption[] = [
  { value: 'open', label: 'Open' },
  { value: 'closed', label: 'Closed' },
  { value: 'filtered', label: 'Filtered' },
];

const HOST_STATE_VALUES = [
  { value: 'all', label: 'All States' },
  { value: 'up', label: 'Up' },
  { value: 'down', label: 'Down' },
];

/**
 * Inline switch+label row used several times in the filter grid.
 */
const SwitchRow: React.FC<{
  id: string;
  label: string;
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  disabled?: boolean;
  description?: string;
}> = ({ id, label, checked, onCheckedChange, disabled, description }) => (
  <div className="flex items-start gap-sm">
    <Switch id={id} checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} />
    <div className="flex min-w-0 flex-col">
      <Label htmlFor={id} className={cn('text-metadata', disabled && 'opacity-60')}>
        {label}
      </Label>
      {description && <span className="text-caption text-muted-foreground">{description}</span>}
    </div>
  </div>
);

const HostFilters: React.FC<HostFiltersProps> = ({
  filters,
  onFiltersChange,
  availableData,
  optionsLoading,
  notesToggleVisible,
}) => {
  const notesToggleAvailable = notesToggleVisible === true;
  // While facets load, every combobox's option list is legitimately empty;
  // swap the "nothing here" message for a loading one so the two states aren't
  // confused on large inventories.
  const facetEmpty = (msg: string) => (optionsLoading ? 'Loading options…' : msg);
  const onlyWithNotes = filters.onlyWithNotes === true;
  const handleOnlyWithNotesChange = (next: boolean) => {
    const updated = { ...filters };
    if (next) {
      updated.onlyWithNotes = true;
    } else {
      delete updated.onlyWithNotes;
    }
    onFiltersChange(updated);
  };
  const activePresetId = useMemo(() => activeFilterPresetId(filters), [filters]);
  // v5.2.0 — the legacy "Search hosts" field was removed; bare-text search now
  // lives in the command bar (maps to filters.query). The `/` focus shortcut
  // moved there too. filters.search may still arrive from a saved view or an
  // older shared URL, so it stays in the active-filter model (chip + counts).

  // Advanced-filter collapse — persisted to localStorage so a user who
  // prefers the full form stays expanded between page visits.
  const [advancedOpen, setAdvancedOpen] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return window.localStorage.getItem('hosts.advancedFiltersOpen') === 'true';
  });
  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem('hosts.advancedFiltersOpen', advancedOpen ? 'true' : 'false');
    }
  }, [advancedOpen]);

  // Use a ref to always have the latest filters available to effects without
  // adding filters to the dependency array (which would defeat debouncing).
  const filtersRef = React.useRef(filters);
  filtersRef.current = filters;

  useEffect(() => {
    if ((filters.scanIds?.length ?? 0) === 0 && filters.firstSeenInSelectedScans) {
      const updated = { ...filtersRef.current };
      delete (updated as any).firstSeenInSelectedScans;
      onFiltersChange(updated);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.scanIds?.length, onFiltersChange]);

  const activeFiltersCount = useMemo(() => {
    return [
      !!filters.search,
      !!filters.state,
      (filters.ports?.length ?? 0) > 0,
      (filters.services?.length ?? 0) > 0,
      (filters.portStates?.length ?? 0) > 0,
      filters.hasOpenPorts !== undefined,
      !!filters.osFilter,
      filters.hasCriticalVulns !== undefined,
      filters.hasHighVulns !== undefined,
      filters.hasExploitAvailable !== undefined,
      filters.hasTestExecution !== undefined,
      filters.minRiskScore !== undefined,
      (filters.subnets?.length ?? 0) > 0,
      (filters.scanIds?.length ?? 0) > 0,
      filters.firstSeenInSelectedScans === true,
      filters.hasWebInterface !== undefined,
      (filters.tech?.length ?? 0) > 0,
      (filters.tags?.length ?? 0) > 0,
      (filters.subnetLabels?.length ?? 0) > 0,
      filters.assignedToMe === true,
      // v4.26.0 — previously omitted (count bug); the chip-row in
      // Hosts.tsx counts these, so the badge here was off by 1-2.
      filters.outOfScopeOnly === true,
      notesToggleAvailable && filters.onlyWithNotes === true,
      // v4.51.0 — followFilter folded into filters.  Counted only
      // when it actually selects a non-default value.
      filters.followFilter !== undefined,
    ].filter((active) => active).length;
  }, [filters, notesToggleAvailable]);

  // Count only the filters that live in the collapsed advanced section
  // so the badge next to "More filters" is accurate when collapsed.
  // v5.2.0 — OS / ports / services / subnets / tags were surfaced into the
  // always-visible grid, so they no longer count toward the advanced badge.
  const advancedFiltersActive = useMemo(() => {
    return [
      filters.minRiskScore !== undefined,
      (filters.portStates?.length ?? 0) > 0,
      filters.hasWebInterface !== undefined,
      (filters.tech?.length ?? 0) > 0,
      (filters.subnetLabels?.length ?? 0) > 0,
    ].filter((active) => active).length;
  }, [filters]);

  useEffect(() => {
    if (advancedFiltersActive > 0 && !advancedOpen) {
      setAdvancedOpen(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleFilterChange = <K extends keyof HostFilterOptions>(
    key: K,
    value: HostFilterOptions[K] | undefined,
  ) => {
    const next = { ...filters };
    if (value === undefined) {
      delete (next as any)[key];
    } else {
      (next as any)[key] = value;
    }
    onFiltersChange(next);
  };

  const handleClearFilters = () => {
    onFiltersChange({});
  };

  // v4.51.0 — REPLACE semantics.  A preset is a canonical view, not
  // an additive shortcut; clicking "Critical" means "show me the
  // Critical view", not "add hasCriticalVulns on top of whatever I
  // already had".  This is the rule the old Quick views used; the
  // old Quick presets used merge (`{ ...filters, ...preset.filters }`)
  // and that was the reported "Critical AND Not Reviewed feels
  // conflicting" symptom because the two preset systems disagreed.
  // If the user picks the already-active preset, clear it (toggle
  // behaviour matches the chip pressed-state).
  const applyPreset = (preset: (typeof HOST_FILTER_PRESETS)[number]) => {
    if (activePresetId === preset.id) {
      onFiltersChange({});
    } else {
      onFiltersChange({ ...preset.filters });
    }
  };

  // ---------------------------------------------------------------------
  // Combobox option lists — derived from availableData.
  // ---------------------------------------------------------------------

  const portOptions: ComboboxOption[] = useMemo(() => {
    if (!availableData?.common_ports) return [];
    const portMap = new Map<number, { service: string; count: number }>();
    availableData.common_ports.forEach((port) => {
      if (!portMap.has(port.port) || (portMap.get(port.port)!.count ?? 0) < port.count) {
        portMap.set(port.port, { service: port.service, count: port.count });
      }
    });
    return Array.from(portMap.entries())
      .map(([port, data]) => ({
        value: port.toString(),
        label: `${port} (${data.service})`,
        trailing: `${data.count}`,
        keywords: [data.service],
      }))
      .sort((a, b) => Number(b.trailing) - Number(a.trailing));
  }, [availableData?.common_ports]);

  const serviceOptions: ComboboxOption[] = useMemo(() => {
    return (
      availableData?.services?.map((service) => ({
        value: service.name,
        label: service.name,
        trailing: `${service.count}`,
      })) || []
    );
  }, [availableData?.services]);

  const osOptions: ComboboxOption[] = useMemo(() => {
    return (
      availableData?.operating_systems?.map((os) => ({
        value: os.name,
        label: os.name,
        trailing: `${os.count}`,
      })) || []
    );
  }, [availableData?.operating_systems]);

  const subnetOptions: ComboboxOption[] = useMemo(() => {
    return (
      availableData?.subnets?.map((subnet) => ({
        value: subnet.cidr,
        label: subnet.cidr,
        description: subnet.scope_name,
        trailing: `${subnet.host_count}`,
        keywords: [subnet.scope_name],
      })) || []
    );
  }, [availableData?.subnets]);

  const scanOptions: ComboboxOption[] = useMemo(() => {
    return (
      availableData?.scans?.map((scan) => ({
        value: scan.id.toString(),
        label: scan.filename || `Scan #${scan.id}`,
        description: scan.created_at
          ? `${scan.tool_name ?? 'unknown'} · ${new Date(scan.created_at).toLocaleDateString()}`
          : scan.tool_name ?? undefined,
        keywords: scan.tool_name ? [scan.tool_name] : undefined,
      })) || []
    );
  }, [availableData?.scans]);

  const techOptions: ComboboxOption[] = useMemo(() => {
    return (
      availableData?.technologies?.map((tech) => ({
        value: tech.name,
        label: tech.name,
        trailing: `${tech.host_count}`,
      })) || []
    );
  }, [availableData?.technologies]);

  const tagOptions: ComboboxOption[] = useMemo(() => {
    return (
      availableData?.tags?.map((tag) => ({
        value: String(tag.id),
        label: tag.name,
        trailing: `${tag.host_count}`,
      })) || []
    );
  }, [availableData?.tags]);

  // v2.86.0 — subnet-label combobox options.  Same trailing-count
  // convention as tagOptions; the backend already returns
  // host_count as COUNT DISTINCT host_id so the picker never
  // overcounts on overlapping subnets.
  const subnetLabelOptions: ComboboxOption[] = useMemo(() => {
    return (
      availableData?.subnet_labels?.map((lbl) => ({
        value: String(lbl.id),
        label: lbl.name,
        trailing: `${lbl.host_count}`,
      })) || []
    );
  }, [availableData?.subnet_labels]);

  const webInterfaceLabel = filters.hasWebInterface === true
    ? 'Has web interface'
    : filters.hasWebInterface === false
      ? 'No web interface'
      : 'Web interface: any';

  const minRiskScoreInvalid =
    filters.minRiskScore !== undefined &&
    (filters.minRiskScore < 0 || filters.minRiskScore > 100);

  return (
    <Card className="mb-md">
      <CardContent className="space-y-md pt-md">
        <div className="flex items-center justify-between gap-sm">
          <div className="flex items-center gap-xs">
            <FilterIcon className="size-4 text-muted-foreground" aria-hidden />
            <h2 className="text-subheading">Host Filters</h2>
            {activeFiltersCount > 0 && (
              <Badge variant="default" aria-label={`${activeFiltersCount} active filters`}>
                {activeFiltersCount}
              </Badge>
            )}
          </div>
          {activeFiltersCount > 0 && (
            <Button variant="ghost" size="sm" onClick={handleClearFilters}>
              <X className="size-3.5" aria-hidden />
              Clear all
            </Button>
          )}
        </div>

        {/* Quick presets (v4.51.0 — unified surface; replaces both the
            old Quick presets list here and the Quick views chip row
            that lived on the Hosts sticky bar) */}
        <div className="space-y-xxs">
          <div className="flex items-center gap-xs">
            <SlidersHorizontal className="size-4 text-muted-foreground" aria-hidden />
            <h3 className="text-metadata font-semibold uppercase tracking-wider text-muted-foreground">
              Quick presets
            </h3>
          </div>
          <p className="text-caption text-muted-foreground">
            Click a preset to apply that view. Picking one replaces any active filters; click the lit preset again to clear it.
          </p>
          <div className="flex flex-wrap gap-xs" role="group" aria-label="Filter presets">
            <button
              key="all"
              type="button"
              onClick={() => onFiltersChange({})}
              aria-pressed={activePresetId === 'all'}
              title="Clear all filters"
              className={cn(
                'inline-flex items-center gap-xxs rounded-control border px-sm py-xxs text-caption font-medium transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                activePresetId === 'all'
                  ? 'border-transparent bg-primary text-primary-foreground ring-1 ring-inset ring-primary-foreground/30'
                  : 'border-border bg-card text-foreground hover:bg-accent',
              )}
            >
              {activePresetId === 'all' && <Check className="size-3" aria-hidden />}
              All Hosts
            </button>
            {HOST_FILTER_PRESETS.map((preset) => {
              const active = activePresetId === preset.id;
              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => applyPreset(preset)}
                  aria-pressed={active}
                  title={preset.description}
                  className={cn(
                    'inline-flex items-center gap-xxs rounded-control border px-sm py-xxs text-caption font-medium transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                    active
                      ? 'border-transparent bg-primary text-primary-foreground ring-1 ring-inset ring-primary-foreground/30'
                      : 'border-border bg-card text-foreground hover:bg-accent',
                  )}
                >
                  {active ? (
                    <Check className="size-3" aria-hidden />
                  ) : (
                    <preset.Icon className="size-3.5" aria-hidden />
                  )}
                  {preset.name}
                </button>
              );
            })}
          </div>
        </div>

        <div className="h-px bg-border" role="separator" />

        {/* v4.26.0 — "Filters" section header.  The card previously
            flowed Search → Presets → grid-of-mixed-inputs-and-switches
            with no visual demarcation between preset shortcuts and the
            actual filter composition area.  An explicit header makes
            the zone change legible without adding heavyweight chrome. */}
        <div className="flex items-center gap-xs">
          <FilterIcon className="size-4 text-muted-foreground" aria-hidden />
          <h3 className="text-metadata font-semibold uppercase tracking-wider text-muted-foreground">
            Filters
          </h3>
        </div>

        {/* Common filter grid — selects + comboboxes only.  Switches
            moved into their own sub-panel below (v4.26.0) so the
            mixed-cell-height look is gone.  Exception: the
            "first-seen" switch stays paired with the scans combobox
            because its enablement depends on a non-empty scan
            selection — keeping them spatially adjacent surfaces the
            dependency. */}
        <div className="grid gap-md md:grid-cols-2 lg:grid-cols-3">
          {/* Scan discovery filter */}
          <div className="space-y-xxs md:col-span-2 lg:col-span-2">
            <Label htmlFor="hosts-filter-scans" id="hosts-filter-scans-label">Discovered in scans</Label>
            <Combobox
              id="hosts-filter-scans"
              multiple
              options={scanOptions}
              values={filters.scanIds ?? []}
              onValuesChange={(values) =>
                handleFilterChange('scanIds', values.length ? values : undefined)
              }
              placeholder="Select scans…"
              emptyMessage={facetEmpty('No scans available.')}
            />
          </div>

          {/* Wrapped in a label-on-top cell to match the surrounding
              combobox/select cells' vertical rhythm — without this
              wrap the SwitchRow's flex-row alignment misaligned with
              the controls in the same grid track. */}
          <div className="space-y-xxs">
            <Label className="text-metadata text-transparent" aria-hidden>
              &nbsp;
            </Label>
            <SwitchRow
              id="hosts-filter-first-seen"
              label="Only first discovered in selected scans"
              checked={filters.firstSeenInSelectedScans || false}
              disabled={!filters.scanIds || filters.scanIds.length === 0}
              onCheckedChange={(checked) =>
                handleFilterChange('firstSeenInSelectedScans', checked ? true : undefined)
              }
            />
          </div>

          <div className="space-y-xxs">
            <Label htmlFor="hosts-filter-state">Host state</Label>
            <Select
              value={filters.state || 'all'}
              onValueChange={(value) =>
                handleFilterChange('state', value === 'all' ? undefined : value)
              }
            >
              <SelectTrigger id="hosts-filter-state">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {HOST_STATE_VALUES.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* v5.2.0 — common network filters surfaced into the always-visible
              grid (were behind "More filters"). Ports/services/OS/tags/subnets
              are everyday triage filters, so they no longer require a click. */}
          <div className="space-y-xxs">
            <Label htmlFor="hosts-filter-os" id="hosts-filter-os-label">Operating system</Label>
            <Combobox
              id="hosts-filter-os"
              options={osOptions}
              value={filters.osFilter ?? null}
              onChange={(value) => handleFilterChange('osFilter', value ?? undefined)}
              placeholder="Any"
              emptyMessage={facetEmpty('No OSes seen yet.')}
            />
          </div>

          <div className="space-y-xxs">
            <Label htmlFor="hosts-filter-ports" id="hosts-filter-ports-label">Ports</Label>
            <Combobox
              id="hosts-filter-ports"
              multiple
              options={portOptions}
              values={filters.ports ?? []}
              onValuesChange={(values) =>
                handleFilterChange('ports', values.length ? values : undefined)
              }
              placeholder="Select ports…"
              emptyMessage={facetEmpty('No ports seen yet.')}
            />
          </div>

          <div className="space-y-xxs">
            <Label htmlFor="hosts-filter-services" id="hosts-filter-services-label">Services</Label>
            <Combobox
              id="hosts-filter-services"
              multiple
              options={serviceOptions}
              values={filters.services ?? []}
              onValuesChange={(values) =>
                handleFilterChange('services', values.length ? values : undefined)
              }
              placeholder="Select services…"
              emptyMessage={facetEmpty('No services seen yet.')}
            />
          </div>

          <div className="space-y-xxs">
            <Label htmlFor="hosts-filter-subnets" id="hosts-filter-subnets-label">Subnets</Label>
            <Combobox
              id="hosts-filter-subnets"
              multiple
              options={subnetOptions}
              values={filters.subnets ?? []}
              onValuesChange={(values) =>
                handleFilterChange('subnets', values.length ? values : undefined)
              }
              placeholder="Select subnets…"
              emptyMessage={facetEmpty('No subnets configured.')}
            />
          </div>

          <div className="space-y-xxs">
            <Label htmlFor="hosts-filter-tags" id="hosts-filter-tags-label">Tags</Label>
            <Combobox
              id="hosts-filter-tags"
              multiple
              options={tagOptions}
              values={filters.tags ?? []}
              onValuesChange={(values) =>
                handleFilterChange('tags', values.length ? values : undefined)
              }
              placeholder="Filter by tag…"
              emptyMessage={facetEmpty('No tags created yet.')}
            />
          </div>
        </div>

        {/* Boolean-filter panel — extracted from the common grid
            (v4.26.0) so toggles aren't interleaved with comboboxes
            and selects, and so "Only hosts with notes" can live with
            its peers instead of floating in the sticky bar above the
            table.  Two-column layout reads as a checklist at a
            glance. */}
        <div className="rounded-control border border-border/60 bg-muted/30 p-sm">
          <p className="mb-xs text-caption font-semibold uppercase tracking-wider text-muted-foreground">
            Host properties
          </p>
          <div className="grid gap-y-xs gap-x-md md:grid-cols-2">
            <SwitchRow
              id="hosts-filter-open-ports"
              label="Has open ports"
              checked={filters.hasOpenPorts || false}
              onCheckedChange={(checked) =>
                handleFilterChange('hasOpenPorts', checked ? true : undefined)
              }
            />

            <SwitchRow
              id="hosts-filter-oos"
              label="Out of scope only"
              checked={filters.outOfScopeOnly || false}
              onCheckedChange={(checked) =>
                handleFilterChange('outOfScopeOnly', checked ? true : undefined)
              }
            />

            <SwitchRow
              id="hosts-filter-critical"
              label="Critical vulnerabilities"
              checked={filters.hasCriticalVulns || false}
              onCheckedChange={(checked) =>
                handleFilterChange('hasCriticalVulns', checked ? true : undefined)
              }
            />

            <SwitchRow
              id="hosts-filter-high"
              label="High vulnerabilities"
              checked={filters.hasHighVulns || false}
              onCheckedChange={(checked) =>
                handleFilterChange('hasHighVulns', checked ? true : undefined)
              }
            />

            {/* v4.43.0 — "Has PoC / exploit available" filter. Backed by
                Vulnerability.exploitable, which the Nessus parser sets when
                the .nessus ReportItem carries exploit_available=true,
                metasploit_name, core_impact_name, canvas_package, or
                exploit_code_maturity in {functional, high, proof-of-concept}. */}
            <SwitchRow
              id="hosts-filter-exploit-available"
              label="Has PoC / exploit available"
              checked={filters.hasExploitAvailable || false}
              onCheckedChange={(checked) =>
                handleFilterChange('hasExploitAvailable', checked ? true : undefined)
              }
            />

            {/* v4.45.0 — "Has been tested" filter.  Backed by a count of
                TestExecutionResult rows joined via TestPlanEntry.host_id;
                true means an agentic test was actually executed against
                the host (distinct from `test_plan_entry_count` which only
                means "host is in a plan"). */}
            <SwitchRow
              id="hosts-filter-test-execution"
              label="Has been tested (agentic execution)"
              checked={filters.hasTestExecution || false}
              onCheckedChange={(checked) =>
                handleFilterChange('hasTestExecution', checked ? true : undefined)
              }
            />

            <SwitchRow
              id="hosts-filter-assigned-me"
              label="Assigned to me"
              checked={filters.assignedToMe || false}
              onCheckedChange={(checked) =>
                handleFilterChange('assignedToMe', checked ? true : undefined)
              }
            />

            {notesToggleAvailable && (
              <SwitchRow
                id="hosts-filter-only-notes"
                label="Only hosts with notes"
                checked={onlyWithNotes}
                onCheckedChange={handleOnlyWithNotesChange}
              />
            )}
          </div>
        </div>

        {/* Advanced collapse */}
        <div className="flex flex-wrap items-center gap-xs pt-xxs">
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={advancedOpen}
            aria-controls="host-filters-advanced"
            onClick={() => setAdvancedOpen((open) => !open)}
          >
            {advancedOpen ? (
              <ChevronUp className="size-3.5" aria-hidden />
            ) : (
              <ChevronDown className="size-3.5" aria-hidden />
            )}
            {advancedOpen ? 'Hide advanced filters' : 'More filters'}
          </Button>
          {advancedFiltersActive > 0 && !advancedOpen && (
            <Badge variant="default">{advancedFiltersActive} active</Badge>
          )}
        </div>

        {advancedOpen && (
          <div id="host-filters-advanced" className="grid gap-md md:grid-cols-2 lg:grid-cols-3">
            {/* Min risk score */}
            <div className="space-y-xxs">
              <Label htmlFor="hosts-filter-min-risk">Min risk score</Label>
              <Input
                id="hosts-filter-min-risk"
                type="number"
                min={0}
                max={100}
                placeholder="0–100"
                value={filters.minRiskScore ?? ''}
                onChange={(event) => {
                  const raw = event.target.value;
                  if (raw === '') {
                    handleFilterChange('minRiskScore', undefined);
                    return;
                  }
                  const parsed = parseInt(raw, 10);
                  if (Number.isNaN(parsed)) return;
                  const clamped = Math.max(0, Math.min(100, parsed));
                  handleFilterChange('minRiskScore', clamped);
                }}
                aria-invalid={minRiskScoreInvalid || undefined}
                aria-label="Minimum risk score filter (0-100)"
              />
              <p
                className={cn(
                  'text-caption',
                  minRiskScoreInvalid ? 'text-destructive' : 'text-muted-foreground',
                )}
              >
                {minRiskScoreInvalid ? 'Must be between 0 and 100' : 'Risk score 0-100'}
              </p>
            </div>

            {/* Port states */}
            <div className="space-y-xxs">
              <Label htmlFor="hosts-filter-port-states" id="hosts-filter-port-states-label">Port states</Label>
              <Combobox
                id="hosts-filter-port-states"
                multiple
                options={PORT_STATE_OPTIONS}
                values={filters.portStates ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('portStates', values.length ? values : undefined)
                }
                placeholder="Any"
              />
            </div>

            {/* Web interface tri-state */}
            <div className="flex items-start gap-sm">
              <Checkbox
                id="hosts-filter-web"
                checked={
                  filters.hasWebInterface === undefined
                    ? 'indeterminate'
                    : filters.hasWebInterface
                }
                onCheckedChange={() => {
                  const current = filters.hasWebInterface;
                  const next =
                    current === undefined ? true : current === true ? false : undefined;
                  handleFilterChange('hasWebInterface', next);
                }}
                aria-label="Toggle web interface filter (cycles any → has → none)"
              />
              <Label htmlFor="hosts-filter-web" className="text-metadata">
                {webInterfaceLabel}
              </Label>
            </div>

            {/* Tech */}
            <div className="space-y-xxs">
              <Label htmlFor="hosts-filter-tech" id="hosts-filter-tech-label">Technologies</Label>
              <Combobox
                id="hosts-filter-tech"
                multiple
                options={techOptions}
                values={filters.tech ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('tech', values.length ? values : undefined)
                }
                placeholder="Filter by tech stack (nginx, jenkins, …)"
                emptyMessage={facetEmpty('No technologies fingerprinted yet.')}
              />
            </div>

            {/* Subnet labels (v2.86.0) — distinct vocabulary from tags;
                AND between the two groups, OR within each. */}
            <div className="space-y-xxs">
              <Label htmlFor="hosts-filter-subnet-labels" id="hosts-filter-subnet-labels-label">
                Subnet labels
              </Label>
              <Combobox
                id="hosts-filter-subnet-labels"
                multiple
                options={subnetLabelOptions}
                values={filters.subnetLabels ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('subnetLabels', values.length ? values : undefined)
                }
                placeholder="Filter by subnet label…"
                emptyMessage={facetEmpty('No subnet labels created yet.')}
              />
            </div>
          </div>
        )}

        {/* v4.26.0 — the bottom "N filters active" Alert was redundant
            with the top-of-card count badge + Clear-all button (this
            same component, lines ~360-380) and with the active-filter
            chip row in the sticky bar (Hosts.tsx).  Three surfaces for
            one signal; the Alert was the one without per-chip detail,
            so it goes.  Single authoritative chip row lives in the
            sticky bar where users actually scan filter state. */}
      </CardContent>
    </Card>
  );
};

export default HostFilters;
