import { describe, expect, it } from 'vitest';
import { endpointMatchCriteria, matchedEndpoints, positiveEndpointTerms } from '../../utils/endpointMatch';

const ports = [
  { port_number: 21, protocol: 'tcp', state: 'open', service_name: 'ftp', service_product: 'vsftpd', service_version: '3.0.3' },
  { port_number: 2121, protocol: 'tcp', state: 'filtered', service_name: 'ccproxy-ftp', service_product: null, service_version: null },
  { port_number: 22, protocol: 'tcp', state: 'open', service_name: 'ssh', service_product: 'OpenSSH', service_version: '7.4' },
  { port_number: 23, protocol: 'tcp', state: 'open', service_name: 'telnet', service_product: null, service_version: null },
];

const labels = (filters: Parameters<typeof endpointMatchCriteria>[0]) =>
  matchedEndpoints(ports, endpointMatchCriteria(filters)).map((m) => (m.state ? `${m.label} (${m.state})` : m.label));

describe('endpointMatch', () => {
  it('shows nothing without a port / service / version condition', () => {
    expect(endpointMatchCriteria({})).toBeNull();
    expect(endpointMatchCriteria({ hasOpenPorts: true, portStates: ['open'] })).toBeNull();
    expect(endpointMatchCriteria({ query: 'tag:dmz AND os:linux' })).toBeNull();
    expect(labels({})).toEqual([]);
  });

  it('matches a service as a substring of the service name, any state, open first', () => {
    expect(labels({ services: ['ftp'] })).toEqual(['ftp 21/tcp', 'ccproxy-ftp 2121/tcp (filtered)']);
  });

  it('applies every structured dimension to the same port', () => {
    expect(labels({ services: ['ftp'], portStates: ['open'] })).toEqual(['ftp 21/tcp']);
    expect(labels({ ports: ['22'], services: ['ftp'] })).toEqual([]);
    expect(labels({ services: ['ftp'], hasOpenPorts: false })).toEqual([]);
  });

  it('reads positive port / service / version terms from the query and ignores negated ones', () => {
    expect(labels({ query: 'port:23' })).toEqual(['telnet 23/tcp']);
    expect(labels({ query: 'svc:ssh AND NOT service:ftp' })).toEqual(['ssh 22/tcp']);
    expect(labels({ query: 'NOT (port:21 OR port:22) port:23' })).toEqual(['telnet 23/tcp']);
    expect(labels({ query: 'version:"OpenSSH 7"' })).toEqual(['OpenSSH 7.4 22/tcp']);
  });

  it('parses quoted, comma-separated values', () => {
    expect(positiveEndpointTerms('service:"ms-wbt-server",ftp NOT port:22')).toEqual([
      { field: 'service', values: ['ms-wbt-server', 'ftp'] },
    ]);
  });
});
