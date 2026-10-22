/**
 * "Is this port a web service, and http or https?" — the ONE answer on the
 * frontend (review 2026-10-10, R1d).  The Scan page, the host inspector's web
 * links and the connection helpers each had their own port set, so a service
 * on 3000 linked on one page and not on the other.
 *
 * It is the server's rule (`scope_targets_service.web_targets_from_hosts`,
 * what `web-targets.txt` exports): a port whose service is named as HTTP is a
 * web port, https when TLS-wrapped (`service_tunnel: "ssl"`) or named so; the
 * common-port table only when nothing identified the service (no name,
 * "unknown", or nmap's guess from the port number, `service_method:
 * "table"`).  TLS alone never makes one (imaps, ldaps), nor does a port number
 * under a service identified as something else (ssh on 443).
 * `tests/utils/webPort.test.ts` pins `WEB_PORT_SCHEMES` to the server's table.
 */

export type WebScheme = 'http' | 'https';

export interface WebPortLike {
  port_number: number;
  service_name?: string | null;
  service_tunnel?: string | null;
  service_method?: string | null;
}

/** The server's `_WEB_PORT_SCHEMES`: the fallback for unidentified ports. */
export const WEB_PORT_SCHEMES: Readonly<Record<number, WebScheme>> = {
  80: 'http', 8080: 'http', 8000: 'http', 81: 'http',
  443: 'https', 8443: 'https', 4443: 'https',
};

const UNIDENTIFIED = new Set(['', 'unknown']);

/** `http` / `https` for a web port, `null` for anything else. */
export const webScheme = (port: WebPortLike): WebScheme | null => {
  const svc = (port.service_name ?? '').trim().toLowerCase();
  const tunnel = (port.service_tunnel ?? '').toLowerCase();
  const method = (port.service_method ?? '').toLowerCase();
  if (svc.includes('http')) {
    return tunnel === 'ssl' || svc.includes('https') ? 'https' : 'http';
  }
  if (UNIDENTIFIED.has(svc) || method === 'table') {
    return WEB_PORT_SCHEMES[port.port_number] ?? null;
  }
  return null;
};
