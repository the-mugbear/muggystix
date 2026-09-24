/**
 * Why a host row matched an endpoint condition.
 *
 * With "Endpoint: service ftp" applied, the Hosts list showed 157 hosts and no
 * row said FTP: the Exposure column shows the host's risk-ranked services
 * (Telnet, RDP, SSH…), and FTP is not one of those.  This module re-applies
 * the ACTIVE port / service / version conditions to the ports each list row
 * already carries (the list loads every port of every host on the page), so a
 * row can say "ftp 21/tcp" — no extra request, no per-row query.
 *
 * The rules mirror the backend predicates (`host_query_predicates`):
 *   - structured filter (`ports` / `services` / `portStates` / `hasOpenPorts`):
 *     ONE port row must satisfy every dimension (`port_match_subquery`);
 *     service = case-insensitive substring of `service_name`;
 *   - query `port:` = port number, any state (`port_predicate`);
 *   - query `service:` / `svc:` = substring of `service_name` (`service_predicate`);
 *   - query `version:` / `product:` = an OPEN port whose product, version or
 *     "product version" contains the value (`version_predicate`).
 * A negated query term (`NOT service:ftp`, or inside `NOT (…)`) never marks a
 * port as matched.  Port state alone ("has open ports") is not shown: every
 * open port would match, which says nothing.
 */

export interface EndpointMatchFilters {
  ports?: string[];
  services?: string[];
  portStates?: string[];
  hasOpenPorts?: boolean;
  query?: string;
}

export interface MatchablePort {
  port_number: number;
  protocol?: string | null;
  state: string | null;
  service_name?: string | null;
  service_product?: string | null;
  service_version?: string | null;
}

type Matcher = (p: MatchablePort) => boolean;

export interface EndpointMatchCriteria {
  matchers: Matcher[];
  /** Whether a `version:` term is active — the match then names the product. */
  byProduct: boolean;
}

export interface MatchedEndpoint {
  key: string;
  /** "ftp 21/tcp", or "vsftpd 3.0.3 21/tcp" for a product match. */
  label: string;
  /** The port state when it is not open ("filtered"), else null. */
  state: string | null;
}

const contains = (haystack: string | null | undefined, needle: string) =>
  !!haystack && haystack.toLowerCase().includes(needle.toLowerCase());

// --- A tiny read of the query DSL: only the positive port/service/version leaves.

const FIELD_ALIASES: Record<string, 'port' | 'service' | 'version'> = {
  port: 'port',
  service: 'service',
  svc: 'service',
  version: 'version',
  product: 'version',
};

type Tok = { kind: 'open' | 'close' | 'word'; text: string };

function tokenize(q: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  while (i < q.length) {
    const ch = q[i];
    if (/\s/.test(ch)) { i += 1; continue; }
    if (ch === '(' || ch === ')') { toks.push({ kind: ch === '(' ? 'open' : 'close', text: ch }); i += 1; continue; }
    // A word runs to whitespace or a paren; a quoted run inside it may hold both.
    let text = '';
    while (i < q.length && !/\s/.test(q[i]) && q[i] !== '(' && q[i] !== ')') {
      if (q[i] === '"') {
        text += q[i];
        i += 1;
        while (i < q.length && q[i] !== '"') {
          if (q[i] === '\\' && i + 1 < q.length) { text += q[i] + q[i + 1]; i += 2; continue; }
          text += q[i];
          i += 1;
        }
        if (i < q.length) { text += q[i]; i += 1; }
        continue;
      }
      text += q[i];
      i += 1;
    }
    toks.push({ kind: 'word', text });
  }
  return toks;
}

function splitValues(raw: string): string[] {
  const out: string[] = [];
  let cur = '';
  let quoted = false;
  for (let i = 0; i < raw.length; i += 1) {
    const c = raw[i];
    if (c === '\\' && quoted && i + 1 < raw.length) { cur += raw[i + 1]; i += 1; continue; }
    if (c === '"') { quoted = !quoted; continue; }
    if (c === ',' && !quoted) { out.push(cur); cur = ''; continue; }
    cur += c;
  }
  out.push(cur);
  return out.map((v) => v.trim()).filter(Boolean);
}

/** Positive port/service/version leaves of a host query. */
export function positiveEndpointTerms(query: string): Array<{ field: 'port' | 'service' | 'version'; values: string[] }> {
  const terms: Array<{ field: 'port' | 'service' | 'version'; values: string[] }> = [];
  const negStack: boolean[] = [false];
  let pendingNot = false;
  for (const tok of tokenize(query)) {
    const current = negStack[negStack.length - 1];
    if (tok.kind === 'open') { negStack.push(current !== pendingNot); pendingNot = false; continue; }
    if (tok.kind === 'close') { if (negStack.length > 1) negStack.pop(); pendingNot = false; continue; }
    const upper = tok.text.toUpperCase();
    if (upper === 'NOT') { pendingNot = !pendingNot; continue; }
    if (upper === 'AND' || upper === 'OR' || tok.text === ',') continue;
    const negated = current !== pendingNot;
    pendingNot = false;
    const colon = tok.text.indexOf(':');
    if (colon <= 0 || negated) continue;
    const field = FIELD_ALIASES[tok.text.slice(0, colon).toLowerCase()];
    if (!field) continue;
    const values = splitValues(tok.text.slice(colon + 1));
    if (values.length) terms.push({ field, values });
  }
  return terms;
}

/**
 * The active endpoint conditions as port matchers, or null when no port /
 * service / version condition is applied (the row then shows no match line).
 */
export function endpointMatchCriteria(filters: EndpointMatchFilters): EndpointMatchCriteria | null {
  const matchers: Matcher[] = [];
  let byProduct = false;

  const ports = (filters.ports ?? []).map((p) => Number(p)).filter((n) => Number.isInteger(n));
  const services = (filters.services ?? []).map((s) => s.trim()).filter(Boolean);
  const states = (filters.portStates ?? []).map((s) => s.toLowerCase());
  // "No recorded open ports" makes the backend ignore the other port filters.
  if ((ports.length || services.length) && filters.hasOpenPorts !== false) {
    const requireOpen = filters.hasOpenPorts === true;
    matchers.push((p) =>
      (!ports.length || ports.includes(p.port_number))
      && (!services.length || services.some((s) => contains(p.service_name, s)))
      && (!states.length || states.includes((p.state ?? '').toLowerCase()))
      && (!requireOpen || p.state === 'open'),
    );
  }

  if (filters.query?.trim()) {
    for (const term of positiveEndpointTerms(filters.query)) {
      if (term.field === 'port') {
        const nums = term.values.map(Number).filter((n) => Number.isInteger(n));
        if (nums.length) matchers.push((p) => nums.includes(p.port_number));
      } else if (term.field === 'service') {
        matchers.push((p) => term.values.some((v) => contains(p.service_name, v)));
      } else {
        byProduct = true;
        matchers.push((p) =>
          p.state === 'open'
          && term.values.some((v) =>
            contains(p.service_product, v)
            || contains(p.service_version, v)
            || contains(`${p.service_product ?? ''} ${p.service_version ?? ''}`, v)),
        );
      }
    }
  }

  return matchers.length ? { matchers, byProduct } : null;
}

/** The host's ports that satisfy any active endpoint condition, open ones first. */
export function matchedEndpoints(
  ports: MatchablePort[] | null | undefined,
  criteria: EndpointMatchCriteria | null,
): MatchedEndpoint[] {
  if (!criteria) return [];
  const hits = (ports ?? []).filter((p) => criteria.matchers.some((m) => m(p)));
  hits.sort((a, b) => Number(b.state === 'open') - Number(a.state === 'open') || a.port_number - b.port_number);
  const seen = new Set<string>();
  const out: MatchedEndpoint[] = [];
  for (const p of hits) {
    const proto = (p.protocol || 'tcp').toLowerCase();
    const key = `${p.port_number}/${proto}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const product = [p.service_product, p.service_version].filter(Boolean).join(' ');
    const name = (criteria.byProduct && product) || p.service_name || 'port';
    out.push({ key, label: `${name} ${key}`, state: p.state && p.state !== 'open' ? p.state : null });
  }
  return out;
}
