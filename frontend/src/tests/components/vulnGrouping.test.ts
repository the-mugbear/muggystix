import { describe, it, expect } from 'vitest';

import { groupVulnerabilities, normalizeVulnTitle } from '../../utils/vulnGrouping';
import type { HostVulnerability } from '../../services/api/hosts';

let nextId = 1;
const vuln = (over: Partial<HostVulnerability> = {}): HostVulnerability =>
  ({
    id: nextId++,
    plugin_id: null,
    title: 'Some issue',
    severity: 'high',
    source: 'nessus',
    cvss_score: null,
    cvss_vector: null,
    cve_id: null,
    scan_id: 1,
    port_id: null,
    port_number: null,
    protocol: null,
    service_name: null,
    exploitable: null,
    finding_id: null,
    first_seen: '2026-08-01T00:00:00Z',
    last_seen: '2026-08-01T00:00:00Z',
    solution: null,
    ...over,
  }) as HostVulnerability;

describe('groupVulnerabilities', () => {
  // The reported problem: two scanners, same issue, different words.
  it('merges the same CVE reported by different scanners', () => {
    const groups = groupVulnerabilities([
      vuln({
        source: 'nessus',
        cve_id: 'CVE-2021-44228',
        title: 'Apache Log4j Remote Code Execution',
      }),
      vuln({
        source: 'openvas',
        cve_id: 'CVE-2021-44228',
        title: 'Apache Log4j RCE Vulnerability (Log4Shell)',
      }),
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0].keyKind).toBe('cve');
    expect(groups[0].cveId).toBe('CVE-2021-44228');
    expect(groups[0].reports.map((r) => r.source).sort()).toEqual(['nessus', 'openvas']);
    expect(groups[0].members).toHaveLength(2);
  });

  it('matches CVE case-insensitively', () => {
    const groups = groupVulnerabilities([
      vuln({ cve_id: 'CVE-2021-44228' }),
      vuln({ cve_id: 'cve-2021-44228', source: 'openvas' }),
    ]);
    expect(groups).toHaveLength(1);
  });

  // Different CVEs are different problems, however similar the titles read.
  it('never merges distinct CVEs', () => {
    const groups = groupVulnerabilities([
      vuln({ cve_id: 'CVE-2021-44228', title: 'Log4j RCE' }),
      vuln({ cve_id: 'CVE-2021-45046', title: 'Log4j RCE' }),
    ]);
    expect(groups).toHaveLength(2);
  });

  it('groups CVE-less findings on an exactly-normalised title', () => {
    const groups = groupVulnerabilities([
      vuln({ source: 'nessus', title: 'SSL/TLS: Weak Cipher Suites Supported' }),
      vuln({ source: 'openvas', title: 'ssl tls weak cipher suites supported' }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].keyKind).toBe('title');
  });

  // The safety property. Normalisation is not similarity matching — titles
  // that merely resemble each other must stay apart, because a wrong merge
  // hides a real finding.
  it('does not merge titles that are merely similar', () => {
    const groups = groupVulnerabilities([
      vuln({ title: 'TLS 1.0 Protocol Detected' }),
      vuln({ title: 'TLS 1.1 Protocol Detected', source: 'openvas' }),
    ]);
    expect(groups).toHaveLength(2);
  });

  // A finding with neither identifier tells us nothing about what it is;
  // pooling those together would be a guess.
  it('keeps unidentifiable findings separate', () => {
    const groups = groupVulnerabilities([
      vuln({ title: null, cve_id: null, plugin_id: null }),
      vuln({ title: null, cve_id: null, plugin_id: null, source: 'openvas' }),
    ]);
    expect(groups).toHaveLength(2);
  });

  // Disagreement is the signal, not noise to average away.
  it('flags severity disagreement and badges the worst', () => {
    const groups = groupVulnerabilities([
      vuln({ source: 'nessus', cve_id: 'CVE-2020-1111', severity: 'critical' }),
      vuln({ source: 'openvas', cve_id: 'CVE-2020-1111', severity: 'medium' }),
    ]);
    expect(groups[0].severity).toBe('critical');
    expect(groups[0].severityDisagreement).toBe(true);
  });

  it('does not flag disagreement when scanners agree', () => {
    const groups = groupVulnerabilities([
      vuln({ source: 'nessus', cve_id: 'CVE-2020-1111', severity: 'high' }),
      vuln({ source: 'openvas', cve_id: 'CVE-2020-1111', severity: 'high' }),
    ]);
    expect(groups[0].severityDisagreement).toBe(false);
  });

  it('carries exploitability if any scanner reported it', () => {
    const groups = groupVulnerabilities([
      vuln({ cve_id: 'CVE-2020-2222', exploitable: false }),
      vuln({ cve_id: 'CVE-2020-2222', exploitable: true, source: 'openvas' }),
    ]);
    expect(groups[0].exploitable).toBe(true);
  });

  // The same CVE on two services is one problem affecting two places —
  // more useful as one row listing both ports than as two rows.
  it('collects distinct affected ports', () => {
    const groups = groupVulnerabilities([
      vuln({ cve_id: 'CVE-2020-3333', port_number: 8443 }),
      vuln({ cve_id: 'CVE-2020-3333', port_number: 443 }),
      vuln({ cve_id: 'CVE-2020-3333', port_number: 443, source: 'openvas' }),
    ]);
    expect(groups[0].ports).toEqual([443, 8443]);
  });

  it('gives one report per scanner even when it reported twice', () => {
    const groups = groupVulnerabilities([
      vuln({ source: 'nessus', cve_id: 'CVE-2020-4444', port_number: 443 }),
      vuln({ source: 'nessus', cve_id: 'CVE-2020-4444', port_number: 8443 }),
    ]);
    expect(groups[0].reports).toHaveLength(1);
    expect(groups[0].reports[0].members).toHaveLength(2);
  });

  // Grouping must never reorder the list relative to the flat view.
  it('orders groups worst-severity first', () => {
    const groups = groupVulnerabilities([
      vuln({ cve_id: 'CVE-1', severity: 'low' }),
      vuln({ cve_id: 'CVE-2', severity: 'critical' }),
      vuln({ cve_id: 'CVE-3', severity: 'medium' }),
    ]);
    expect(groups.map((g) => g.severity)).toEqual(['critical', 'medium', 'low']);
  });

  // The no-regression property: with nothing to merge, grouping is a no-op.
  it('is a pass-through when every finding is distinct', () => {
    const input = [
      vuln({ cve_id: 'CVE-9001' }),
      vuln({ cve_id: 'CVE-9002' }),
      vuln({ title: 'Unique config check' }),
    ];
    const groups = groupVulnerabilities(input);
    expect(groups).toHaveLength(3);
    groups.forEach((g) => expect(g.members).toHaveLength(1));
  });

  it('handles an empty list', () => {
    expect(groupVulnerabilities([])).toEqual([]);
  });
});

describe('normalizeVulnTitle', () => {
  it('strips vendor prefixes and suffixes', () => {
    expect(normalizeVulnTitle('Nessus: Weak Ciphers')).toBe('weak ciphers');
    expect(normalizeVulnTitle('Weak Ciphers (OpenVAS)')).toBe('weak ciphers');
  });

  it('preserves version numbers, which distinguish real findings', () => {
    expect(normalizeVulnTitle('TLS 1.0 Detected')).toContain('1.0');
    expect(normalizeVulnTitle('TLS 1.0 Detected')).not.toBe(
      normalizeVulnTitle('TLS 1.1 Detected'),
    );
  });
});
