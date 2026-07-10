import React, { useEffect, useMemo, useState } from 'react';
import {
  Check,
  ClipboardCheck,
  Computer as ComputerIcon,
  Eye,
  FileText,
  Filter as FilterIcon,
  Globe,
  Info,
  Network as NetworkIcon,
  ShieldAlert,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { Link } from 'react-router-dom';
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
  // Site names (a host matches if any of its subnets belongs to the site).
  sites?: string[];
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
    id: 'my_queue',
    name: 'My review queue',
    Icon: ClipboardCheck,
    description: "Assigned to me and not yet reviewed — your queue to work through",
    filters: { assignedToMe: true, followFilter: 'none' },
  },
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

/**
 * A dropdown label with a provenance hint.  The (i) icon's tooltip names the
 * scanner/tool (or in-app action) that populates this filter, so an empty
 * dropdown is self-explanatory.  Provenance text is also exposed as an
 * `aria-label` and the trigger is focusable, so it's reachable without a hover.
 */
const FilterLabel: React.FC<{
  htmlFor: string;
  id?: string;
  provenance: string;
  children: React.ReactNode;
}> = ({ htmlFor, id, provenance, children }) => (
  <div className="flex items-center gap-xxs">
    <Label htmlFor={htmlFor} id={id}>
      {children}
    </Label>
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className="inline-flex shrink-0 cursor-help text-muted-foreground/70 hover:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 rounded-full"
          tabIndex={0}
          role="img"
          aria-label={provenance}
        >
          <Info className="h-3.5 w-3.5" aria-hidden />
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{provenance}</TooltipContent>
    </Tooltip>
  </div>
);

/**
 * Inline "no data yet — run X" caption shown beneath a filter whose facet is
 * empty (and not still loading).  Coaches a fresh-project user toward the scan
 * or action that populates it; `commandsLink` optionally links to the Tool
 * Reference for the exact BlueStick-ingestible command.
 */
const FieldHint: React.FC<{ children: React.ReactNode; commandsLink?: boolean }> = ({
  children,
  commandsLink,
}) => (
  <p className="text-caption text-muted-foreground break-words">
    {children}
    {commandsLink && (
      <>
        {' '}
        <Link to="/tool-reference" className="underline underline-offset-2 hover:text-foreground">
          See commands
        </Link>
      </>
    )}
  </p>
);

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
// Tooltips note provenance, verified against the parsers/predicates; `group`
// places each chip in its intent section (#43).
type FilterGroup = 'workflow' | 'risk' | 'exposure' | 'inventory';
const PROPERTY_FILTERS: Array<{ key: keyof HostFilterOptions; label: string; tooltip: string; group: FilterGroup }> = [
  { key: 'hasOpenPorts', label: 'Open ports', group: 'exposure', tooltip: 'Hosts with ≥1 open port — from any port scan (Nmap, Masscan, Naabu, RustScan…).' },
  { key: 'hasExploitAvailable', label: 'Exploitable', group: 'risk', tooltip: 'Hosts with a finding flagged exploitable — Nessus only: set when the plugin reports exploit_available, a Metasploit / Core Impact / Canvas module, or proof-of-concept-or-higher maturity.' },
  { key: 'hasTestExecution', label: 'Tested by agent', group: 'workflow', tooltip: 'Hosts an agentic test plan was actually executed against (not merely drafted) — from the agent execution workflow.' },
  { key: 'outOfScopeOnly', label: 'Out of scope', group: 'inventory', tooltip: 'Hosts outside every subnet in your defined scope — from your uploaded scope/subnets.' },
  { key: 'assignedToMe', label: 'Assigned to me', group: 'workflow', tooltip: 'Hosts you own — explicitly assigned to you (the bulk Assign action) or that you took In Review / Reviewed.' },
];

/** A labelled filter group (#43) — one intent (Workflow / Risk / …) per box. */
const FilterSection: React.FC<{ title: string; hint?: string; children: React.ReactNode }> = ({ title, hint, children }) => (
  <div className="space-y-sm">
    <div className="flex items-baseline gap-xs">
      <h3 className="text-metadata font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>
      {hint && <span className="text-caption font-normal text-muted-foreground/80">{hint}</span>}
    </div>
    {children}
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
  const noFiltersActive = definedFilterKeys(filters).length === 0;
  // v5.2.0 — the legacy "Search hosts" field was removed; bare-text search now
  // lives in the command bar (maps to filters.query). The `/` focus shortcut
  // moved there too. filters.search may still arrive from a saved view or an
  // older shared URL, so it stays in the active-filter model (chip + counts).

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
      (filters.sites?.length ?? 0) > 0,
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

  // Site options — value is the site NAME (the filter matches by name).
  const siteOptions: ComboboxOption[] = useMemo(() => {
    return (
      availableData?.sites?.map((s) => ({
        value: s.name,
        label: s.name,
        trailing: `${s.host_count}`,
      })) || []
    );
  }, [availableData?.sites]);

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

        {/* Intent-grouped sections (v5.66.1) — replaces the mixed grid +
            "Show only" panel + nested "More filters" disclosure with one flat
            surface organised by what an analyst is actually asking. */}

        {/* WORKFLOW — ownership, notes.  Review status is NOT here: it lives
            in the always-visible chip row above the table (#45 — the card
            dropdown was a redundant second control on the same followFilter). */}
        <FilterSection title="Workflow" hint="review status: use the chips above the table">
          <div className="flex flex-wrap gap-xs">
            {PROPERTY_FILTERS.filter((p) => p.group === 'workflow').map((p) => (
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
        </FilterSection>

        <div className="h-px bg-border" role="separator" />

        {/* RISK — vulnerabilities */}
        <FilterSection title="Risk" hint="severity matches any selected">
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
            {PROPERTY_FILTERS.filter((p) => p.group === 'risk').map((p) => (
              <ToggleChip
                key={p.key}
                label={p.label}
                active={filters[p.key] === true}
                tooltip={p.tooltip}
                onToggle={() => handleFilterChange(p.key, filters[p.key] ? undefined : true)}
              />
            ))}
          </div>
        </FilterSection>

        <div className="h-px bg-border" role="separator" />

        {/* NETWORK EXPOSURE — web surface, open ports/services/tech */}
        <FilterSection title="Network exposure">
          <div className="flex flex-wrap gap-xs">
            {PROPERTY_FILTERS.filter((p) => p.group === 'exposure').map((p) => (
              <ToggleChip
                key={p.key}
                label={p.label}
                active={filters[p.key] === true}
                tooltip={p.tooltip}
                onToggle={() => handleFilterChange(p.key, filters[p.key] ? undefined : true)}
              />
            ))}
            <ToggleChip
              label="Web: detected"
              active={filters.hasWebInterface === true}
              tooltip="Hosts with a detected web interface — from web-detection imports (httpx, EyeWitness, WhatWeb)."
              onToggle={() => handleFilterChange('hasWebInterface', filters.hasWebInterface === true ? undefined : true)}
            />
            <ToggleChip
              label="Web: not detected"
              active={filters.hasWebInterface === false}
              tooltip="Hosts with no recorded web-interface row — absence of evidence (httpx/EyeWitness/WhatWeb never saw one), not proof there's no web service."
              onToggle={() => handleFilterChange('hasWebInterface', filters.hasWebInterface === false ? undefined : false)}
            />
          </div>
          <div className="grid gap-md md:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-xxs">
              <FilterLabel
                htmlFor="hosts-filter-ports"
                id="hosts-filter-ports-label"
                provenance="Populated by port scans — Nmap, Masscan, Naabu, RustScan."
              >
                Ports
              </FilterLabel>
              <Combobox
                id="hosts-filter-ports"
                multiple
                options={portOptions}
                values={filters.ports ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('ports', values.length ? values : undefined)
                }
                placeholder="Select ports…"
                emptyMessage={facetEmpty('No ports yet — run a port scan (Nmap/Masscan).')}
              />
              {portOptions.length === 0 && !optionsLoading && (
                <FieldHint commandsLink>
                  No ports yet — run a port scan (Nmap/Masscan) and upload it.
                </FieldHint>
              )}
            </div>
            <div className="space-y-xxs">
              <FilterLabel
                htmlFor="hosts-filter-services"
                id="hosts-filter-services-label"
                provenance="Service names from version detection — nmap -sV (also Masscan/Naabu banners)."
              >
                Services
              </FilterLabel>
              <Combobox
                id="hosts-filter-services"
                multiple
                options={serviceOptions}
                values={filters.services ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('services', values.length ? values : undefined)
                }
                placeholder="Select services…"
                emptyMessage={facetEmpty('No services yet — run nmap -sV.')}
              />
              {serviceOptions.length === 0 && !optionsLoading && (
                <FieldHint commandsLink>
                  No services yet — run a version scan (nmap -sV) and upload it.
                </FieldHint>
              )}
            </div>
            {/* Port states control removed (v5.67.1) — in practice every
                recorded port is "open" (closed/filtered are almost never
                ingested), so the Open/Closed/Filtered picker was clutter.
                The `portStates` param stays for DSL `portstate:` use. */}
            <div className="space-y-xxs">
              <FilterLabel
                htmlFor="hosts-filter-tech"
                id="hosts-filter-tech-label"
                provenance="Web fingerprinting — httpx, WhatWeb."
              >
                Technologies
              </FilterLabel>
              <Combobox
                id="hosts-filter-tech"
                multiple
                options={techOptions}
                values={filters.tech ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('tech', values.length ? values : undefined)
                }
                placeholder="Filter by tech stack (nginx, jenkins, …)"
                emptyMessage={facetEmpty('No technologies yet — run httpx or WhatWeb.')}
              />
              {techOptions.length === 0 && !optionsLoading && (
                <FieldHint commandsLink>
                  No technologies yet — run httpx or WhatWeb and upload the output.
                </FieldHint>
              )}
            </div>
          </div>
        </FilterSection>

        <div className="h-px bg-border" role="separator" />

        {/* INVENTORY & LOCATION — OS, network location, labels */}
        <FilterSection title="Inventory & location">
          <div className="flex flex-wrap gap-xs">
            {PROPERTY_FILTERS.filter((p) => p.group === 'inventory').map((p) => (
              <ToggleChip
                key={p.key}
                label={p.label}
                active={filters[p.key] === true}
                tooltip={p.tooltip}
                onToggle={() => handleFilterChange(p.key, filters[p.key] ? undefined : true)}
              />
            ))}
          </div>
          <div className="grid gap-md md:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-xxs">
              <FilterLabel
                htmlFor="hosts-filter-os"
                id="hosts-filter-os-label"
                provenance="OS detection — nmap -O (also NetExec, Nessus)."
              >
                Operating system
              </FilterLabel>
              <Combobox
                id="hosts-filter-os"
                options={osOptions}
                value={filters.osFilter ?? null}
                onChange={(value) => handleFilterChange('osFilter', value ?? undefined)}
                placeholder="Any"
                emptyMessage={facetEmpty('No OS data yet — run nmap -O.')}
              />
              {osOptions.length === 0 && !optionsLoading && (
                <FieldHint commandsLink>
                  No OS data yet — run OS detection (nmap -O) and upload it.
                </FieldHint>
              )}
            </div>
            <div className="space-y-xxs">
              <FilterLabel
                htmlFor="hosts-filter-subnets"
                id="hosts-filter-subnets-label"
                provenance="Your uploaded scope / subnet definitions (not a scanner)."
              >
                Subnets
              </FilterLabel>
              <Combobox
                id="hosts-filter-subnets"
                multiple
                options={subnetOptions}
                values={filters.subnets ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('subnets', values.length ? values : undefined)
                }
                placeholder="Select subnets…"
                emptyMessage={facetEmpty('No subnets yet — upload a scope/subnet file.')}
              />
              {subnetOptions.length === 0 && !optionsLoading && (
                <FieldHint>No subnets yet — upload a scope/subnet file to define your network.</FieldHint>
              )}
            </div>
            <div className="space-y-xxs">
              <FilterLabel
                htmlFor="hosts-filter-sites"
                id="hosts-filter-sites-label"
                provenance="Site names set on your uploaded subnets."
              >
                Site
              </FilterLabel>
              <Combobox
                id="hosts-filter-sites"
                multiple
                options={siteOptions}
                values={filters.sites ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('sites', values.length ? values : undefined)
                }
                placeholder="Select sites…"
                emptyMessage={facetEmpty('No sites yet — set a site on a subnet/scope.')}
              />
              {siteOptions.length === 0 && !optionsLoading && (
                <FieldHint>No sites yet — assign a site to a subnet or scope.</FieldHint>
              )}
            </div>
            <div className="space-y-xxs">
              <FilterLabel
                htmlFor="hosts-filter-tags"
                id="hosts-filter-tags-label"
                provenance="Tags you create in-app on hosts."
              >
                Tags
              </FilterLabel>
              <Combobox
                id="hosts-filter-tags"
                multiple
                options={tagOptions}
                values={filters.tags ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('tags', values.length ? values : undefined)
                }
                placeholder="Filter by tag…"
                emptyMessage={facetEmpty('No tags yet — create one from a host.')}
              />
              {tagOptions.length === 0 && !optionsLoading && (
                <FieldHint>No tags yet — create one from a host's Tags menu.</FieldHint>
              )}
            </div>
            <div className="space-y-xxs">
              <FilterLabel
                htmlFor="hosts-filter-subnet-labels"
                id="hosts-filter-subnet-labels-label"
                provenance="Labels you create in-app on subnets."
              >
                Subnet labels
              </FilterLabel>
              <Combobox
                id="hosts-filter-subnet-labels"
                multiple
                options={subnetLabelOptions}
                values={filters.subnetLabels ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('subnetLabels', values.length ? values : undefined)
                }
                placeholder="Filter by subnet label…"
                emptyMessage={facetEmpty('No subnet labels yet — create one in Subnet management.')}
              />
              {subnetLabelOptions.length === 0 && !optionsLoading && (
                <FieldHint>No subnet labels yet — create one in Subnet management.</FieldHint>
              )}
            </div>
          </div>
        </FilterSection>

        <div className="h-px bg-border" role="separator" />

        {/* DISCOVERY — which scans found the host */}
        <FilterSection title="Discovery">
          <div className="grid gap-md md:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-xxs md:col-span-2 lg:col-span-2">
              <FilterLabel
                htmlFor="hosts-filter-scans"
                id="hosts-filter-scans-label"
                provenance="Every scan you upload (any tool)."
              >
                Discovered in scans
              </FilterLabel>
              <Combobox
                id="hosts-filter-scans"
                multiple
                options={scanOptions}
                values={filters.scanIds ?? []}
                onValuesChange={(values) =>
                  handleFilterChange('scanIds', values.length ? values : undefined)
                }
                placeholder="Select scans…"
                emptyMessage={facetEmpty('No scans yet — upload a scan file.')}
              />
              {scanOptions.length === 0 && !optionsLoading && (
                <FieldHint commandsLink>No scans yet — run a scan and upload the results.</FieldHint>
              )}
            </div>
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
          </div>
        </FilterSection>

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
