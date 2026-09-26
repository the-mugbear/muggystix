/**
 * The Hosts filter MODEL: the `HostFilterOptions` shape, the presets (built-in
 * views + port groups) and how a port group toggles.  The filter PANEL that
 * used to live here (a 900-line card of every control at once) was replaced by
 * the catalog in `hosts/HostFilterPopover.tsx` (5.250.0) and deleted in
 * 5.251.1; the file keeps its name because a dozen modules import the type
 * from it.
 */
import {
  ClipboardCheck,
  ClipboardList,
  Computer as ComputerIcon,
  Eye,
  FileText,
  Globe,
  Network as NetworkIcon,
  ShieldAlert,
  Shield,
  ShieldCheck,
} from 'lucide-react';
import type { FollowStatus } from '../services/api';

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
  // RDAP network attribution — registered owner(s), ASN(s), ISO country
  // code(s) of the host's netblock. Empty facets hide the controls, so these
  // only appear once a project has ingested RDAP output.
  orgs?: string[];
  asns?: string[];
  countries?: string[];
  // v2.423.0 — weakness / access flags (the DSL's has: values) and
  // misconfiguration check ids; OR within each.
  weaknesses?: string[];
  checks?: string[];
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

// Every preset, in one list; how each is APPLIED depends on what it is (see
// the split below): a built-in view replaces the applied filters, a port
// group adds to them.  `activeFilterPresetId` is an exact match — extra
// filters on top of a view mean it is no longer that view.
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
    description: 'Assigned to you and not yet marked Reviewed by anyone — includes what you already have In review',
    // NOT `followFilter: 'none'`: taking a host In review is what assigns most
    // hosts, so "assigned to me AND review not started" was empty by
    // construction — the queue lost every host the moment work began on it.
    filters: { assignedToMe: true, query: 'NOT follow:reviewed' },
  },
  {
    id: 'not_reviewed',
    name: 'Review not started',
    Icon: Eye,
    description: 'Hosts nobody on the team has taken In review or marked Reviewed',
    filters: { followFilter: 'none' },
  },
  {
    id: 'critical',
    name: 'Critical observations',
    Icon: ShieldAlert,
    description: 'Hosts with a critical-severity scanner observation',
    filters: { hasCriticalVulns: true },
  },
  {
    // The id is kept for anything that stored it; the name no longer claims
    // business value — the preset measures severity and exposure, nothing else.
    id: 'high_value',
    name: 'High severity + open ports',
    Icon: ShieldCheck,
    description: 'Hosts with a high-severity scanner observation and at least one open port',
    filters: { hasHighVulns: true, hasOpenPorts: true },
  },
  {
    id: 'out_of_scope',
    name: 'Out of Scope',
    Icon: Globe,
    description: 'Hosts in no scope subnet and not reachable through an in-scope name',
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
    name: 'Windows service ports',
    Icon: ComputerIcon,
    description: 'Open 135/139/445 — ports Windows commonly exposes; not an OS identification',
    filters: { ports: ['135', '139', '445'], portStates: ['open'] },
  },
  {
    id: 'legacy',
    name: 'FTP / Telnet / DNS / TFTP / NetBIOS',
    Icon: ShieldAlert,
    description: 'Open 21/23/53/69/135/139 — whether any of these is a problem depends on the host',
    filters: { ports: ['21', '23', '53', '69', '135', '139'], portStates: ['open'] },
  },
  {
    id: 'planned_not_tested',
    name: 'Planned, not tested',
    Icon: ClipboardList,
    description: 'Approved in a test plan but no results recorded yet — work that never ran',
    filters: { query: 'has:planned AND NOT has:tested' },
  },
];

// Two different things shared one chip row (5.249.0).  A PORT GROUP adds ports
// or services to the endpoint filter and composes with everything else — it
// stays in the filter panel.  A BUILT-IN VIEW is a whole named question ("my
// review queue"); it lives in the View picker beside the saved views and, like
// them, REPLACES the applied filters.
const isPortGroup = (preset: { filters: HostFilterOptions }) =>
  Boolean(preset.filters.ports?.length || preset.filters.services?.length);
export const HOST_PORT_GROUP_PRESETS = HOST_FILTER_PRESETS.filter(isPortGroup);
export const HOST_BUILT_IN_VIEWS = HOST_FILTER_PRESETS.filter((p) => !isPortGroup(p));

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
// satisfied in the live filters.  A list key is satisfied when the live list
// CONTAINS the preset's values — a list is "match any", so SSH (22) and
// Windows (135/139/445) are both applied under ports 22,135,139,445.  Drives
// both the lit state and the toggle-off behaviour.
const presetIsApplied = (preset: HostFilterOptions, current: HostFilterOptions): boolean => {
  const pKeys = definedFilterKeys(preset);
  if (pKeys.length === 0) return false;
  for (const k of pKeys) {
    const pv = (preset as Record<string, unknown>)[k];
    const cv = (current as Record<string, unknown>)[k];
    if (Array.isArray(pv)) {
      if (!Array.isArray(cv) || pv.some((v) => !cv.includes(v))) return false;
    } else if (pv !== cv) {
      return false;
    }
  }
  return true;
};

/**
 * Toggle one preset against the live filters, by VALUE rather than by key.
 * Several presets write the same keys (`ports`, `portStates`): assigning a
 * key overwrote the other preset's ports, and deleting a key on toggle-off
 * took the still-lit preset's `portStates` with it.  On: list values are
 * added to what is there.  Off: a value or key stays when another applied
 * preset still needs it.
 */
export const togglePreset = (
  preset: HostFilterOptions,
  filters: HostFilterOptions,
  allPresets: HostFilterOptions[] = HOST_FILTER_PRESETS.map((p) => p.filters),
): HostFilterOptions => {
  const updated = { ...filters } as Record<string, unknown>;
  const presetKeys = definedFilterKeys(preset);

  if (!presetIsApplied(preset, filters)) {
    for (const k of presetKeys) {
      const pv = (preset as Record<string, unknown>)[k];
      const cv = updated[k];
      updated[k] = Array.isArray(pv) && Array.isArray(cv)
        ? [...cv, ...pv.filter((v) => !cv.includes(v))]
        : pv;
    }
    return updated as HostFilterOptions;
  }

  // A preset this one IMPLIES ("Review not started" inside "My review queue")
  // lights up with it but was never chosen, so it holds nothing back.
  const stillApplied = allPresets.filter(
    (p) => p !== preset && presetIsApplied(p, filters) && !presetIsApplied(p, preset),
  );
  for (const k of presetKeys) {
    const pv = (preset as Record<string, unknown>)[k];
    const claims = stillApplied
      .map((p) => (p as Record<string, unknown>)[k])
      .filter((v) => v !== undefined);
    if (Array.isArray(pv)) {
      const claimed = claims.flatMap((v) => (Array.isArray(v) ? v : []));
      const remaining = ((updated[k] as unknown[]) ?? []).filter(
        (v) => !pv.includes(v) || claimed.includes(v),
      );
      if (remaining.length > 0) updated[k] = remaining;
      else delete updated[k];
    } else if (claims.length === 0) {
      delete updated[k];
    }
  }
  return updated as HostFilterOptions;
};
