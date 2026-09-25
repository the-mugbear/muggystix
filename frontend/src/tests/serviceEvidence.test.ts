/**
 * The Services section's grouping (v5.297.0): every piece of a host's
 * evidence goes under the port it is about.
 */
import { describe, expect, it } from 'vitest';

import type { Port } from '../services/api';
import { evidenceForPort, summariseAccess, unplacedNetexec, worstSeverity } from '../utils/serviceEvidence';

const port = (id: number, n: number) => ({ id, port_number: n, protocol: 'tcp', state: 'open' }) as Port;

const all = {
  vulnerabilities: [
    { id: 1, port_id: 10, severity: 'low', title: 'b' },
    { id: 2, port_id: 10, severity: 'high', title: 'a' },
    { id: 3, port_id: 11, severity: 'medium', title: 'c' },
  ],
  netexec: [{ id: 1, port: 445, auth_success: true }, { id: 2, port: 3389, auth_success: null }],
  // One linked by port_id, one only by number (the web tool made no port row).
  web: [{ id: 1, port_id: 10, port: 443 }, { id: 2, port_id: null, port: 443 }, { id: 3, port_id: null, port: 8080 }],
  paths: [{ url: 'u1', port: 443 }, { url: 'u2', port: 80 }],
} as never;

describe('evidenceForPort', () => {
  it('splits a host\'s evidence by port, worst weakness first', () => {
    const e = evidenceForPort(port(10, 443), all);
    expect(e.weaknesses.map((v) => v.id)).toEqual([2, 1]);
    expect(e.web.map((w) => w.id)).toEqual([1, 2]);
    expect(e.paths.map((p) => p.url)).toEqual(['u1']);
    expect(e.access).toEqual([]);
  });

  it('lists one row per issue, keeping the worst-rated', () => {
    const rows = [
      { id: 1, port_id: 10, severity: 'low', title: 'HSTS missing', issue_key: 'title:hsts' },
      { id: 2, port_id: 10, severity: 'medium', title: 'HSTS missing', issue_key: 'title:hsts' },
    ];
    const e = evidenceForPort(port(10, 443), { vulnerabilities: rows, netexec: [], web: [], paths: [] } as never);
    expect(e.weaknesses.map((v) => v.id)).toEqual([2]);
  });

  it('finds results on no listed port', () => {
    expect(unplacedNetexec([port(20, 445)], (all as { netexec: never[] }).netexec).map((r: { id: number }) => r.id)).toEqual([2]);
  });
});

describe('summaries', () => {
  it('names the worst severity and how many share it', () => {
    const e = evidenceForPort(port(10, 443), all);
    expect(worstSeverity(e.weaknesses)).toEqual({ severity: 'high', count: 1 });
    expect(worstSeverity([])).toBeNull();
  });

  it('splits access into worked, failed and other', () => {
    const s = summariseAccess([{ auth_success: true }, { auth_success: false }, { auth_success: false }, { auth_success: null }]);
    expect([s.worked.length, s.failed.length, s.other.length]).toEqual([1, 2, 1]);
  });
});
