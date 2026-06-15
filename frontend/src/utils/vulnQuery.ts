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
