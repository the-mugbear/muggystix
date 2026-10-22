/**
 * Review 2026-10-10, R1d: "is this a web port, http or https" is one rule.
 * The Scan page, the inspector's web links and the connection helpers had
 * three port sets (a service on 3000 linked on one page and not the other).
 * All three read `utils/webPort`, which is the server's web-targets rule; its
 * port table is pinned to the server's here.
 */
import { readFileSync } from 'fs';
import { join } from 'path';

import { describe, expect, it } from 'vitest';

import type { Host, Port } from '../../services/api';
import { getConnectionHelpers } from '../../utils/connectionHelpers';
import { getHostWebLinks } from '../../utils/webLinks';
import { WEB_PORT_SCHEMES, webScheme } from '../../utils/webPort';

const repoRoot = join(__dirname, '..', '..', '..', '..');

const port = (port_number: number, over: Partial<Port> = {}): Port => ({
  id: port_number, port_number, protocol: 'tcp', state: 'open', service_name: null,
  service_product: null, service_version: null, ...over,
});

describe('webScheme', () => {
  it('uses the same port table as the server', () => {
    const src = readFileSync(
      join(repoRoot, 'backend', 'app', 'services', 'scope_targets_service.py'), 'utf8',
    );
    const block = src.match(/_WEB_PORT_SCHEMES[^{]*\{([\s\S]*?)\}/);
    expect(block).not.toBeNull();
    const server = Object.fromEntries(
      [...block![1].matchAll(/(\d+):\s*"(https?)"/g)].map((m) => [Number(m[1]), m[2]]),
    );
    expect(server).toEqual(WEB_PORT_SCHEMES);
  });

  it('follows the service name first, then the table for an unidentified port', () => {
    expect(webScheme(port(3000, { service_name: 'http' }))).toBe('http');
    expect(webScheme(port(3000))).toBeNull();
    expect(webScheme(port(8443, { service_name: 'http', service_tunnel: 'ssl' }))).toBe('https');
    expect(webScheme(port(9000, { service_name: 'https' }))).toBe('https');
    expect(webScheme(port(443))).toBe('https');
    expect(webScheme(port(8080, { service_name: 'unknown' }))).toBe('http');
    // nmap's guess from the port number is not an identification.
    expect(webScheme(port(4443, { service_name: 'pharos', service_method: 'table' }))).toBe('https');
    // TLS alone is not web, nor a web port number under another service.
    expect(webScheme(port(993, { service_name: 'imaps', service_tunnel: 'ssl' }))).toBeNull();
    expect(webScheme(port(443, { service_name: 'ssh', service_method: 'probed' }))).toBeNull();
  });
});

describe('every page asks the same question', () => {
  const ports = [
    port(3000, { service_name: 'http' }),
    port(3001),
    port(443, { service_name: 'ssh', service_method: 'probed' }),
    port(8443, { service_name: 'http', service_tunnel: 'ssl' }),
    port(993, { service_name: 'imaps', service_tunnel: 'ssl' }),
  ];

  it('the inspector links and the connection helpers agree with webScheme', () => {
    const host = { ip_address: '10.0.0.5', hostname: null, ports } as unknown as Host;
    const links = getHostWebLinks(host).map((l) => `${l.protocol}:${l.port}`).sort();
    const expected = ports
      .filter((p) => webScheme(p))
      .map((p) => `${webScheme(p)}:${p.port_number}`)
      .sort();
    expect(links).toEqual(expected);
    expect(links).toEqual(['http:3000', 'https:8443']);

    for (const p of ports) {
      const curl = getConnectionHelpers('10.0.0.5', p).find((h) => h.tool === 'curl');
      const scheme = webScheme(p);
      if (scheme) expect(curl?.command).toContain(`${scheme}://`);
      else expect(curl?.command ?? '').not.toMatch(/https?:\/\//);
    }
  });
});
