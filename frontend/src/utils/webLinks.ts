import type { Host, Port } from '../services/api';
import { webScheme } from './webPort';

export interface HostWebLink {
  protocol: 'http' | 'https';
  url: string;
  port: number;
  label: string;
}

const isOpenPort = (port: Port) => port.state?.toLowerCase() === 'open';

/**
 * Validate that a host label is safe to interpolate into a URL authority.
 * `host.hostname` is scanner/PTR data (untrusted), so a value like
 * `user@evil.example` or `realhost/path` could produce a link that points
 * somewhere other than the displayed label (a misdirection/phishing
 * vector — the protocol is fixed so this is not script injection, but the
 * link must not be reshaped). Accept only IPv4/IPv6 literals and
 * RFC-1123 hostname characters; reject anything carrying URL-structural
 * characters or whitespace.
 */
export const isSafeHostLabel = (host: string): boolean => {
  if (!host || /[@/\\?#\s]/.test(host)) {
    return false;
  }
  // IPv6 (with the colons it legitimately contains) — letters/digits/colons.
  if (host.includes(':')) {
    return /^[0-9A-Fa-f:]+$/.test(host);
  }
  // IPv4 or RFC-1123 hostname: labels of [A-Za-z0-9-] separated by dots.
  return /^[A-Za-z0-9.-]+$/.test(host);
};

/**
 * Bracket IPv6 literals so they can carry a `:port` suffix without
 * the address's own colons being misread as the port separator.
 * Hostnames and IPv4 addresses pass through unchanged.
 */
export const formatHostForUrl = (host: string): string => {
  if (host.includes(':') && !host.startsWith('[')) {
    return `[${host}]`;
  }
  return host;
};

const buildUrl = (hostLabel: string, protocol: 'http' | 'https', port: number) => {
  const defaultPort = protocol === 'https' ? 443 : 80;
  const portSuffix = port === defaultPort ? '' : `:${port}`;
  return `${protocol}://${formatHostForUrl(hostLabel)}${portSuffix}`;
};

export const getHostWebLinks = (host: Host): HostWebLink[] => {
  const ports = host.ports?.filter(isOpenPort) ?? [];
  if (!ports.length) {
    return [];
  }

  const hostLabel = (host.hostname && host.hostname.trim()) || host.ip_address;
  if (!hostLabel || !isSafeHostLabel(hostLabel)) {
    return [];
  }

  // One link per open web port, its scheme from `utils/webPort` (the one
  // rule, R1d — this list had its own two port sets).
  const links: HostWebLink[] = [];
  ports.forEach((port) => {
    const protocol = webScheme(port);
    if (!protocol) return;
    const defaultPort = protocol === 'https' ? 443 : 80;
    links.push({
      protocol,
      port: port.port_number,
      url: buildUrl(hostLabel, protocol, port.port_number),
      label: `${hostLabel}${port.port_number === defaultPort ? '' : `:${port.port_number}`}`,
    });
  });

  return links.sort((a, b) => {
    if (a.protocol !== b.protocol) {
      return a.protocol === 'https' ? -1 : 1;
    }
    return a.port - b.port;
  });
};
