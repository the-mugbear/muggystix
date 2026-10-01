/**
 * Review 2026-10-01 C2 / B13 — a findings LIST row carries a preview of at
 * most five endpoints and the true total in `host_count`.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  chunked, endpointPreviewIsCut, endpointPreviewTitle, endpointStateCounts, filterEndpoints, idRange,
} from '../utils/findingEndpoints';

const h = (ip: string, status = 'open', hostname: string | null = null, fqdn: string | null = null) => ({
  ip_address: ip, hostname, fqdn, host_status: status as 'open',
});

describe('finding endpoints', () => {
  it('a preview shorter than host_count is cut; an equal one is whole', () => {
    expect(endpointPreviewIsCut({ hosts: [h('10.0.0.1')] as never, host_count: 2000 })).toBe(true);
    expect(endpointPreviewIsCut({ hosts: [h('10.0.0.1')] as never, host_count: 1 })).toBe(false);
    expect(endpointPreviewIsCut({ hosts: [] as never, host_count: 0 })).toBe(false);
  });

  it('the tooltip says how many endpoints it does not list', () => {
    expect(endpointPreviewTitle([h('10.0.0.1', 'open', 'db'), h('10.0.0.2')], 2)).toBe('10.0.0.1 (db), 10.0.0.2');
    expect(endpointPreviewTitle([h('10.0.0.1'), h('10.0.0.2')], 1502)).toBe('10.0.0.1, 10.0.0.2, and 1,500 more');
    expect(endpointPreviewTitle([{ ip_address: null, hostname: null }], 1)).toBe('—');
  });

  it('filters by state and by address, hostname or name, case-insensitively', () => {
    const hosts = [h('10.0.0.1', 'open', 'DB01'), h('10.0.0.2', 'remediated', null, 'portal.example.com'), h('10.0.1.1', 'open')];
    expect(filterEndpoints(hosts, 'all', '')).toHaveLength(3);
    expect(filterEndpoints(hosts, 'remediated', '')).toHaveLength(1);
    expect(filterEndpoints(hosts, 'all', 'db0')).toEqual([hosts[0]]);
    expect(filterEndpoints(hosts, 'all', 'PORTAL')).toEqual([hosts[1]]);
    expect(filterEndpoints(hosts, 'open', '10.0.1')).toEqual([hosts[2]]);
    expect(filterEndpoints(hosts, 'open', 'portal')).toEqual([]);
  });

  it('counts per state from the server roll-up when sent, else from the rows', () => {
    const hosts = [h('a', 'open'), h('b', 'retest')];
    expect(endpointStateCounts(hosts, { open: 1900, remediated: 100 })).toEqual({ open: 1900, retest: 0, remediated: 100, false_positive: 0 });
    expect(endpointStateCounts(hosts)).toEqual({ open: 1, retest: 1, remediated: 0, false_positive: 0 });
    expect(endpointStateCounts(hosts, {})).toEqual({ open: 1, retest: 1, remediated: 0, false_positive: 0 });
  });

  it('a shift-click range runs from the anchor to the target, either way round', () => {
    const ids = [5, 6, 7, 8, 9];
    expect(idRange(ids, 6, 8)).toEqual([6, 7, 8]);
    expect(idRange(ids, 8, 6)).toEqual([6, 7, 8]);
    expect(idRange(ids, null, 7)).toEqual([7]);
    expect(idRange(ids, 99, 7)).toEqual([7]);   // the anchor was filtered away
    expect(idRange(ids, 6, 99)).toEqual([]);
  });

  it('chunks a selection to the route’s ceiling', () => {
    expect(chunked(Array.from({ length: 1200 }, (_, i) => i), 500).map((c) => c.length)).toEqual([500, 500, 200]);
    expect(chunked([], 500)).toEqual([]);
  });
});

// Review 2026-10-01 N6 — vulnGrouping.ts held a literal NUL byte inside a
// template literal, so `grep` treated the file as binary and skipped it.
describe('source files are text', () => {
  it('vulnGrouping.ts has no NUL byte, and still separates source from identity with one', () => {
    const bytes = readFileSync(resolve(__dirname, '../utils/vulnGrouping.ts'));
    expect(bytes.includes(0)).toBe(false);
    expect(bytes.toString('utf8')).toContain('`${source}\\u0000${ident}`');
  });
});
