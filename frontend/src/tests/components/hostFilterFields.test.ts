import { describe, it, expect } from 'vitest';
import {
  HOST_FILTER_FIELDS,
  FILTER_CATEGORIES,
  fieldForChip,
  fieldIsApplied,
  fieldById,
  portOptions,
  searchFields,
  searchValues,
} from '../../components/hosts/hostFilterFields';
import { hostConditionChips } from '../../utils/hostConditionChips';
import type { HostFilterOptions } from '../../components/HostFilters';

// Every structured filter the page can hold.  `query`, `search` and `state`
// have no editor on purpose: the query bar owns the first, the other two only
// arrive from old links and are removable from their chip.
const EVERY_FILTER: HostFilterOptions = {
  ports: ['22'], services: ['ssh'], portStates: ['open'], hasOpenPorts: true,
  osFilter: 'Linux', subnets: ['10.0.0.0/24'], sites: ['East'], tags: ['1'], subnetLabels: ['2'],
  hasCriticalVulns: true, hasHighVulns: true, hasMediumVulns: true, hasLowVulns: true,
  hasExploitAvailable: true, hasTestExecution: true, outOfScopeOnly: true,
  scanIds: ['9'], firstSeenInSelectedScans: true, hasWebInterface: false, tech: ['nginx'],
  orgs: ['Acme'], asns: ['13335'], countries: ['US'], assignedToMe: true,
  followFilter: 'none', onlyWithNotes: true, weaknesses: ['smb_unsigned'], checks: ['smbv1_enabled'],
};

describe('host filter field registry', () => {
  it('reaches every structured filter key from some field — none is only settable by URL', () => {
    const owned = new Set(HOST_FILTER_FIELDS.flatMap((f) => f.keys));
    for (const key of Object.keys(EVERY_FILTER)) expect(owned, key).toContain(key);
  });

  it('gives every applied condition\'s chip an editor', () => {
    for (const chip of hostConditionChips(EVERY_FILTER)) {
      expect(fieldForChip(chip.key, EVERY_FILTER), chip.key).toBeDefined();
    }
    // …except the three with no structured editor.
    for (const chip of hostConditionChips({ query: 'port:1', search: 'x', state: 'up' })) {
      expect(fieldForChip(chip.key, {})).toBeUndefined();
    }
  });

  it('routes hasOpenPorts by its value: true is the endpoint, false its own condition', () => {
    expect(fieldForChip('hasOpenPorts', { hasOpenPorts: false })?.id).toBe('noOpenPorts');
    expect(fieldIsApplied(fieldById('endpoint')!, { hasOpenPorts: false })).toBe(false);
    expect(fieldIsApplied(fieldById('noOpenPorts')!, { hasOpenPorts: false })).toBe(true);
    expect(fieldIsApplied(fieldById('endpoint')!, { hasOpenPorts: true })).toBe(true);
  });

  it('treats "web interface: not recorded" as applied, and an unset toggle as not', () => {
    expect(fieldIsApplied(fieldById('hasWebInterface')!, { hasWebInterface: false })).toBe(true);
    expect(fieldIsApplied(fieldById('assignedToMe')!, {})).toBe(false);
  });

  it('has unique ids, a known category for every field, and a short Common list', () => {
    const ids = HOST_FILTER_FIELDS.map((f) => f.id);
    expect(new Set(ids).size).toBe(ids.length);
    const categories = FILTER_CATEGORIES.map((c) => c.id);
    HOST_FILTER_FIELDS.forEach((f) => expect(categories).toContain(f.category));
    // 5.303.0 — weaknesses joined Common; Team review left it (the toolbar's
    // Review menu is the everyday control for it).
    expect(HOST_FILTER_FIELDS.filter((f) => f.common).map((f) => f.id))
      .toEqual(['subnets', 'endpoint', 'weaknesses', 'severity', 'assignedToMe']);
  });

  // UX review 2026-09-25 — "smb" found only Port / service; the weakness
  // filters existed only as query syntax.
  it('finds weakness VALUES by the word an analyst types', () => {
    const data = {
      weaknesses: [
        { name: 'smb_unsigned', label: 'SMB signing not required', description: 'relay', host_count: 3 },
        { name: 'weak_auth', label: 'Guest / anonymous / null login worked', description: 'NetExec, SMBMap', host_count: 1 },
      ],
      checks: [{ id: 'smbv1_enabled', title: 'SMBv1 enabled', host_count: 2 }],
    } as never;
    const hits = searchValues('smb', data).map((h) => `${h.field.id}:${h.option.value}`);
    expect(hits).toEqual(['weaknesses:smb_unsigned', 'checks:smbv1_enabled']);
    expect(searchFields('smb').map((f) => f.id)).toContain('weaknesses');
    expect(searchValues('s', data)).toEqual([]);
  });

  it('offers query-only conditions (CVE) as catalog rows that start the query', () => {
    const cve = HOST_FILTER_FIELDS.find((f) => f.id === 'cve');
    expect(cve?.kind).toBe('query');
    expect(searchFields('cve')[0].id).toBe('cve');
  });

  it('finds a field by the word an analyst would type, best match first', () => {
    expect(searchFields('port')[0].id).toBe('endpoint');
    expect(searchFields('unreviewed').map((f) => f.id)).toContain('followFilter');
    expect(searchFields('asn')[0].id).toBe('asns');
    expect(searchFields('exploit')[0].id).toBe('hasExploitAvailable');
    expect(searchFields('zzzz')).toEqual([]);
    expect(searchFields('  ')).toHaveLength(HOST_FILTER_FIELDS.length);
  });

  it('lists each port once, most common first', () => {
    expect(portOptions({
      common_ports: [
        { port: 443, service: 'https', state: 'open', count: 5 },
        { port: 22, service: 'ssh', state: 'open', count: 9 },
        { port: 443, service: 'ssl/http', state: 'open', count: 2 },
      ],
      services: [], operating_systems: [], subnets: [],
    }).map((o) => [o.value, o.label, o.count])).toEqual([['22', '22 (ssh)', 9], ['443', '443 (https)', 5]]);
  });
});
