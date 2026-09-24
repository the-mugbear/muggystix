/**
 * Client-side check for a scope entry (v5.290.0): an IPv4 / IPv6 address or
 * CIDR range, accepting what the backend's `ipaddress.ip_network(strict=False)`
 * accepts — host bits set (`10.0.0.5/24`), and for IPv4 a dotted netmask or
 * hostmask in place of the prefix length (`10.0.0.0/255.255.255.0`).
 *
 * It exists so an obvious typo is caught under the field before a request,
 * not so the server can stop validating: the server stays the authority.
 */

export const IP_OR_CIDR_HINT = 'Not an IP address or CIDR range — e.g. 10.0.0.0/24 or 10.0.0.5';

const DECIMAL = /^(0|[1-9]\d*)$/;

function ipv4ToInt(value: string): number | null {
  const parts = value.split('.');
  if (parts.length !== 4) return null;
  let n = 0;
  for (const part of parts) {
    // Python rejects leading zeros ("010") as ambiguous octal; so do we.
    if (!DECIMAL.test(part) || part.length > 3) return null;
    const octet = Number(part);
    if (octet > 255) return null;
    n = n * 256 + octet;
  }
  return n;
}

export function isIPv4Address(value: string): boolean {
  return ipv4ToInt(value) !== null;
}

export function isIPv6Address(value: string): boolean {
  if (!value.includes(':')) return false;
  const halves = value.split('::');
  if (halves.length > 2) return false;
  const groupsOf = (s: string) => (s === '' ? [] : s.split(':'));
  const head = groupsOf(halves[0]);
  const tail = halves.length === 2 ? groupsOf(halves[1]) : [];
  const all = [...head, ...tail];
  let count = 0;
  for (let i = 0; i < all.length; i += 1) {
    const g = all[i];
    const isLast = i === all.length - 1;
    if (isLast && g.includes('.')) {
      // An embedded IPv4 address counts as two groups, and only at the end.
      if (!isIPv4Address(g)) return false;
      count += 2;
    } else if (/^[0-9a-fA-F]{1,4}$/.test(g)) {
      count += 1;
    } else {
      return false;
    }
  }
  return halves.length === 2 ? count <= 7 : count === 8;
}

function isContiguousMask(n: number): boolean {
  // A netmask is ones then zeros; a hostmask is zeros then ones.
  const inverted = (~n) >>> 0;
  const isNetmask = ((inverted + 1) & inverted) === 0;
  const isHostmask = ((n + 1) & n) === 0;
  return isNetmask || isHostmask;
}

/** True when `raw` is an IPv4/IPv6 address or CIDR range. */
export function isIpOrCidr(raw: string): boolean {
  const value = raw.trim();
  if (!value) return false;
  const slash = value.split('/');
  if (slash.length > 2) return false;
  const [address, prefix] = slash;
  if (isIPv4Address(address)) {
    if (prefix === undefined) return true;
    if (DECIMAL.test(prefix)) return Number(prefix) <= 32;
    const mask = ipv4ToInt(prefix);
    return mask !== null && isContiguousMask(mask);
  }
  if (isIPv6Address(address)) {
    if (prefix === undefined) return true;
    return DECIMAL.test(prefix) && Number(prefix) <= 128;
  }
  return false;
}
