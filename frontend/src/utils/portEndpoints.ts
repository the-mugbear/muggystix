/**
 * The named endpoints behind one port (v5.236.0; design review 2026-09-19).
 *
 * One address:port can serve many websites.  The port table used to reduce
 * a port's web interfaces to a single TLS entry — the newest observation —
 * so on shared hosting one vhost's certificate expiry or weak-protocol flag
 * was shown as THE port's, and evidence from one website described another.
 *
 * An endpoint is (port, name): the name an interface answered as (`fqdn`),
 * else the host its URL targeted, else the bare address.  Within one
 * endpoint the newest observation wins; across endpoints nothing is merged.
 * Name/URL, source and observation time stay together so every fact can be
 * attributed.
 */
import type { WebInterface } from '../services/api';

export interface EndpointTls {
  cert_not_after?: string | null;
  cert_self_signed?: boolean | null;
  cert_subject_org?: string | null;
  tls_weak_protocol?: boolean | null;
}

export interface PortEndpoint {
  /** Stable within a port: the lower-cased name, or "" for the bare address. */
  key: string;
  /** The name it answers as; null = reached by address only. */
  name: string | null;
  url: string;
  source: string;
  last_seen: string | null;
  /** Null when this endpoint's newest observation carried no TLS evidence. */
  tls: EndpointTls | null;
}

export const EXPIRY_WARN_DAYS = 30;

export const daysUntil = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  return Math.round((t - Date.now()) / 86_400_000);
};

const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

/** The name an interface answers as, or null when it targeted an address. */
export const endpointNameOf = (w: Pick<WebInterface, 'fqdn' | 'url'>): string | null => {
  const fqdn = (w.fqdn ?? '').trim();
  if (fqdn) return fqdn;
  try {
    const host = new URL(w.url).hostname.replace(/^\[|\]$/g, '');
    if (!host || IPV4.test(host) || host.includes(':')) return null;
    return host;
  } catch {
    return null;
  }
};

const hasTlsSignal = (w: WebInterface): boolean =>
  w.cert_not_after != null || w.cert_self_signed != null
  || !!w.cert_subject_org || w.tls_weak_protocol === true;

const tsOf = (iso: string | null | undefined): number => {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : t;
};

/** Endpoints per `port_id`: named ones first (A→Z), the bare address last. */
export function endpointsByPort(interfaces: WebInterface[]): Map<number, PortEndpoint[]> {
  const byPort = new Map<number, Map<string, PortEndpoint & { _ts: number }>>();
  for (const w of interfaces) {
    if (w.port_id == null) continue;
    const name = endpointNameOf(w);
    const key = (name ?? '').toLowerCase();
    const ts = tsOf(w.last_seen);
    const forPort = byPort.get(w.port_id) ?? new Map<string, PortEndpoint & { _ts: number }>();
    const prev = forPort.get(key);
    if (!prev || ts >= prev._ts) {
      forPort.set(key, {
        _ts: ts,
        key,
        name,
        url: w.url,
        source: w.source,
        last_seen: w.last_seen ?? null,
        tls: hasTlsSignal(w)
          ? {
              cert_not_after: w.cert_not_after,
              cert_self_signed: w.cert_self_signed,
              cert_subject_org: w.cert_subject_org,
              tls_weak_protocol: w.tls_weak_protocol,
            }
          // A newer observation without TLS detail must not erase what an
          // older one of the SAME endpoint established.
          : prev?.tls ?? null,
      });
    }
    byPort.set(w.port_id, forPort);
  }
  const out = new Map<number, PortEndpoint[]>();
  byPort.forEach((forPort, portId) => {
    const list = Array.from(forPort.values()).map(({ _ts, ...rest }) => rest);
    list.sort((a, b) => {
      if (!a.name !== !b.name) return a.name ? -1 : 1;
      return a.key.localeCompare(b.key);
    });
    out.set(portId, list);
  });
  return out;
}

export interface EndpointTlsSummary {
  withTls: number;
  weak: number;
  selfSigned: number;
  expired: number;
  expiringSoon: number;
}

/** Counts across a port's endpoints — a roll-up of how many, never a merge of which. */
export function summariseEndpointTls(endpoints: PortEndpoint[]): EndpointTlsSummary {
  const s: EndpointTlsSummary = { withTls: 0, weak: 0, selfSigned: 0, expired: 0, expiringSoon: 0 };
  for (const e of endpoints) {
    if (!e.tls) continue;
    s.withTls += 1;
    if (e.tls.tls_weak_protocol === true) s.weak += 1;
    if (e.tls.cert_self_signed === true) s.selfSigned += 1;
    const d = daysUntil(e.tls.cert_not_after);
    if (d !== null && d < 0) s.expired += 1;
    else if (d !== null && d <= EXPIRY_WARN_DAYS) s.expiringSoon += 1;
  }
  return s;
}
