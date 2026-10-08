/**
 * Review 2026-10-01 R15, kept when the HTML host report was retired: a
 * narrowed download must never read as the whole inventory.  The rule was
 * pinned on the report's "Applied Filters" line (backend
 * `test_report_review_2026_10_01.py`); it now lives on the dialog that offers
 * the downloads.
 */
import { describe, expect, it } from 'vitest';
import { describeInventoryFilters } from '../../utils/inventoryFilters';

describe('describeInventoryFilters', () => {
  it('says nothing when nothing narrows the list', () => {
    expect(describeInventoryFilters({})).toEqual([]);
    expect(describeInventoryFilters({ state: undefined, q: '', orgs: [], search: null })).toEqual([]);
  });

  it('does not call paging or sort order a filter', () => {
    expect(describeInventoryFilters({
      sort_by: 'ip_address', sort_order: 'asc', skip: 50, limit: 25, include_total: true,
    })).toEqual([]);
  });

  it('names every filter the Hosts page sends', () => {
    expect(describeInventoryFilters({
      search: 'db', q: 'port:445 AND NOT tag:"x"', state: 'up', sites: 'London DC', subnets: '10.0.0.0/24',
      subnet_labels: '2', tags: '3,7', ports: '80,443', services: 'http', port_states: 'open',
      has_open_ports: false, os_filter: 'Windows', tech: 'nginx', has_exploit_available: true,
      weaknesses: 'smb_unsigned,weak_tls', checks: 'smb_signing_off', has_test_execution: true,
      has_web_interface: false, follow_status: 'none', assigned_to: 'me', out_of_scope_only: true,
      scan_ids: '4', first_seen_in_scan: true, with_notes_only: true,
      orgs: ['Google, LLC', 'Example'], asns: ['AS15169'], countries: ['US'],
    })).toEqual([
      'Search: "db"', 'Query: port:445 AND NOT tag:"x"', 'State: up', 'Site: London DC',
      'Subnets: 10.0.0.0/24', 'Subnet labels: 2', 'Tags: 3,7', 'Ports: 80,443', 'Services: http',
      'Port states: open', 'Has open ports: No', 'OS: Windows', 'Tech: nginx', 'Exploit reported',
      'Weakness: smb unsigned or weak tls', 'Check: smb signing off', 'Tested',
      'Web interface: not recorded', 'Review: not started', 'Assigned: me', 'Out of scope',
      'Scan IDs: 4', 'First seen in selected scans', 'With notes only',
      'Organisation: Google, LLC, Example', 'ASN: AS15169', 'Country: US',
    ]);
  });

  // The backend ORs the severity switches; lines are read as ANDed.
  it('puts the scanner severities on one line, as alternatives', () => {
    expect(describeInventoryFilters({ has_critical_vulns: true, has_low_vulns: true, has_high_vulns: false }))
      .toEqual(['Scanner severity: Critical or Low']);
  });

  it('says a flag that is set to false', () => {
    expect(describeInventoryFilters({ has_exploit_available: false })).toEqual(['Exploit reported: No']);
  });

  // A new Hosts filter can never go unreported.
  it('still names a filter nobody worded', () => {
    expect(describeInventoryFilters({ brand_new_filter: 'v', another_flag: true, third: ['a', 'b'], off_flag: false }))
      .toEqual(['Brand new filter: v', 'Another flag', 'Third: a, b', 'Off flag: No']);
  });
});
