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
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
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
  hasMediumVulns?: boolean;
  hasLowVulns?: boolean;
  hasExploitAvailable?: boolean;
  hasTestExecution?: boolean;
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
  // so the page state is a single object instead of three useStates.  Both
  // the sticky-bar "Review status" chip group and the card's Review status
  // select write here; absent followFilter means "no review-status filter"
  // (the old 'all' sentinel), absent onlyWithNotes means the toggle is off.
  // See Hosts.tsx for the setter helpers that delete the keys when the user
  // clears them so `Object.keys(filters).length === 0` remains the canonical
  // "nothing filtered" check.  Review status is team-shared (see the backend
  // follow_predicate): 'none' = nobody is reviewing, in_review/reviewed = any
  // teammate.
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
    description: 'Hosts nobody on the team is reviewing yet',
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

// Composable presets: a preset is "applied" when EVERY key it sets is
// satisfied in the live filters (a subset match — extra active filters are
// fine, since presets now compose rather than replace).  Drives both the lit
// state and the toggle-off behaviour.
const presetIsApplied = (preset: HostFilterOptions, current: HostFilterOptions): boolean => {
  const pKeys = definedFilterKeys(preset);
  if (pKeys.length === 0) return false;
  for (const k of pKeys) {
    const pv = (preset as Record<string, unknown>)[k];
    const cv = (current as Record<string, unknown>)[k];
    if (Array.isArray(pv)) {
      if (!Array.isArray(cv) || pv.length !== cv.length) return false;
      const sp = [...pv].sort();
      const sc = [...(cv as unknown[])].sort();
      if (sp.some((v, i) => v !== sc[i])) return false;
    } else if (pv !== cv) {
      return false;
    }
  }
  return true;
};

const PORT_STATE_OPTIONS: ComboboxOption[] = [
  { value: 'open', label: 'Open' },
  { value: 'closed', label: 'Closed' },
  { value: 'filtered', label: 'Filtered' },
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

/**
 * A binary filter toggle rendered as a pressable chip — replaces the row of
 * switches.  Denser, scannable, and consistent with the preset chips; a
 * single click toggles on/off (vs a switch that needs a precise hit).
 */
const ToggleChip: React.FC<{
  label: string;
  active: boolean;
  onToggle: () => void;
  activeClass?: string;
  /** Provenance hint — where this filter's data comes from. */
  tooltip?: string;
}> = ({ label, active, onToggle, activeClass, tooltip }) => {
  const chip = (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className={cn(
        'inline-flex items-center rounded-chip border px-sm py-xxs text-caption font-medium transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        active
          ? activeClass ?? 'border-transparent bg-primary text-primary-foreground ring-1 ring-inset ring-primary-foreground/30'
          : 'border-border bg-card text-foreground hover:bg-accent',
      )}
    >
      {/* Active state is shown by fill colour only — no icon, so the chip's
          width never changes on toggle (a width change re-wraps the flex row
          and shifts the panel) and the label stays centred. */}
      {label}
    </button>
  );
  if (!tooltip) return chip;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{chip}</TooltipTrigger>
      <TooltipContent className="max-w-xs">{tooltip}</TooltipContent>
    </Tooltip>
  );
};

// Vulnerability severities (#48) — the backend ORs the selected `has_*_vulns`
// flags into one severity match, so this reads as one multi-select.  Tooltips
// state provenance: severity rows come from imported vuln scanners only.
const SEVERITY_SOURCE = 'from imported vulnerability scans (Nessus, OpenVAS, Nikto).';
const SEVERITY_FILTERS: Array<{ key: keyof HostFilterOptions; label: string; activeClass: string; tooltip: string }> = [
  { key: 'hasCriticalVulns', label: 'Critical', activeClass: 'border-transparent bg-destructive text-destructive-foreground', tooltip: `Hosts with ≥1 Critical-severity finding — ${SEVERITY_SOURCE}` },
  { key: 'hasHighVulns', label: 'High', activeClass: 'border-transparent bg-warning text-warning-foreground', tooltip: `Hosts with ≥1 High-severity finding — ${SEVERITY_SOURCE}` },
  { key: 'hasMediumVulns', label: 'Medium', activeClass: 'border-transparent bg-info text-info-foreground', tooltip: `Hosts with ≥1 Medium-severity finding — ${SEVERITY_SOURCE}` },
  { key: 'hasLowVulns', label: 'Low', activeClass: 'border-transparent bg-success text-success-foreground', tooltip: `Hosts with ≥1 Low-severity finding — ${SEVERITY_SOURCE}` },
];

// Binary "show only" property filters (#42/#4) — each means "only hosts WITH
// this", off means "don't filter" (the backend has_* params are positive-only).
// Tooltips note provenance, verified against the parsers/predicates.
const PROPERTY_FILTERS: Array<{ key: keyof HostFilterOptions; label: string; tooltip: string }> = [
  { key: 'hasOpenPorts', label: 'Open ports', tooltip: 'Hosts with ≥1 open port — from any port scan (Nmap, Masscan, Naabu, RustScan…).' },
  { key: 'hasExploitAvailable', label: 'Exploitable', tooltip: 'Hosts with a finding flagged exploitable — Nessus only: set when the plugin reports exploit_available, a Metasploit / Core Impact / Canvas module, or proof-of-concept-or-higher maturity.' },
  { key: 'hasTestExecution', label: 'Tested by agent', tooltip: 'Hosts an agentic test plan was actually executed against (not merely drafted) — from the agent execution workflow.' },
  { key: 'outOfScopeOnly', label: 'Out of scope', tooltip: 'Hosts outside every subnet in your defined scope — from your uploaded scope/subnets.' },
  { key: 'assignedToMe', label: 'Assigned to me', tooltip: 'Hosts you own — explicitly assigned to you (the bulk Assign action) or that you took In Review / Reviewed.' },
];

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
  const noFiltersActive = definedFilterKeys(filters).length === 0;
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
      filters.hasMediumVulns !== undefined,
      filters.hasLowVulns !== undefined,
      filters.hasExploitAvailable !== undefined,
      filters.hasTestExecution !== undefined,
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
      (filters.portStates?.length ?? 0) > 0,
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
    // Compose, don't replace: clicking a preset toggles ITS keys on top of
    // whatever is already active, so a preset never silently wipes the user's
    // other filters.  Clicking a lit preset removes just its keys.
    const updated = { ...filters } as Record<string, unknown>;
    if (presetIsApplied(preset.filters, filters)) {
      for (const k of definedFilterKeys(preset.filters)) delete updated[k];
    } else {
      Object.assign(updated, preset.filters);
    }
    onFiltersChange(updated as HostFilterOptions);
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
            Click presets to combine them with your filters; click a lit preset to remove just its part. "All Hosts" clears everything.
          </p>
          <div className="flex flex-wrap gap-xs" role="group" aria-label="Filter presets">
            <button
              key="all"
              type="button"
              onClick={() => onFiltersChange({})}
              aria-pressed={noFiltersActive}
              title="Clear all filters"
              className={cn(
                'inline-flex items-center gap-xxs rounded-control border px-sm py-xxs text-caption font-medium transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                noFiltersActive
                  ? 'border-transparent bg-primary text-primary-foreground ring-1 ring-inset ring-primary-foreground/30'
                  : 'border-border bg-card text-foreground hover:bg-accent',
              )}
            >
              {noFiltersActive && <Check className="size-3" aria-hidden />}
              All Hosts
            </button>
            {HOST_FILTER_PRESETS.map((preset) => {
              const active = presetIsApplied(preset.filters, filters);
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

          {/* Host state control removed (v5.65.1) — in practice every
              detected host is "up" (or "unknown"); the nmap parser drops bare
              down-hosts, so the Up/Down select was clutter that also hid
              "unknown" hosts.  The `state` param stays for DSL `state:` use. */}

          {/* Review status — team-shared.  Lives in the card (not only the
              sticky-bar chips) so it composes with the property toggles
              below: e.g. "Critical vulnerabilities" + "Not reviewed" =
              critical hosts nobody is looking at.  Writes the same
              filters.followFilter the sticky chips do, so the two stay in
              sync. */}
          <div className="space-y-xxs">
            <Label htmlFor="hosts-filter-review">Review status</Label>
            <Select
              value={filters.followFilter ?? 'any'}
              onValueChange={(value) =>
                handleFilterChange(
                  'followFilter',
                  value === 'any' ? undefined : (value as 'none' | FollowStatus),
                )
              }
            >
              <SelectTrigger id="hosts-filter-review">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="any">Any</SelectItem>
                <SelectItem value="none">Not reviewed</SelectItem>
                <SelectItem value="in_review">In review</SelectItem>
                <SelectItem value="reviewed">Reviewed</SelectItem>
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

        {/* Property filters as toggle-chips (v5.64.0) — replaces the row of
            switches + the Web-interface dropdown.  Denser, single-click, and
            consistent; severity becomes one multi-select that mirrors the
            backend's OR; web surface is honest about meaning "recorded web
            row", not "proof of (no) web service". */}
        <div className="space-y-sm rounded-control border border-border/60 bg-muted/30 p-sm">
          <div>
            <p className="mb-xs text-caption font-semibold uppercase tracking-wider text-muted-foreground">
              Vulnerability severity
              <span className="ml-xs font-normal normal-case tracking-normal text-muted-foreground/80">matches any selected</span>
            </p>
            <div className="flex flex-wrap gap-xs">
              {SEVERITY_FILTERS.map((s) => (
                <ToggleChip
                  key={s.key}
                  label={s.label}
                  active={filters[s.key] === true}
                  activeClass={s.activeClass}
                  tooltip={s.tooltip}
                  onToggle={() => handleFilterChange(s.key, filters[s.key] ? undefined : true)}
                />
              ))}
            </div>
          </div>

          <div>
            <p className="mb-xs text-caption font-semibold uppercase tracking-wider text-muted-foreground">Show only</p>
            <div className="flex flex-wrap gap-xs">
              {PROPERTY_FILTERS.map((p) => (
                <ToggleChip
                  key={p.key}
                  label={p.label}
                  active={filters[p.key] === true}
                  tooltip={p.tooltip}
                  onToggle={() => handleFilterChange(p.key, filters[p.key] ? undefined : true)}
                />
              ))}
              {notesToggleAvailable && (
                <ToggleChip
                  label="With notes"
                  active={onlyWithNotes}
                  tooltip="Hosts with ≥1 analyst note — from in-app notes."
                  onToggle={() => handleOnlyWithNotesChange(!onlyWithNotes)}
                />
              )}
            </div>
          </div>

          <div>
            <p className="mb-xs text-caption font-semibold uppercase tracking-wider text-muted-foreground">Web surface</p>
            <div className="flex flex-wrap gap-xs">
              <ToggleChip
                label="Detected"
                active={filters.hasWebInterface === true}
                tooltip="Hosts with a detected web interface — from web-detection imports (httpx, EyeWitness, WhatWeb)."
                onToggle={() => handleFilterChange('hasWebInterface', filters.hasWebInterface === true ? undefined : true)}
              />
              <ToggleChip
                label="Not detected"
                active={filters.hasWebInterface === false}
                tooltip="Hosts with no recorded web-interface row — absence of evidence (httpx/EyeWitness/WhatWeb never saw one), not proof there's no web service."
                onToggle={() => handleFilterChange('hasWebInterface', filters.hasWebInterface === false ? undefined : false)}
              />
            </div>
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
