/**
 * Evidence freshness beside the assertion (design review item 4): a recent
 * host observation must not make every fact about the host look current.
 */
import { describe, expect, it } from 'vitest';

import type { HostAssessment } from '../services/api';
import { changesSinceReview, freshnessFacts, portFreshness } from '../utils/evidenceFreshness';

const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

describe('portFreshness', () => {
  it('flags a port the latest sweep did not see open', () => {
    const f = portFreshness({ last_seen: daysAgo(10), first_seen: daysAgo(40) }, daysAgo(1));
    expect(f.notInLatestScan).toBe(true);
    expect(f.seen).toBeTruthy();
  });

  it('a port seen in the same sweep as the host is current', () => {
    const host = daysAgo(1);
    const port = new Date(new Date(host).getTime() - 5 * 60_000).toISOString(); // 5 minutes earlier
    expect(portFreshness({ last_seen: port, first_seen: port }, host).notInLatestScan).toBe(false);
  });

  it('says nothing when either side is unknown', () => {
    expect(portFreshness({ last_seen: null, first_seen: null }, daysAgo(1))).toEqual({ seen: null, notInLatestScan: false });
    expect(portFreshness({ last_seen: daysAgo(3), first_seen: null }, null).notInLatestScan).toBe(false);
  });
});

const assessment = (over: Partial<HostAssessment> = {}): HostAssessment => ({
  last_observed_at: daysAgo(1),
  vuln_assessed: false,
  last_vuln_assessed_at: null,
  web_eligible: false,
  web_assessed: false,
  last_web_assessed_at: null,
  auth_eligible: false,
  auth_assessed: false,
  tests_executed: 0,
  last_tested_at: null,
  conflicts: 0,
  open_ports_not_in_latest_scan: 0,
  ...over,
});

describe('freshnessFacts', () => {
  it('tells "not assessed" from "n/a" from a dated assessment', () => {
    const facts = freshnessFacts(assessment({
      web_eligible: true,
      auth_eligible: true, auth_assessed: true,
      tests_executed: 2, last_tested_at: daysAgo(90),
      conflicts: 3, open_ports_not_in_latest_scan: 1,
    }));
    const byKey = Object.fromEntries(facts.map((f) => [f.key, f]));
    expect(byKey.observed.tone).toBe('ok');
    expect(byKey.vulns).toMatchObject({ value: 'not assessed', tone: 'gap' });
    expect(byKey.web).toMatchObject({ value: 'not assessed', tone: 'gap' });
    expect(byKey.auth).toMatchObject({ value: 'assessed', tone: 'ok' });
    expect(byKey.tested.tone).toBe('ok');
    expect(byKey.tested.value).not.toBe('never');
    expect(byKey.conflicts).toMatchObject({ value: '3', tone: 'warn' });
    expect(byKey.stale_ports).toMatchObject({ value: '1', tone: 'warn' });
  });

  it('a host with no web or auth port says n/a, not a gap', () => {
    const byKey = Object.fromEntries(freshnessFacts(assessment()).map((f) => [f.key, f]));
    expect(byKey.web).toMatchObject({ value: 'n/a', tone: 'na' });
    expect(byKey.auth).toMatchObject({ value: 'n/a', tone: 'na' });
    expect(byKey.tested).toMatchObject({ value: 'never', tone: 'gap' });
    expect(byKey.conflicts).toBeUndefined();
  });
});

describe('changesSinceReview', () => {
  const reviewed = daysAgo(10);
  it('is null without a review or when nothing happened after it', () => {
    expect(changesSinceReview(null, daysAgo(1), [], [])).toBeNull();
    expect(changesSinceReview(reviewed, daysAgo(20), [], [])).toBeNull();
  });

  it('separates material changes from a mere re-observation', () => {
    const reobserved = changesSinceReview(reviewed, daysAgo(1), [
      { id: 1, port_number: 22, protocol: 'tcp', first_seen: daysAgo(30) },
    ], []);
    expect(reobserved).toMatchObject({ reobservedOnly: true, newPorts: [], newVulns: [] });

    const changed = changesSinceReview(reviewed, daysAgo(1), [
      { id: 1, port_number: 22, protocol: 'tcp', first_seen: daysAgo(30) },
      { id: 2, port_number: 3389, protocol: 'tcp', first_seen: daysAgo(2) },
    ], [{ id: 9, title: 'MS17-010', severity: 'critical', first_seen: daysAgo(3) }]);
    expect(changed?.reobservedOnly).toBe(false);
    expect(changed?.newPorts.map((p) => p.port_number)).toEqual([3389]);
    expect(changed?.newVulns.map((v) => v.title)).toEqual(['MS17-010']);
  });
});
