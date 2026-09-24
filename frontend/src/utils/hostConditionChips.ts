import type { HostFilterOptions } from '../components/HostFilters';

/**
 * The applied filters as the toolbar's "Matching all of:" strip — ONE chip per
 * condition, never one per value.
 *
 * The chips are read as ANDed.  A chip per value made `Service: http` next to
 * `Service: https` say "http AND https" when the filter means "http OR https",
 * and made three severities look like three requirements.  Values inside one
 * condition are alternatives, so they share a chip and are joined with "or".
 *
 * Port / service / port state (and "has open ports") are ONE condition: the
 * backend matches them against the same port row (`port_match_subquery`), so
 * they are shown — and removed — together as an Endpoint.
 */

export interface HostConditionChip {
  key: string;
  label: string;
  /** Every value, for the tooltip, when the label abbreviates them. */
  title?: string;
  /** Filter keys this condition owns; removing the chip deletes exactly these. */
  clearKeys: Array<keyof HostFilterOptions>;
}

export interface ChipNameLookups {
  scan?: (id: string) => string | undefined;
  tag?: (id: string) => string | undefined;
  subnetLabel?: (id: string) => string | undefined;
  asn?: (asn: string) => string | undefined;
  followStatus?: (value: string) => string | undefined;
}

// Values named in the chip before "+N"; the tooltip always carries them all.
const MAX_VALUES_SHOWN = 3;

const titleCase = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);

const anyOf = (values: string[]): { text: string; full: string } => {
  const full = values.join(' or ');
  if (values.length <= MAX_VALUES_SHOWN) return { text: full, full };
  return {
    text: `${values.slice(0, MAX_VALUES_SHOWN).join(' or ')} +${values.length - MAX_VALUES_SHOWN}`,
    full,
  };
};

export function hostConditionChips(
  filters: HostFilterOptions,
  names: ChipNameLookups = {},
): HostConditionChip[] {
  const chips: HostConditionChip[] = [];

  const list = (
    key: keyof HostFilterOptions,
    label: string,
    display: (value: string) => string = (v) => v,
  ) => {
    const values = (filters[key] as string[] | undefined) ?? [];
    if (values.length === 0) return;
    const { text, full } = anyOf(values.map(display));
    chips.push({
      key: String(key),
      label: `${label}: ${text}`,
      title: text === full ? undefined : `${label}: ${full}`,
      clearKeys: [key],
    });
  };
  const flag = (key: keyof HostFilterOptions, label: string) => {
    if (filters[key]) chips.push({ key: String(key), label, clearKeys: [key] });
  };

  // The query narrows results like any other condition, so it is part of the
  // model — a query-only view is not "the full inventory".
  if (filters.query?.trim()) {
    chips.push({ key: 'query', label: `Query: ${filters.query.trim()}`, clearKeys: ['query'] });
  }
  // Only ever arrives from an old link or saved view; named for what it is so
  // it is never an invisible constraint.
  if (filters.search) {
    chips.push({ key: 'search', label: `Text search: ${filters.search}`, clearKeys: ['search'] });
  }
  if (filters.state) {
    chips.push({ key: 'state', label: `Last observed state: ${titleCase(filters.state)}`, clearKeys: ['state'] });
  }

  // Endpoint — every part must hold for the SAME recorded port.
  const endpointParts: string[] = [];
  const endpointKeys: Array<keyof HostFilterOptions> = [];
  if (filters.ports?.length) {
    endpointParts.push(`port ${filters.ports.join(' or ')}`);
    endpointKeys.push('ports');
  }
  if (filters.services?.length) {
    endpointParts.push(`service ${filters.services.join(' or ')}`);
    endpointKeys.push('services');
  }
  const hasEndpoint = endpointParts.length > 0;
  if (filters.portStates?.length) {
    endpointParts.push(filters.portStates.includes('any') ? 'any state' : `state ${filters.portStates.join(' or ')}`);
    endpointKeys.push('portStates');
  }
  if (filters.hasOpenPorts === true) {
    // Alone it is simply "has an open port"; beside the others it requires the
    // matched port to be open.
    if (hasEndpoint && !filters.portStates?.includes('open')) endpointParts.push('open');
    endpointKeys.push('hasOpenPorts');
  } else if (hasEndpoint && !filters.portStates?.length && filters.hasOpenPorts !== false) {
    // v5.289.0 — open is the default for a port / service condition; say so.
    // (Beside "no recorded open ports" the backend ignores the port filters.)
    endpointParts.push('open');
  }
  if (endpointKeys.length > 0) {
    const full = endpointParts.length > 0 ? `Endpoint: ${endpointParts.join(' · ')}` : 'Has open ports';
    chips.push({
      key: 'endpoint',
      label: full,
      title: endpointParts.length > 1 ? `${full} — all on the same recorded port` : undefined,
      clearKeys: endpointKeys,
    });
  }
  // `false` is a standalone exclusion the backend applies on its own.
  if (filters.hasOpenPorts === false) {
    chips.push({ key: 'hasOpenPorts', label: 'No recorded open ports', clearKeys: ['hasOpenPorts'] });
  }

  if (filters.osFilter) chips.push({ key: 'osFilter', label: `OS: ${filters.osFilter}`, clearKeys: ['osFilter'] });
  if (filters.hasWebInterface !== undefined) {
    chips.push({
      key: 'hasWebInterface',
      label: filters.hasWebInterface ? 'Web interface recorded' : 'No web interface recorded',
      clearKeys: ['hasWebInterface'],
    });
  }
  list('tech', 'Web technology');
  list('subnets', 'Subnet');
  list('sites', 'Site');
  list('tags', 'Tag', (id) => names.tag?.(id) ?? id);
  list('subnetLabels', 'Subnet label', (id) => names.subnetLabel?.(id) ?? id);

  // Scans, with the "first discovered" modifier that only means something
  // alongside them.
  if (filters.scanIds?.length) {
    const { text, full } = anyOf(filters.scanIds.map((id) => names.scan?.(id) ?? `Scan #${id}`));
    const verb = filters.firstSeenInSelectedScans ? 'First discovered in' : 'Seen in';
    chips.push({
      key: 'scanIds',
      label: `${verb}: ${text}`,
      title: text === full ? undefined : `${verb}: ${full}`,
      clearKeys: ['scanIds', 'firstSeenInSelectedScans'],
    });
  }

  // Registered attribution (RDAP) — who the address is registered to, not who
  // runs the host or where it is.
  list('orgs', 'Registered owner');
  list('asns', 'ASN', (asn) => {
    const name = names.asn?.(asn);
    return name ? `AS${asn} (${name})` : `AS${asn}`;
  });
  list('countries', 'Registered country');

  // Severities are ORed by the backend — one condition.
  const severities = [
    filters.hasCriticalVulns && 'Critical',
    filters.hasHighVulns && 'High',
    filters.hasMediumVulns && 'Medium',
    filters.hasLowVulns && 'Low',
  ].filter(Boolean) as string[];
  if (severities.length > 0) {
    chips.push({
      key: 'severity',
      label: `Scanner severity: ${severities.join(' or ')}`,
      clearKeys: ['hasCriticalVulns', 'hasHighVulns', 'hasMediumVulns', 'hasLowVulns'],
    });
  }
  flag('hasExploitAvailable', 'Exploit reported');
  flag('hasTestExecution', 'Tested by agent');
  flag('outOfScopeOnly', 'Out of scope');

  if (filters.followFilter) {
    const label = filters.followFilter === 'none'
      ? 'Not started'
      : names.followStatus?.(filters.followFilter) ?? titleCase(filters.followFilter);
    chips.push({ key: 'followFilter', label: `Review: ${label}`, clearKeys: ['followFilter'] });
  }
  flag('onlyWithNotes', 'Has notes');
  flag('assignedToMe', 'Assigned to me');

  return chips;
}
