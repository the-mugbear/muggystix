/**
 * Vulnerability → host-query DSL predicate.
 *
 * Powers the "find other hosts with this vulnerability" pivot on a host's vuln
 * rows. Prefer the CVE (the canonical cross-host identifier — server-side
 * `cve:` matches `Vulnerability.cve_id`); fall back to the finding title
 * (`vuln:` matches `Vulnerability.title`) for plugin-only findings with no CVE.
 * Both are project-scoped ILIKE matches in the backend predicate layer.
 *
 * Returns null when neither a CVE nor a title is available to match on (the
 * affordance is then hidden).
 *
 * `\` and `"` are escaped for the DSL's quoted-string lexer, which treats only
 * \" and \\ as meaningful escapes — so an un-escaped quote in a title would
 * otherwise terminate the string early and corrupt the query.
 */
import type { HostVulnerability } from '../services/api/hosts';

export const buildSameVulnQuery = (vuln: HostVulnerability): string | null => {
  const escape = (s: string) => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const cve = vuln.cve_id?.trim();
  if (cve) return `cve:"${escape(cve)}"`;
  const title = vuln.title?.trim();
  if (title) return `vuln:"${escape(title)}"`;
  return null;
};

/**
 * Vulnerability group → "hosts with an exploit ON THESE PORTS" host-query DSL
 * predicate.
 *
 * Powers the port-scoped exploit pivot on a host's grouped vuln rows. The
 * backend `exploitport:` field correlates exploitability and port on the SAME
 * finding (not `port:X AND has:exploit`, which matches a host with X open and an
 * exploit on any other port), and accepts a comma list as OR-within-field.
 *
 * Takes EVERY port that carries an exploitable member of the group — a plugin
 * exploitable on 80/443/8080 pivots on all three. Previously this read one
 * representative row's single port, so a multi-port finding queried an arbitrary
 * one and silently missed the rest. Returns null when the group has no
 * exploitable-and-ported member (the pivot is then hidden). Values are bare port
 * numbers, so no DSL-string escaping is needed.
 */
export const buildExploitOnPortsQuery = (ports: number[]): string | null => {
  const unique = [...new Set(ports.filter((p) => Number.isInteger(p)))].sort((a, b) => a - b);
  return unique.length ? `exploitport:${unique.join(',')}` : null;
};
