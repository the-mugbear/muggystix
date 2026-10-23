/**
 * Why a host row matched an endpoint condition.
 *
 * With "Endpoint: service ftp" applied, the Hosts list showed 157 hosts and no
 * row said FTP: the Exposure column shows the host's risk-ranked services
 * (Telnet, RDP, SSH…), and FTP is not one of those.  So a row names the
 * port(s) the active port / service / version conditions matched — "ftp
 * 21/tcp".
 *
 * WHICH ports matched is the server's answer, from the filter's own
 * conditions (`GET /hosts` → each row's `matched_port_ids`, the answer's
 * `match_by_product`; `host_query.matched_port_condition`).  This module used
 * to re-read the query language itself to decide it — a second lexer of the
 * Hosts DSL, which already read `port: 22` and `service:"ssh"@closed`
 * differently from the server (review 2026-10-23, R-FE2).  What is left here
 * is the row's wording: open ports first, one entry per port/protocol, the
 * product when a `version:` condition matched, the state when not open.
 */

/** The explicit "every state" value (structured `portStates`, query `@any`). */
export const PORT_STATE_ANY = 'any';

export interface MatchablePort {
  id: number;
  port_number: number;
  protocol?: string | null;
  state: string | null;
  service_name?: string | null;
  service_product?: string | null;
  service_version?: string | null;
}

export interface MatchedEndpoint {
  key: string;
  /** "ftp 21/tcp", or "vsftpd 3.0.3 21/tcp" for a product match. */
  label: string;
  /** The port state when it is not open ("filtered"), else null. */
  state: string | null;
}

/** The row's ports the server marked as matched, open ones first. */
export function matchedEndpoints(
  ports: MatchablePort[] | null | undefined,
  matchedIds: number[] | null | undefined,
  byProduct = false,
): MatchedEndpoint[] {
  if (!matchedIds?.length) return [];
  const wanted = new Set(matchedIds);
  const hits = (ports ?? []).filter((p) => wanted.has(p.id));
  hits.sort((a, b) => Number(b.state === 'open') - Number(a.state === 'open') || a.port_number - b.port_number);
  const seen = new Set<string>();
  const out: MatchedEndpoint[] = [];
  for (const p of hits) {
    const proto = (p.protocol || 'tcp').toLowerCase();
    const key = `${p.port_number}/${proto}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const product = [p.service_product, p.service_version].filter(Boolean).join(' ');
    const name = (byProduct && product) || p.service_name || 'port';
    out.push({ key, label: `${name} ${key}`, state: p.state && p.state !== 'open' ? p.state : null });
  }
  return out;
}
