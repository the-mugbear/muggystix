import type { HostFilterData } from '../../services/api';
import type { HostFilterOptions } from '../HostFilters';

/**
 * The Hosts filter catalog: every structured filter as one described FIELD.
 *
 * "+ Add filter" lists these by what the analyst is asking; choosing one opens
 * that field's editor, and an applied condition's chip opens the same editor —
 * so there is never a second, independent version of a control.  The registry
 * is data so the catalog, its search, the editors and the chip → editor link
 * all read one list (`tests/components/hostFilterFields.test.ts` pins that
 * every structured filter key is reachable from it).
 *
 * Nothing here adds a backend capability: each field writes the same
 * `HostFilterOptions` keys the old panel wrote.
 */

export type FilterCategory = 'network' | 'services' | 'observations' | 'work' | 'discovery';

export const FILTER_CATEGORIES: Array<{ id: FilterCategory; label: string }> = [
  { id: 'network', label: 'Network & scope' },
  { id: 'services', label: 'Services & web evidence' },
  { id: 'observations', label: 'Scanner observations' },
  { id: 'work', label: 'Analyst work' },
  { id: 'discovery', label: 'Discovery & attribution' },
];

export interface FilterValueOption {
  value: string;
  label: string;
  /** Distinct hosts with this value under the OTHER applied conditions. */
  count?: number;
  description?: string;
  keywords?: string[];
}

interface FieldBase {
  id: string;
  label: string;
  category: FilterCategory;
  /** Shown on the catalog's first screen, above the categories. */
  common?: boolean;
  /** Extra words the catalog search matches ("unreviewed", "certificate"…). */
  keywords?: string[];
  /** What the field means and where its data comes from. */
  help: string;
  /** Filter keys the field owns — its editor writes these and nothing else. */
  keys: Array<keyof HostFilterOptions>;
  /** Chip (utils/hostConditionChips.ts `key`) that opens this editor. */
  chipKey: string;
}

/** Match ANY of the selected values; edited as a draft, applied once. */
export interface MultiField extends FieldBase {
  kind: 'multi';
  key: keyof HostFilterOptions;
  options: (data: HostFilterData | null) => FilterValueOption[];
  /** Server-side cap on the option list; reaching it means "more exist". */
  cap?: number;
  /** How to reach a value beyond the cap. */
  queryHint?: string;
  /** Shown when the project has no data for the field at all. */
  noData: string;
}

/** One value or none; applies immediately. */
export interface SingleField extends FieldBase {
  kind: 'single';
  key: keyof HostFilterOptions;
  options: (data: HostFilterData | null) => FilterValueOption[];
  cap?: number;
  queryHint?: string;
  noData: string;
}

/** A fixed choice list; applies immediately.  `value: undefined` is "Any". */
export interface ChoiceField extends FieldBase {
  kind: 'choice';
  key: keyof HostFilterOptions;
  choices: Array<{ value: string | boolean | undefined; label: string; help?: string }>;
}

/** Positive-only: on, or not filtering.  There is no "No" — the backend has
 *  no negative predicate for these, so the catalog never offers one. */
export interface ToggleField extends FieldBase {
  kind: 'toggle';
  key: keyof HostFilterOptions;
}

/** Bespoke editors (several keys that make ONE condition). */
export interface CompositeField extends FieldBase {
  kind: 'severity' | 'endpoint' | 'scans';
}

export type HostFilterField = MultiField | SingleField | ChoiceField | ToggleField | CompositeField;

const counted = <T>(rows: T[] | undefined, map: (row: T) => FilterValueOption): FilterValueOption[] =>
  (rows ?? []).map(map);

/** Ports, de-duplicated across protocols/services, most common first. */
export const portOptions = (data: HostFilterData | null): FilterValueOption[] => {
  const byPort = new Map<number, { service: string; count: number }>();
  (data?.common_ports ?? []).forEach((p) => {
    const seen = byPort.get(p.port);
    if (!seen || seen.count < p.count) byPort.set(p.port, { service: p.service, count: p.count });
  });
  return Array.from(byPort.entries())
    .map(([port, d]) => ({
      value: String(port),
      label: d.service ? `${port} (${d.service})` : String(port),
      count: d.count,
      keywords: d.service ? [d.service] : undefined,
    }))
    .sort((a, b) => (b.count ?? 0) - (a.count ?? 0));
};

export const serviceOptions = (data: HostFilterData | null): FilterValueOption[] =>
  counted(data?.services, (s) => ({ value: s.name, label: s.name, count: s.count }));

export const scanOptions = (data: HostFilterData | null): FilterValueOption[] =>
  counted(data?.scans, (scan) => ({
    value: String(scan.id),
    label: scan.filename || `Scan #${scan.id}`,
    description: [scan.tool_name ?? 'unknown tool', scan.created_at && new Date(scan.created_at).toLocaleDateString()]
      .filter(Boolean)
      .join(' · '),
    keywords: scan.tool_name ? [scan.tool_name] : undefined,
  }));

export const HOST_FILTER_FIELDS: HostFilterField[] = [
  // ── Network & scope ────────────────────────────────────────────────────
  {
    kind: 'multi', id: 'subnets', key: 'subnets', keys: ['subnets'], chipKey: 'subnets',
    label: 'Subnet', category: 'network', common: true, keywords: ['cidr', 'network', 'range'],
    help: 'Hosts inside any selected subnet — from your uploaded scope, not a scanner.',
    options: (d) => counted(d?.subnets, (s) => ({ value: s.cidr, label: s.cidr, count: s.host_count })),
    cap: 200, queryHint: 'subnet:10.0.0.0/24',
    noData: 'No subnets yet — upload a scope file to define your network.',
  },
  {
    kind: 'multi', id: 'sites', key: 'sites', keys: ['sites'], chipKey: 'sites',
    label: 'Site', category: 'network', keywords: ['location', 'office'],
    help: 'Hosts in any subnet that belongs to a selected site — site names set on your subnets.',
    options: (d) => counted(d?.sites, (s) => ({ value: s.name, label: s.name, count: s.host_count })),
    cap: 200, queryHint: 'site:"East"',
    noData: 'No sites yet — name a site on a subnet in Scopes.',
  },
  {
    kind: 'toggle', id: 'outOfScopeOnly', key: 'outOfScopeOnly', keys: ['outOfScopeOnly'], chipKey: 'outOfScopeOnly',
    label: 'Out of scope', category: 'network', keywords: ['scope', 'coverage', 'unscoped'],
    help: 'Hosts in no scope subnet AND not reachable through an in-scope name. A name resolving to an address does not put that address in subnet scope.',
  },
  {
    kind: 'multi', id: 'tags', key: 'tags', keys: ['tags'], chipKey: 'tags',
    label: 'Host tags', category: 'network', keywords: ['label', 'tag'],
    help: 'Hosts carrying any selected tag — applied by analysts in BlueStick.',
    options: (d) => counted(d?.tags, (t) => ({ value: String(t.id), label: t.name, count: t.host_count })),
    cap: 200, queryHint: 'tag:prod',
    noData: 'No tags yet — tag hosts from the table\'s bulk actions.',
  },
  {
    kind: 'multi', id: 'subnetLabels', key: 'subnetLabels', keys: ['subnetLabels'], chipKey: 'subnetLabels',
    label: 'Subnet labels', category: 'network', keywords: ['label', 'zone', 'dmz'],
    help: 'Hosts in any subnet carrying a selected label. A separate vocabulary from host tags.',
    options: (d) => counted(d?.subnet_labels, (l) => ({ value: String(l.id), label: l.name, count: l.host_count })),
    cap: 200, queryHint: 'label:dmz',
    noData: 'No subnet labels yet — label a subnet in Scopes.',
  },

  // ── Services & web evidence ────────────────────────────────────────────
  {
    kind: 'endpoint', id: 'endpoint', keys: ['ports', 'services', 'portStates', 'hasOpenPorts'], chipKey: 'endpoint',
    label: 'Port / service', category: 'services', common: true,
    keywords: ['port', 'service', 'endpoint', 'ssh', 'http', 'https', 'smb', 'rdp', 'open', 'database', 'windows'],
    help: 'A recorded port matching the port, service and state you choose — all on the SAME port.',
  },
  {
    kind: 'choice', id: 'noOpenPorts', key: 'hasOpenPorts', keys: ['hasOpenPorts'], chipKey: 'hasOpenPorts',
    label: 'No recorded open ports', category: 'services', keywords: ['closed', 'filtered', 'dark', 'silent'],
    help: 'Hosts with no open port on record. Absence of a recorded port is not proof nothing listens.',
    choices: [
      { value: undefined, label: 'Any' },
      { value: false, label: 'No recorded open ports' },
    ],
  },
  {
    kind: 'choice', id: 'hasWebInterface', key: 'hasWebInterface', keys: ['hasWebInterface'], chipKey: 'hasWebInterface',
    label: 'Web interface evidence', category: 'services', keywords: ['web', 'http', 'httpx', 'eyewitness', 'screenshot'],
    help: 'From web-detection imports (httpx, EyeWitness, WhatWeb).',
    choices: [
      { value: undefined, label: 'Any' },
      { value: true, label: 'Recorded' },
      { value: false, label: 'Not recorded', help: 'No import ever saw one — not proof there is no web service.' },
    ],
  },
  {
    kind: 'multi', id: 'tech', key: 'tech', keys: ['tech'], chipKey: 'tech',
    label: 'Web technologies', category: 'services', keywords: ['nginx', 'apache', 'jenkins', 'stack', 'framework', 'cms'],
    help: 'Hosts whose web interface was fingerprinted with any selected technology — httpx, WhatWeb.',
    options: (d) => counted(d?.technologies, (t) => ({ value: t.name, label: t.name, count: t.host_count })),
    cap: 200, queryHint: 'tech:nginx',
    noData: 'No technologies yet — run httpx or WhatWeb and upload the output.',
  },

  // ── Scanner observations ───────────────────────────────────────────────
  {
    kind: 'severity', id: 'severity', chipKey: 'severity',
    keys: ['hasCriticalVulns', 'hasHighVulns', 'hasMediumVulns', 'hasLowVulns'],
    label: 'Scanner severity', category: 'observations', common: true,
    keywords: ['critical', 'high', 'medium', 'low', 'vulnerability', 'vuln', 'nessus', 'openvas'],
    help: 'Imported vulnerability observations (Nessus, OpenVAS, Nikto) — match ANY selected severity. Scanner severity is not an analyst-confirmed finding.',
  },
  {
    kind: 'toggle', id: 'hasExploitAvailable', key: 'hasExploitAvailable', keys: ['hasExploitAvailable'], chipKey: 'hasExploitAvailable',
    label: 'Exploit reported', category: 'observations', keywords: ['exploit', 'metasploit', 'poc', 'exploitable'],
    help: 'A scanner observation reports that an exploit exists (Nessus only). Not proof this host was exploited. For CVE or plugin text use the query bar (cve:…, vuln:…).',
  },

  // ── Analyst work ───────────────────────────────────────────────────────
  {
    kind: 'choice', id: 'followFilter', key: 'followFilter', keys: ['followFilter'], chipKey: 'followFilter',
    label: 'Team review', category: 'work', common: true, keywords: ['review', 'reviewed', 'unreviewed', 'watching', 'status'],
    help: 'The team\'s review state — shared, not yours alone.',
    choices: [
      { value: undefined, label: 'Any' },
      { value: 'none', label: 'Not started', help: 'Nobody has taken it In review or marked it Reviewed.' },
      { value: 'watching', label: 'Watching' },
      { value: 'in_review', label: 'In review' },
      { value: 'reviewed', label: 'Reviewed' },
    ],
  },
  {
    kind: 'toggle', id: 'assignedToMe', key: 'assignedToMe', keys: ['assignedToMe'], chipKey: 'assignedToMe',
    label: 'Assigned to me', category: 'work', common: true, keywords: ['mine', 'assignee', 'analyst', 'queue'],
    help: 'Hosts explicitly assigned to you, or that you took In review / Reviewed. In a shared link, "me" is whoever opens it.',
  },
  {
    kind: 'toggle', id: 'onlyWithNotes', key: 'onlyWithNotes', keys: ['onlyWithNotes'], chipKey: 'onlyWithNotes',
    label: 'Has notes', category: 'work', keywords: ['note', 'comment'],
    help: 'Hosts with at least one analyst note.',
  },
  {
    kind: 'toggle', id: 'hasTestExecution', key: 'hasTestExecution', keys: ['hasTestExecution'], chipKey: 'hasTestExecution',
    label: 'Tested by agent', category: 'work', keywords: ['test', 'plan', 'executed', 'agent'],
    help: 'An agentic test plan was actually executed against the host (not merely drafted). "Approved but never run" is the built-in view Planned, not tested.',
  },

  // ── Discovery & attribution ────────────────────────────────────────────
  {
    kind: 'single', id: 'osFilter', key: 'osFilter', keys: ['osFilter'], chipKey: 'osFilter',
    label: 'Operating system', category: 'discovery', keywords: ['os', 'windows', 'linux'],
    help: 'The OS a scan detected — nmap -O, NetExec, Nessus. A detection, with the confidence the scan gave it.',
    options: (d) => counted(d?.operating_systems, (o) => ({ value: o.name, label: o.name, count: o.count })),
    cap: 100, queryHint: 'os:"Windows Server 2019"',
    noData: 'No OS data yet — run OS detection (nmap -O) and upload it.',
  },
  {
    kind: 'scans', id: 'scans', keys: ['scanIds', 'firstSeenInSelectedScans'], chipKey: 'scanIds',
    label: 'Seen in scans', category: 'discovery', keywords: ['scan', 'file', 'upload', 'import', 'first', 'new', 'discovered'],
    help: 'Hosts observed in any selected scan — or, optionally, FIRST discovered in them.',
  },
  {
    kind: 'multi', id: 'orgs', key: 'orgs', keys: ['orgs'], chipKey: 'orgs',
    label: 'Registered network owner', category: 'discovery', keywords: ['org', 'rdap', 'whois', 'owner'],
    help: 'Who the address block is REGISTERED to (RDAP) — not who runs the host.',
    options: (d) => counted(d?.orgs, (o) => ({ value: o.name, label: o.name, count: o.host_count })),
    cap: 200, queryHint: 'org:"Acme"',
    noData: 'No RDAP data yet — upload RDAP output to attribute addresses.',
  },
  {
    kind: 'multi', id: 'asns', key: 'asns', keys: ['asns'], chipKey: 'asns',
    label: 'ASN', category: 'discovery', keywords: ['as', 'autonomous', 'rdap'],
    help: 'The autonomous system announcing the address (RDAP).',
    options: (d) => counted(d?.asns, (a) => ({
      value: String(a.asn),
      label: a.as_name ? `AS${a.asn} · ${a.as_name}` : `AS${a.asn}`,
      count: a.host_count,
      keywords: a.as_name ? [a.as_name] : undefined,
    })),
    cap: 200, queryHint: 'asn:13335',
    noData: 'No RDAP data yet — upload RDAP output to attribute addresses.',
  },
  {
    kind: 'multi', id: 'countries', key: 'countries', keys: ['countries'], chipKey: 'countries',
    label: 'Registered country', category: 'discovery', keywords: ['country', 'geo', 'rdap'],
    help: 'The country of REGISTRATION (RDAP) — not the host\'s physical location.',
    options: (d) => counted(d?.countries, (c) => ({ value: c.country, label: c.country, count: c.host_count })),
    cap: 200, queryHint: 'country:US',
    noData: 'No RDAP data yet — upload RDAP output to attribute addresses.',
  },
];

export const fieldById = (id: string): HostFilterField | undefined =>
  HOST_FILTER_FIELDS.find((f) => f.id === id);

/** The editor an applied condition's chip opens; none for the query / legacy
 *  search / host-state chips, which have no structured editor. */
export const fieldForChip = (chipKey: string, filters: HostFilterOptions): HostFilterField | undefined => {
  // `hasOpenPorts` is two conditions: true is part of the endpoint, false its own.
  if (chipKey === 'hasOpenPorts') return fieldById(filters.hasOpenPorts === false ? 'noOpenPorts' : 'endpoint');
  return HOST_FILTER_FIELDS.find((f) => f.chipKey === chipKey);
};

/** Whether a field currently constrains the result. */
export const fieldIsApplied = (field: HostFilterField, filters: HostFilterOptions): boolean => {
  if (field.id === 'noOpenPorts') return filters.hasOpenPorts === false;
  if (field.id === 'endpoint') {
    return Boolean(filters.ports?.length || filters.services?.length || filters.portStates?.length)
      || filters.hasOpenPorts === true;
  }
  return field.keys.some((k) => {
    const v = filters[k];
    if (Array.isArray(v)) return v.length > 0;
    // The one boolean whose `false` is itself a condition ("not recorded").
    if (k === 'hasWebInterface') return v !== undefined;
    return v !== undefined && v !== false;
  });
};

/** Catalog search: label, keywords, category and help text. */
export const searchFields = (query: string): HostFilterField[] => {
  const q = query.trim().toLowerCase();
  if (!q) return HOST_FILTER_FIELDS;
  const categoryLabel = (f: HostFilterField) =>
    FILTER_CATEGORIES.find((c) => c.id === f.category)?.label.toLowerCase() ?? '';
  const score = (f: HostFilterField): number => {
    if (f.label.toLowerCase().startsWith(q)) return 0;
    if (f.label.toLowerCase().includes(q)) return 1;
    if (f.keywords?.some((k) => k.startsWith(q) || q.startsWith(k))) return 2;
    if (categoryLabel(f).includes(q) || f.help.toLowerCase().includes(q)) return 3;
    return -1;
  };
  return HOST_FILTER_FIELDS
    .map((f) => ({ f, s: score(f) }))
    .filter((x) => x.s >= 0)
    .sort((a, b) => a.s - b.s)
    .map((x) => x.f);
};
