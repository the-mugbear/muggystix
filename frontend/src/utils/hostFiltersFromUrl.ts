import type { HostFilterOptions } from '../components/HostFilters';
import type { FollowStatus } from '../services/api/shared';

/**
 * The Hosts page's restore step, as a pure function: URL parameters (and, on a
 * bare /hosts visit, the session's saved state) → the filters to start with.
 *
 * A shared link must reproduce the SENDER's result set, not blend with the
 * recipient's previous session filters.  So if the URL carries any recognised
 * host parameter it is authoritative and the saved session is ignored entirely.
 */

export type HostSortOption =
  | 'critical_desc'
  | 'exploitable_desc'
  | 'open_ports_desc'
  | 'notes_desc'
  | 'discoveries_desc'
  | 'ip_asc'
  | 'hostname_asc';

/** The session blob, in the legacy 3-key shape older frontends also wrote. */
export interface SavedHostFilterState {
  filters?: HostFilterOptions;
  followFilter?: 'all' | 'none' | FollowStatus;
  onlyWithNotes?: boolean;
}

export const HOST_URL_PARAMS = [
  'search', 'q', 'state', 'os_filter', 'subnets', 'ports', 'services',
  'port_states', 'scan_ids', 'tags', 'subnet_labels', 'sites', 'out_of_scope_only',
  'out_of_scope', 'has_open_ports', 'first_seen_in_scan', 'has_critical_vulns',
  'has_high_vulns', 'has_medium_vulns', 'has_low_vulns',
  'has_exploit_available', 'has_test_execution',
  'has_web_interface', 'tech', 'follow_status', 'follow',
  'with_notes_only', 'with_notes', 'assigned_to', 'sort_by', 'sort_order',
  'orgs', 'asns', 'countries',
];

export const urlCarriesHostFilters = (urlParams: URLSearchParams): boolean =>
  HOST_URL_PARAMS.some((p) => urlParams.has(p));

const STRING_PARAMS: Array<[string, keyof HostFilterOptions]> = [
  ['search', 'search'],
  ['q', 'query'],
  ['state', 'state'],
  ['os_filter', 'osFilter'],
];

// Comma-separated lists.
const LIST_PARAMS: Array<[string, keyof HostFilterOptions]> = [
  ['subnets', 'subnets'],
  ['ports', 'ports'],
  ['services', 'services'],
  ['port_states', 'portStates'],
  ['scan_ids', 'scanIds'],
  ['tags', 'tags'],
  ['subnet_labels', 'subnetLabels'],
  ['sites', 'sites'],
  ['tech', 'tech'],
];

// Repeated params (?orgs=A&orgs=B) — read every value, never comma-split: the
// values themselves (org names) contain commas.
const REPEATED_PARAMS: Array<[string, keyof HostFilterOptions]> = [
  ['orgs', 'orgs'],
  ['asns', 'asns'],
  ['countries', 'countries'],
];

// `false` is meaningful and kept (has_web_interface=false is "no recorded web
// interface"), so a negative filter from an old link survives the restore.
const BOOLEAN_PARAMS: Array<[string[], keyof HostFilterOptions]> = [
  [['out_of_scope_only', 'out_of_scope'], 'outOfScopeOnly'],
  [['has_open_ports'], 'hasOpenPorts'],
  [['first_seen_in_scan'], 'firstSeenInSelectedScans'],
  [['has_critical_vulns'], 'hasCriticalVulns'],
  [['has_high_vulns'], 'hasHighVulns'],
  [['has_medium_vulns'], 'hasMediumVulns'],
  [['has_low_vulns'], 'hasLowVulns'],
  [['has_exploit_available'], 'hasExploitAvailable'],
  [['has_test_execution'], 'hasTestExecution'],
  [['has_web_interface'], 'hasWebInterface'],
];

// Reverse of the API-key map in buildHostQueryContext; direction is encoded in
// the HostSortOption itself, so sort_by alone is enough.
const SORT_FROM_PARAM: Record<string, HostSortOption> = {
  critical_vulns: 'critical_desc',
  exploitable_vulns: 'exploitable_desc',
  open_ports: 'open_ports_desc',
  note_count: 'notes_desc',
  discovery_count: 'discoveries_desc',
  ip_address: 'ip_asc',
  hostname: 'hostname_asc',
};

export function hostFiltersFromUrl(
  urlParams: URLSearchParams,
  savedState: SavedHostFilterState | null,
): {
  filters: HostFilterOptions;
  sortBy: HostSortOption | null;
  /**
   * v5.290.0 — the filters came from the saved session, not from the URL: a
   * bare /hosts visit (a nav link) that opens on a filtered list.  The page
   * says so ("Restored your last filters · Clear") until they are changed.
   */
  restoredFromSession: boolean;
} {
  const saved = urlCarriesHostFilters(urlParams) ? null : savedState;
  const filters: HostFilterOptions = saved?.filters ? { ...saved.filters } : {};
  const set = (key: keyof HostFilterOptions, value: unknown) => {
    (filters as Record<string, unknown>)[key] = value;
  };
  const unset = (key: keyof HostFilterOptions) => {
    delete (filters as Record<string, unknown>)[key];
  };

  for (const [param, key] of STRING_PARAMS) {
    if (!urlParams.has(param)) continue;
    const value = urlParams.get(param);
    if (value) set(key, value);
    else unset(key);
  }
  for (const [param, key] of LIST_PARAMS) {
    if (!urlParams.has(param)) continue;
    const values = (urlParams.get(param) || '').split(',').map((v) => v.trim()).filter(Boolean);
    if (values.length) set(key, values);
    else unset(key);
  }
  for (const [param, key] of REPEATED_PARAMS) {
    if (!urlParams.has(param)) continue;
    const values = urlParams.getAll(param).map((v) => v.trim()).filter(Boolean);
    if (values.length) set(key, values);
    else unset(key);
  }
  for (const [params, key] of BOOLEAN_PARAMS) {
    const param = params.find((p) => urlParams.has(p));
    if (param) set(key, urlParams.get(param) === 'true');
  }

  // v4.51.0 — followFilter + onlyWithNotes live inside `filters`; blobs saved
  // before that stored them as top-level keys, and both shapes are honoured.
  const followParam = urlParams.get('follow_status') ?? urlParams.get('follow');
  if (followParam && ['watching', 'in_review', 'reviewed', 'none'].includes(followParam)) {
    filters.followFilter = followParam as 'none' | FollowStatus;
  } else if (saved?.followFilter && saved.followFilter !== 'all') {
    filters.followFilter = saved.followFilter;
  }

  const notesParam = urlParams.get('with_notes_only') ?? urlParams.get('with_notes');
  if (notesParam === 'true') filters.onlyWithNotes = true;
  else if (notesParam === 'false') delete filters.onlyWithNotes;
  else if (saved?.onlyWithNotes === true) filters.onlyWithNotes = true;

  if (urlParams.get('assigned_to') === 'me') filters.assignedToMe = true;

  return {
    filters,
    sortBy: SORT_FROM_PARAM[urlParams.get('sort_by') ?? ''] ?? null,
    // With a saved session in play the URL carried no host parameter, so every
    // filter present came from the session.
    restoredFromSession: saved !== null && Object.keys(filters).length > 0,
  };
}
