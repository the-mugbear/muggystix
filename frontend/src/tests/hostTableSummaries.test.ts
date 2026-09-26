/**
 * The Hosts-table summaries must not claim more than the evidence supports
 * (design review 2026-09-18, item 1).  Three compact labels were doing so:
 *
 *  - "out of scope" for every host without a subnet mapping, including one
 *    an approved name resolves to and every host on a project with no scope;
 *  - the Exposure chips from port numbers alone, with "no high-value
 *    services" hiding an admin service probed on an unusual port;
 *  - "N critical · exploit" from a critical count and a host-wide exploit
 *    count that were never joined on the same vulnerability.
 */
import { describe, expect, it } from 'vitest';

import { computeAttention, scopeCoverageText } from '../components/hosts/useHostColumns';
import type { Host } from '../services/api/hosts';
import { exposureChips } from '../utils/portsOfInterest';

const host = (over: Partial<Host>): Host =>
  ({ id: 1, ip_address: '10.0.0.1', ...over }) as Host;

describe('scope coverage label', () => {
  it('a host reached only through an in-scope name is not "out of scope"', () => {
    const r = scopeCoverageText({ scope_coverage: 'name', project_has_scope: true });
    expect(r.text).toBe('via in-scope name');
    expect(r.tone).toBe('info');
  });

  it('a project with no scope at all says so instead of flagging every host', () => {
    const r = scopeCoverageText({ scope_coverage: 'none', project_has_scope: false });
    expect(r.text).toBe('no scope defined');
    expect(r.tone).toBe('muted');
  });

  it('an uncovered host on a scoped project is out of scope', () => {
    const r = scopeCoverageText({ scope_coverage: 'none', project_has_scope: true });
    expect(r.text).toBe('out of scope');
    expect(r.tone).toBe('warning');
  });

  it('a row from an older backend (no coverage fields) keeps the old wording', () => {
    expect(scopeCoverageText({}).text).toBe('out of scope');
  });
});

describe('exposure chips', () => {
  it('a probed service on an unusual port is shown by name, as detected', () => {
    const chips = exposureChips([
      { port_number: 3390, state: 'open', service_name: 'ms-wbt-server', service_method: 'probed' },
    ]);
    expect(chips).toEqual([
      { key: 'RDP', label: 'RDP', port: 3390, detected: true, weight: 7 },
    ]);
  });

  it('a well-known port with no probe is a guess, marked as such', () => {
    const chips = exposureChips([
      { port_number: 445, state: 'open', service_name: 'microsoft-ds', service_method: 'table' },
    ]);
    expect(chips).toEqual([
      { key: 'SMB', label: 'SMB', port: 445, detected: false, weight: 7 },
    ]);
  });

  it('probed web services still show when no high-value port is open', () => {
    const chips = exposureChips([
      { port_number: 8443, state: 'open', service_name: 'https', service_method: 'probed' },
      { port_number: 8080, state: 'open', service_name: 'http', service_method: 'probed' },
    ]);
    expect(chips.map((c) => c.label)).toEqual(['http', 'https']);
    expect(chips.every((c) => c.detected && c.weight === 0)).toBe(true);
  });

  it('high-value services rank first, detected ahead of guessed, then other probes', () => {
    const chips = exposureChips([
      { port_number: 80, state: 'open', service_name: 'http', service_method: 'probed' },
      { port_number: 22, state: 'open', service_name: 'ssh', service_method: 'table' },
      { port_number: 3389, state: 'open', service_name: 'ms-wbt-server', service_method: 'probed' },
      { port_number: 445, state: 'open', service_name: null, service_method: null },
    ]);
    expect(chips.map((c) => `${c.label}${c.detected ? '' : '?'}`)).toEqual(['RDP', 'SMB?', 'SSH?', 'http']);
  });

  it('closed ports and unprobed unknown ports produce nothing', () => {
    expect(exposureChips([
      { port_number: 3389, state: 'closed', service_name: 'ms-wbt-server', service_method: 'probed' },
      { port_number: 9999, state: 'open', service_name: null, service_method: null },
      { port_number: 9998, state: 'open', service_name: 'unknown-svc', service_method: 'table' },
    ])).toEqual([]);
  });

  it('the same service on two ports is one chip, and a detection beats a guess', () => {
    const chips = exposureChips([
      { port_number: 22, state: 'open', service_name: 'ssh', service_method: 'table' },
      { port_number: 2222, state: 'open', service_name: 'ssh', service_method: 'probed' },
    ]);
    expect(chips).toEqual([{ key: 'SSH', label: 'SSH', port: 2222, detected: true, weight: 5 }]);
  });
});

describe('attention badge', () => {
  it('claims "critical · exploit" only when a critical vulnerability itself has the exploit', () => {
    const joined = computeAttention(host({
      vulnerability_summary: { critical: 2 } as Host['vulnerability_summary'],
      exploitable_count: 1,
      critical_exploitable_count: 1,
    }));
    expect(joined.primary?.label).toBe('2 critical · exploit');
    expect(joined.primary?.detail).toContain('1 of them has a known public exploit');
  });

  it('a critical without an exploit plus an exploitable low does not fuse into one claim', () => {
    const { primary, others } = computeAttention(host({
      vulnerability_summary: { critical: 1 } as Host['vulnerability_summary'],
      exploitable_count: 1,
      critical_exploitable_count: 0,
    }));
    expect(primary?.label).toBe('1 critical');
    expect(primary?.detail).toContain('lower-severity vulnerability on this host has a known public exploit');
    expect(others.map((r) => r.label)).toContain('Exploit available');
  });

  it('counts issues, not scanner rows, and shows medium (v5.298.0)', () => {
    // One VNC issue on two ports (two rows in the summary) is one high issue.
    const { primary, others } = computeAttention(host({
      vulnerability_summary: { high: 2, medium: 1 } as Host['vulnerability_summary'],
      issue_counts: { critical: 0, high: 1, medium: 1, low: 0, misconfiguration: 2 },
    }));
    expect(primary?.label).toBe('1 high');
    expect(others.map((r) => r.label)).toContain('1 medium');
    expect(others.find((r) => r.label === '1 medium')?.detail).toContain('2 misconfigurations');
  });

  it('an anonymous-FTP host (medium only) is no longer blank', () => {
    const { primary } = computeAttention(host({
      issue_counts: { critical: 0, high: 0, medium: 1, low: 0, misconfiguration: 1 },
    }));
    expect(primary?.label).toBe('1 medium');
  });

  it('exploit-only hosts keep the standalone reason', () => {
    const { primary } = computeAttention(host({ exploitable_count: 3 }));
    expect(primary?.label).toBe('Exploit available');
    expect(primary?.detail).toContain('3 vulnerabilities');
  });
});
