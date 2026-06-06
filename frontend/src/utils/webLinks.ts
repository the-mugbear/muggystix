import type { Host, Port } from '../services/api';

export interface HostWebLink {
  protocol: 'http' | 'https';
  url: string;
  port: number;
  label: string;
}

const HTTPS_PORTS = new Set([443, 8443, 9443, 9444, 4443]);
const HTTP_PORTS = new Set([80, 8080, 8081, 8000, 8008, 8888, 9090, 8181]);

const isOpenPort = (port: Port) => port.state?.toLowerCase() === 'open';

const isLikelyHttps = (port: Port) => {
  if (HTTPS_PORTS.has(port.port_number)) {
    return true;
  }
  const name = port.service_name?.toLowerCase() || '';
  return name.includes('https') || name.includes('ssl');
};

const isLikelyHttp = (port: Port) => {
  if (HTTP_PORTS.has(port.port_number)) {
    return true;
  }
  const name = port.service_name?.toLowerCase() || '';
  if (name.includes('https')) {
    return false;
  }
  return name.includes('http') || name.includes('web');
};

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

  const links: HostWebLink[] = [];
  const seen = new Set<string>();

  ports.forEach((port) => {
    if (isLikelyHttps(port)) {
      const key = `https-${port.port_number}`;
      if (!seen.has(key)) {
        seen.add(key);
        links.push({
          protocol: 'https',
          port: port.port_number,
          url: buildUrl(hostLabel, 'https', port.port_number),
          label: `${hostLabel}${port.port_number === 443 ? '' : `:${port.port_number}`}`,
        });
      }
    }
  });

  ports.forEach((port) => {
    if (isLikelyHttp(port)) {
      const key = `http-${port.port_number}`;
      if (!seen.has(key)) {
        seen.add(key);
        links.push({
          protocol: 'http',
          port: port.port_number,
          url: buildUrl(hostLabel, 'http', port.port_number),
          label: `${hostLabel}${port.port_number === 80 ? '' : `:${port.port_number}`}`,
        });
      }
    }
  });

  return links.sort((a, b) => {
    if (a.protocol !== b.protocol) {
      return a.protocol === 'https' ? -1 : 1;
    }
    return a.port - b.port;
  });
};
