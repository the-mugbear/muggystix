import { describe, expect, it } from 'vitest';

import type { Port, WebInterface } from '../services/api';
import { getConnectionHelpers, isSafeHostname } from '../utils/connectionHelpers';
import { endpointNameOf, endpointsByPort, summariseEndpointTls } from '../utils/portEndpoints';

const day = 86_400_000;
const iso = (offsetDays: number) => new Date(Date.now() + offsetDays * day).toISOString();

const web = (over: Partial<WebInterface>): WebInterface => ({
  id: 1, source: 'httpx', url: 'https://10.0.0.5/', has_screenshot: false, scan_id: 1, port_id: 443, ...over,
}) as WebInterface;

describe('endpointsByPort', () => {
  it('keeps each website on a shared port separate — one cert never describes another', () => {
    const map = endpointsByPort([
      web({ id: 1, fqdn: 'shop.example.com', url: 'https://shop.example.com/', cert_not_after: iso(200), last_seen: iso(-1) }),
      web({ id: 2, fqdn: 'legacy.example.com', url: 'https://legacy.example.com/', cert_not_after: iso(-10), tls_weak_protocol: true, last_seen: iso(-3) }),
      web({ id: 3, url: 'https://10.0.0.5/', cert_self_signed: true, last_seen: iso(-2) }),
    ]);
    const endpoints = map.get(443)!;
    // Named first (A→Z), the bare address last.
    expect(endpoints.map((e) => e.name)).toEqual(['legacy.example.com', 'shop.example.com', null]);
    expect(endpoints[0].tls).toMatchObject({ tls_weak_protocol: true });
    expect(endpoints[1].tls?.tls_weak_protocol).toBeUndefined();
    expect(endpoints[2].tls).toMatchObject({ cert_self_signed: true });
    // Name/URL, source and observation time travel with the fact.
    expect(endpoints[0]).toMatchObject({ url: 'https://legacy.example.com/', source: 'httpx' });
    expect(endpoints[0].last_seen).toBeTruthy();

    expect(summariseEndpointTls(endpoints)).toEqual({ withTls: 3, weak: 1, selfSigned: 1, expired: 1, expiringSoon: 0 });
  });

  it('within ONE endpoint the newest observation wins, without erasing older TLS evidence', () => {
    const map = endpointsByPort([
      web({ id: 1, fqdn: 'a.example.com', source: 'httpx', cert_not_after: iso(40), last_seen: iso(-9) }),
      web({ id: 2, fqdn: 'A.example.com', source: 'eyewitness', last_seen: iso(-1) }), // newer, no TLS detail
    ]);
    const [only] = map.get(443)!;
    expect(map.get(443)).toHaveLength(1); // same name, case-insensitively
    expect(only.source).toBe('eyewitness');
    expect(only.tls?.cert_not_after).toBeTruthy();
  });

  it('names an endpoint from the URL when no fqdn was recorded, and never calls an address a name', () => {
    expect(endpointNameOf({ fqdn: null, url: 'https://portal.example.com:8443/x' })).toBe('portal.example.com');
    expect(endpointNameOf({ fqdn: null, url: 'https://10.0.0.5:8443/' })).toBeNull();
    expect(endpointNameOf({ fqdn: null, url: 'https://[2001:db8::1]/' })).toBeNull();
    expect(endpointNameOf({ fqdn: null, url: 'not a url' })).toBeNull();
    expect(endpointsByPort([web({ port_id: null })]).size).toBe(0);
  });
});

const port = (over: Partial<Port>): Port => ({ id: 1, port_number: 443, protocol: 'tcp', state: 'open', service_name: 'https', ...over }) as Port;

describe('getConnectionHelpers with a named endpoint', () => {
  it('addresses the website, pinned to this host, instead of the default site', () => {
    const helpers = getConnectionHelpers('10.0.0.5', port({}), null, { vhost: 'shop.example.com' });
    const byTool = Object.fromEntries(helpers.map((h) => [h.tool, h.command]));
    expect(byTool.curl).toBe('curl -ik --resolve shop.example.com:443:10.0.0.5 https://shop.example.com/');
    expect(byTool.openssl).toContain('-connect 10.0.0.5:443 -servername shop.example.com');
    expect(byTool['hosts entry']).toBe("echo '10.0.0.5 shop.example.com' | sudo tee -a /etc/hosts");
    expect(byTool.nikto).toBe('nikto -h https://shop.example.com');
  });

  it('without a name behaves as before (and says the certificate is the default site\'s)', () => {
    const helpers = getConnectionHelpers('10.0.0.5', port({ port_number: 8443 }), null);
    const byTool = Object.fromEntries(helpers.map((h) => [h.tool, h]));
    expect(byTool.curl.command).toBe('curl -ik https://10.0.0.5:8443/');
    expect(byTool.openssl.description).toMatch(/default site/);
    expect(byTool['hosts entry']).toBeUndefined();
  });

  it('brackets an IPv6 address in URLs and --resolve', () => {
    const named = getConnectionHelpers('2001:db8::1', port({}), null, { vhost: 'v6.example.com' });
    expect(named[0].command).toBe('curl -ik --resolve v6.example.com:443:[2001:db8::1] https://v6.example.com/');
    expect(getConnectionHelpers('2001:db8::1', port({}), null)[0].command).toBe('curl -ik https://[2001:db8::1]/');
  });

  it('refuses a scanner-reported name that is not plainly a hostname', () => {
    // It would land inside a command the operator pastes into a shell.
    for (const bad of ['a.com; rm -rf ~', '$(id).example.com', "x'y.example.com", 'a b', '-oProxyCommand', '']) {
      expect(isSafeHostname(bad)).toBe(false);
    }
    expect(isSafeHostname('shop-1.example.com')).toBe(true);
    const helpers = getConnectionHelpers('10.0.0.5', port({}), null, { vhost: 'a.com; rm -rf ~' });
    expect(helpers[0].command).toBe('curl -ik https://10.0.0.5/');
    expect(helpers.some((h) => h.command.includes('rm -rf'))).toBe(false);
  });
});
