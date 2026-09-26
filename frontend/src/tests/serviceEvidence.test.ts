/**
 * The Services section's grouping (v5.297.0): every piece of a host's
 * evidence goes under the port it is about.
 */
import { describe, expect, it } from 'vitest';

import type { Port } from '../services/api';
import { evidenceForPort, summariseAccess, unplacedEvidence, worstSeverity } from '../utils/serviceEvidence';

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

});

// v5.299.0 — evidence on no OPEN port was loaded and never shown.
describe('unplacedEvidence', () => {
  const closed443 = { id: 10, port_number: 443, protocol: 'tcp', state: 'closed', last_seen: null } as unknown as Port;
  const open445 = port(20, 445);
  const host = {
    vulnerabilities: [],
    netexec: [{ id: 1, port: 445 }, { id: 2, port: 3389 }, { id: 3, port: null }],
    // On the closed port by id; on a port no scan recorded; on the open one.
    web: [{ id: 1, port_id: 10, port: 443 }, { id: 2, port_id: null, port: 8443 }, { id: 3, port_id: 20, port: 445 }],
    paths: [{ url: 'u1', port: 443 }, { url: 'u2', port: 445 }],
  } as never;

  it('groups what is on no open port by port number, with the port and its state', () => {
    const groups = unplacedEvidence([open445], [open445, closed443], host);
    expect(groups.map((g) => [g.portNumber, g.port?.state ?? null])).toEqual([
      [443, 'closed'], [3389, null], [8443, null], [null, null],
    ]);
    const on443 = groups[0].evidence;
    expect(on443.web.map((w) => w.id)).toEqual([1]);
    expect(on443.paths.map((p) => p.url)).toEqual(['u1']);
    expect(groups[2].evidence.web.map((w) => w.id)).toEqual([2]);
    expect(groups[3].evidence.access.map((r) => r.id)).toEqual([3]);
  });

  it('leaves nothing out: every row is under an open port or in a group', () => {
    const groups = unplacedEvidence([open445], [open445, closed443], host);
    const placed = evidenceForPort(open445, host);
    const web = [...placed.web, ...groups.flatMap((g) => g.evidence.web)].map((w) => w.id).sort();
    const paths = [...placed.paths, ...groups.flatMap((g) => g.evidence.paths)].map((p) => p.url).sort();
    const access = [...placed.access, ...groups.flatMap((g) => g.evidence.access)].map((r) => r.id).sort();
    expect([web, paths, access]).toEqual([[1, 2, 3], ['u1', 'u2'], [1, 2, 3]]);
  });

  it('is empty when everything is on an open port', () => {
    expect(unplacedEvidence([port(10, 443)], [port(10, 443)],
      { vulnerabilities: [], netexec: [], web: [{ id: 1, port_id: 10, port: 443 }], paths: [] } as never)).toEqual([]);
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
