/**
 * What narrowed an inventory download, in words — the "Active filters" list of
 * the Hosts page's "Download inventory" dialog.
 *
 * The rule (review 2026-10-01 R15, first written for the retired HTML host
 * report's "Applied Filters" line): a narrowed download must never read as the
 * whole inventory.  Every filter that is set is named; one this module has no
 * wording for is still named, under its own key — a new Hosts filter can never
 * go unreported.  Client-free.
 */

export type InventoryFilterValue = string | number | boolean | string[] | undefined | null;

const isSet = (value: InventoryFilterValue): boolean =>
  !(value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0));

const text = (value: InventoryFilterValue): string =>
  Array.isArray(value) ? value.join(', ') : String(value);

/** `a_b,c_d` → "a b or c d": the values of one filter are alternatives. */
const alternatives = (value: InventoryFilterValue): string =>
  text(value).split(',').map((v) => v.trim().replace(/_/g, ' ')).filter(Boolean).join(' or ');

const yesNo = (label: string) => (value: InventoryFilterValue) => `${label}: ${value ? 'Yes' : 'No'}`;
const labelled = (label: string) => (value: InventoryFilterValue) => `${label}: ${text(value)}`;
/** A flag that only narrows when true; set to false it says so. */
const flag = (label: string) => (value: InventoryFilterValue) => (value ? label : `${label}: No`);

/** One wording per Hosts filter, in the order the lines are listed. */
const WORDING: Array<[key: string, say: (value: InventoryFilterValue) => string]> = [
  ['search', (v) => `Search: "${text(v)}"`],
  ['q', labelled('Query')],
  ['state', labelled('State')],
  ['sites', labelled('Site')],
  ['subnets', labelled('Subnets')],
  ['subnet_labels', labelled('Subnet labels')],
  ['tags', labelled('Tags')],
  ['ports', labelled('Ports')],
  ['services', labelled('Services')],
  ['port_states', labelled('Port states')],
  ['has_open_ports', yesNo('Has open ports')],
  ['os_filter', labelled('OS')],
  ['tech', labelled('Tech')],
  ['has_exploit_available', flag('Exploit reported')],
  ['weaknesses', (v) => `Weakness: ${alternatives(v)}`],
  ['checks', (v) => `Check: ${alternatives(v)}`],
  ['has_test_execution', flag('Tested')],
  ['has_web_interface', (v) => `Web interface: ${v ? 'recorded' : 'not recorded'}`],
  ['follow_status', (v) => `Review: ${v === 'none' ? 'not started' : text(v)}`],
  ['assigned_to', labelled('Assigned')],
  ['out_of_scope_only', flag('Out of scope')],
  ['scan_ids', labelled('Scan IDs')],
  ['first_seen_in_scan', flag('First seen in selected scans')],
  ['with_notes_only', flag('With notes only')],
  ['orgs', labelled('Organisation')],
  ['asns', labelled('ASN')],
  ['countries', labelled('Country')],
];

/** The four scanner-severity switches are ONE line: the backend ORs them, and
 *  these lines are read as ANDed — the same rule as the Hosts chips. */
const SEVERITIES: Array<[key: string, label: string]> = [
  ['has_critical_vulns', 'Critical'], ['has_high_vulns', 'High'],
  ['has_medium_vulns', 'Medium'], ['has_low_vulns', 'Low'],
];

/** Not filters: how the LIST is paged and ordered, which a download ignores. */
const NOT_FILTERS = new Set(['skip', 'limit', 'include_total', 'sort_by', 'sort_order']);

/** One line per filter that narrows the download; empty when none does. */
export function describeInventoryFilters(
  filters: Record<string, InventoryFilterValue>,
): string[] {
  const out: string[] = [];
  const said = new Set<string>(NOT_FILTERS);
  for (const [key, say] of WORDING) {
    said.add(key);
    if (isSet(filters[key])) out.push(say(filters[key]));
    if (key === 'tech') {
      // Severity sits after the service / technology lines, as on the page.
      const severities = SEVERITIES.filter(([k]) => filters[k]).map(([, label]) => label);
      if (severities.length) out.push(`Scanner severity: ${severities.join(' or ')}`);
      SEVERITIES.forEach(([k]) => said.add(k));
    }
  }
  // A filter nobody worded is still named.
  for (const [key, value] of Object.entries(filters)) {
    if (said.has(key) || !isSet(value)) continue;
    const label = key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
    out.push(value === true ? label : `${label}: ${value === false ? 'No' : text(value)}`);
  }
  return out;
}
