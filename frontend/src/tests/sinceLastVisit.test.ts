import { describe, it, expect } from 'vitest';

import { sinceChips, sinceWindow } from '../utils/sinceLastVisit';
import type { SinceLastVisit } from '../services/api';

const since = (over: Partial<SinceLastVisit> = {}): SinceLastVisit => ({
  last_viewed_at: '2026-09-19T18:00:00+00:00',
  is_first_visit: false,
  new_scan_count: 0,
  latest_scan_id: null,
  latest_scan_filename: null,
  latest_scan_created_at: null,
  new_host_count: 0,
  new_critical_findings: 0,
  new_high_findings: 0,
  as_of: '2026-09-19T20:00:00+00:00',
  ...over,
});

const query = (href?: string) => new URLSearchParams((href ?? '').split('?')[1]).get('q');

// v5.242.0 — the banner's counts were passive badges; each is now a link to
// exactly the records it counted, over the window the counts were taken in.
describe('sinceChips', () => {
  it('links every count to the hosts it counted, over the displayed snapshot window', () => {
    const chips = sinceChips(since({
      new_host_count: 12, changed_host_count: 3,
      new_critical_findings: 4, new_critical_hosts: 2,
    }));
    const w = '2026-09-19T18:00:00+00:00..2026-09-19T20:00:00+00:00';
    expect(chips.map((c) => c.key)).toEqual(['new-hosts', 'changed-hosts', 'critical']);
    expect(query(chips[0].href)).toBe(`firstseen:"${w}"`);
    expect(query(chips[1].href)).toBe(`changedsince:"${w}"`);
    expect(query(chips[2].href)).toBe(`vulnsince:"critical@${w}"`);
    // '+' in the offset must survive the URL (a bare '+' decodes to a space).
    expect(chips[0].href).toContain('%2B00%3A00');
  });

  it('calls scanner observations what they are, and says how many hosts the link lists', () => {
    const [crit, high] = sinceChips(since({
      new_critical_findings: 4, new_critical_hosts: 2,
      new_high_findings: 1, new_high_hosts: 1,
    }));
    expect(crit.label).toBe('4 new critical observations · 2 hosts');
    expect(high.label).toBe('1 new high observation · 1 host');
    expect(crit.label).not.toMatch(/finding/i);
    expect(crit.hint).toMatch(/not yet judged/);
  });

  it('keeps new records apart from changes to hosts already known', () => {
    const chips = sinceChips(since({ new_host_count: 1, changed_host_count: 1 }));
    expect(chips.map((c) => c.label)).toEqual(['1 new host', '1 known host changed']);
  });

  // 5.304.0 — to the imports it counted, not the whole history.
  it('sends new imports to the imports since the last visit', () => {
    const s = since({ new_scan_count: 2 });
    const [scans] = sinceChips(s);
    expect(scans).toMatchObject({ label: '2 new imports' });
    expect(scans.href).toBe(`/scans?since=${encodeURIComponent(s.last_viewed_at!)}`);
  });

  it('shows a count without a link rather than a link over the wrong window', () => {
    // An older backend sends no `as_of`: the window the counts cover is unknown.
    const old = since({ new_host_count: 5, as_of: null });
    expect(sinceWindow(old)).toBeNull();
    expect(sinceChips(old)[0]).toMatchObject({ label: '5 new hosts', href: undefined });
  });

  it('has nothing to say when nothing changed', () => {
    expect(sinceChips(since())).toEqual([]);
  });
});
