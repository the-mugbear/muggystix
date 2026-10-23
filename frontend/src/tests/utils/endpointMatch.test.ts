import { describe, expect, it } from 'vitest';
import { matchedEndpoints } from '../../utils/endpointMatch';

// Which ports matched is the server's answer (`matched_port_ids`); its cases,
// old == new with the lexer this file used to test, are
// backend/tests/test_review_2026_10_23_inventory.py's MATCH_CASES.  What is
// left on the page is the wording of the row's "Matched" line.
const ports = [
  { id: 1, port_number: 21, protocol: 'tcp', state: 'open', service_name: 'ftp', service_product: 'vsftpd', service_version: '3.0.3' },
  { id: 2, port_number: 2121, protocol: 'tcp', state: 'filtered', service_name: 'ccproxy-ftp', service_product: null, service_version: null },
  { id: 3, port_number: 22, protocol: 'tcp', state: 'open', service_name: 'ssh', service_product: 'OpenSSH', service_version: '7.4' },
  { id: 4, port_number: 23, protocol: 'tcp', state: 'open', service_name: 'telnet', service_product: null, service_version: null },
  { id: 5, port_number: 23, protocol: 'tcp', state: 'open', service_name: 'telnet', service_product: null, service_version: null },
];

const labels = (ids: number[] | undefined, byProduct = false) =>
  matchedEndpoints(ports, ids, byProduct).map((m) => (m.state ? `${m.label} (${m.state})` : m.label));

describe('matchedEndpoints', () => {
  it('names nothing when the server marked nothing', () => {
    expect(labels(undefined)).toEqual([]);
    expect(labels([])).toEqual([]);
  });

  it('names the marked ports, open ones first, with a state that is not open', () => {
    expect(labels([2, 1])).toEqual(['ftp 21/tcp', 'ccproxy-ftp 2121/tcp (filtered)']);
    expect(labels([2, 4])).toEqual(['telnet 23/tcp', 'ccproxy-ftp 2121/tcp (filtered)']);
  });

  it('names the product when a version condition matched', () => {
    expect(labels([3], true)).toEqual(['OpenSSH 7.4 22/tcp']);
    expect(labels([4], true)).toEqual(['telnet 23/tcp']);
  });

  it('lists one entry per port and protocol', () => {
    expect(labels([4, 5])).toEqual(['telnet 23/tcp']);
  });
});
