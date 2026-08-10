/**
 * Group a host's scanner vulnerabilities by the ISSUE, not by the scanner.
 *
 * Nessus and GreenBone/OpenVAS report the same underlying problem in their own
 * verbiage, with their own plugin ids and often their own severity. Rendered as
 * a flat list that reads as two unrelated findings, and the operator does the
 * correlation by eye — every time, on every host.
 *
 * Grouping by *source* would only organise that duplication into labelled
 * piles; the correlation work stays manual. Scanner is provenance, not
 * identity. So we key on what the issue actually is:
 *
 *   1. CVE — the one genuinely cross-vendor identifier. `cve_id` is populated
 *      by both parsers and indexed server-side; `vulnQuery.ts` already treats
 *      it as the canonical cross-host key, so this tiering matches the pivot
 *      the row already offers.
 *   2. Normalised title — for the large share of scanner output with no CVE
 *      (config checks, weak ciphers, missing headers, default creds).
 *
 * Both tiers are EXACT matches (the second after normalisation). There is
 * deliberately no fuzzy/similarity matching: in a security tool a wrong merge
 * hides a real finding, which is worse than showing a duplicate. The failure
 * mode of being too strict is the behaviour we already have — a group of one,
 * rendered exactly as before — so grouping can only improve on the status quo,
 * never regress it.
 *
 * Severity disagreement between scanners is surfaced rather than averaged
 * away: two tools agreeing raises confidence, and one calling it Critical
 * while another calls it Medium is worth a human look. That mirrors the
 * confidence service, which already flags hosts where scans disagree about an
 * attribute.
 */
import { SEVERITY_RANK } from './severity';
import type { HostVulnerability } from '../services/api/hosts';

/** How the members of a group were matched — drives how much we claim in the UI. */
export type VulnGroupKeyKind = 'cve' | 'title';

export interface VulnScannerReport {
  /** Lower-cased source key as stored (`nessus`, `openvas`, …). */
  source: string;
  severity: string;
  /** The individual rows this scanner contributed (usually one). */
  members: HostVulnerability[];
}

export interface VulnGroup {
  /** Stable key for React and for expand/collapse state. */
  key: string;
  keyKind: VulnGroupKeyKind;
  cveId: string | null;
  /** Representative title — taken from the worst-severity member. */
  title: string;
  /** Every underlying row, worst severity first. */
  members: HostVulnerability[];
  /** One entry per distinct scanner, with the severity that scanner assigned. */
  reports: VulnScannerReport[];
  /** Worst severity across members — what the group is sorted and badged by. */
  severity: string;
  /** True when scanners assigned different severities to the same issue. */
  severityDisagreement: boolean;
  /** True when any scanner flagged it exploitable. */
  exploitable: boolean;
  /** Distinct affected ports, ascending. Empty for host-level findings. */
  ports: number[];
}

const rank = (severity: string | null | undefined): number =>
  SEVERITY_RANK[(severity ?? 'unknown').toLowerCase()] ?? SEVERITY_RANK.unknown;

/**
 * Reduce a scanner title to a comparable form. Only removes noise that is
 * reliably meaningless — case, punctuation, whitespace, and the vendor
 * prefixes both tools bolt on. Anything that could carry meaning (version
 * numbers, CVE-less identifiers, port references) is preserved, because
 * stripping it would merge genuinely different findings.
 */
export const normalizeVulnTitle = (title: string): string =>
  title
    .toLowerCase()
    .replace(/^(nessus|openvas|greenbone|qualys)\s*[:\-–]\s*/i, '')
    .replace(/\s*\((?:nessus|openvas|greenbone)\)\s*$/i, '')
    .replace(/[^\w\s.]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

const toTime = (value: string | null | undefined): number => {
  if (!value) return 0;
  const t = new Date(value).getTime();
  return Number.isNaN(t) ? 0 : t;
};

/**
 * Identity key for one row. CVE wins; otherwise the normalised title. Rows
 * with neither fall back to their own id so they stay separate — an unnamed
 * finding tells us nothing about what it is, so merging it with another
 * unnamed finding would be a guess.
 */
const groupKeyFor = (v: HostVulnerability): { key: string; kind: VulnGroupKeyKind } | null => {
  const cve = v.cve_id?.trim();
  if (cve) return { key: `cve:${cve.toUpperCase()}`, kind: 'cve' };
  const title = v.title?.trim();
  if (title) {
    const norm = normalizeVulnTitle(title);
    if (norm) return { key: `title:${norm}`, kind: 'title' };
  }
  return null;
};

export function groupVulnerabilities(vulns: HostVulnerability[]): VulnGroup[] {
  const buckets = new Map<string, { kind: VulnGroupKeyKind; members: HostVulnerability[] }>();

  vulns.forEach((v) => {
    const id = groupKeyFor(v);
    // Ungroupable rows get a key of their own rather than being pooled
    // together — see groupKeyFor.
    const key = id ? id.key : `row:${v.id}`;
    const kind: VulnGroupKeyKind = id ? id.kind : 'title';
    const bucket = buckets.get(key);
    if (bucket) bucket.members.push(v);
    else buckets.set(key, { kind, members: [v] });
  });

  const groups: VulnGroup[] = [];
  buckets.forEach(({ kind, members }, key) => {
    const ordered = members.slice().sort((a, b) => {
      const r = rank(a.severity) - rank(b.severity);
      if (r !== 0) return r;
      const t = toTime(b.last_seen ?? b.first_seen) - toTime(a.last_seen ?? a.first_seen);
      if (t !== 0) return t;
      return b.id - a.id;
    });
    const worst = ordered[0];

    // One report per distinct scanner. A scanner that reported the same issue
    // twice (e.g. on two ports) contributes one entry with both members.
    const bySource = new Map<string, VulnScannerReport>();
    ordered.forEach((m) => {
      const source = (m.source ?? 'unknown').toLowerCase();
      const existing = bySource.get(source);
      if (existing) existing.members.push(m);
      else {
        bySource.set(source, {
          source,
          severity: (m.severity ?? 'unknown').toLowerCase(),
          members: [m],
        });
      }
    });
    const reports = [...bySource.values()].sort((a, b) => rank(a.severity) - rank(b.severity));

    const ports = [
      ...new Set(
        ordered
          .map((m) => m.port_number)
          .filter((p): p is number => typeof p === 'number'),
      ),
    ].sort((a, b) => a - b);

    groups.push({
      key,
      keyKind: kind,
      cveId: worst.cve_id?.trim() ? worst.cve_id.trim().toUpperCase() : null,
      title: worst.title || worst.plugin_id || 'Unnamed finding',
      members: ordered,
      reports,
      severity: (worst.severity ?? 'unknown').toLowerCase(),
      severityDisagreement: new Set(reports.map((r) => r.severity)).size > 1,
      exploitable: ordered.some((m) => m.exploitable === true),
      ports,
    });
  });

  // Same ordering the flat list used — worst first, then most recently seen —
  // so the change is a regrouping, not a resort.
  return groups.sort((a, b) => {
    const r = rank(a.severity) - rank(b.severity);
    if (r !== 0) return r;
    const t =
      toTime(b.members[0].last_seen ?? b.members[0].first_seen) -
      toTime(a.members[0].last_seen ?? a.members[0].first_seen);
    if (t !== 0) return t;
    return b.members[0].id - a.members[0].id;
  });
}
