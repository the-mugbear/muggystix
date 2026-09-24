import { describe, expect, it } from 'vitest';
import {
  endpointMatchCriteria, matchedEndpoints, positiveEndpointTerms, resolveEndpointStates, splitPortState,
} from '../../utils/endpointMatch';

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

  it('matches a service as a substring of the service name, OPEN ports only by default', () => {
    // A filtered port's name is nmap's port-table guess — no evidence the service runs.
    expect(labels({ services: ['ftp'] })).toEqual(['ftp 21/tcp']);
    expect(labels({ ports: ['2121'] })).toEqual([]);
  });

  it('honours an explicit structured state, and "any" matches every state, open first', () => {
    expect(labels({ services: ['ftp'], portStates: ['filtered'] })).toEqual(['ccproxy-ftp 2121/tcp (filtered)']);
    expect(labels({ services: ['ftp'], portStates: ['any'] })).toEqual(['ftp 21/tcp', 'ccproxy-ftp 2121/tcp (filtered)']);
    expect(labels({ services: ['ftp'], portStates: ['open', 'filtered'] })).toEqual(['ftp 21/tcp', 'ccproxy-ftp 2121/tcp (filtered)']);
  });

  it('reads an @state suffix per query value; without one a query term matches open ports', () => {
    expect(labels({ query: 'service:ftp' })).toEqual(['ftp 21/tcp']);
    expect(labels({ query: 'port:2121' })).toEqual([]);
    expect(labels({ query: 'port:2121@filtered' })).toEqual(['ccproxy-ftp 2121/tcp (filtered)']);
    expect(labels({ query: 'port:2121@closed' })).toEqual([]);
    expect(labels({ query: 'service:ftp@any' })).toEqual(['ftp 21/tcp', 'ccproxy-ftp 2121/tcp (filtered)']);
    expect(labels({ query: 'port:2121@any,23' })).toEqual(['telnet 23/tcp', 'ccproxy-ftp 2121/tcp (filtered)']);
  });

  it('resolveEndpointStates and splitPortState mirror the backend rule', () => {
    expect(resolveEndpointStates([], true)).toEqual(['open']);
    expect(resolveEndpointStates([], false)).toBeNull();
    expect(resolveEndpointStates(['Closed'], true)).toEqual(['closed']);
    expect(resolveEndpointStates(['closed', 'any'], true)).toBeNull();
    expect(splitPortState('ssh@closed')).toEqual({ value: 'ssh', state: 'closed' });
    expect(splitPortState('user@example')).toEqual({ value: 'user@example', state: null });
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
