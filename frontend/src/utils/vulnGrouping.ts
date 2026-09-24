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

/**
 * One *distinct detail block* to render — a representative row plus the ports it
 * covers. A scanner that reports the same plugin on several ports produces one
 * row per port, all carrying the SAME description / solution / references; those
 * collapse to a single detail here (the ports are listed once) instead of
 * repeating the whole write-up per port. Genuinely different plugins that share
 * a CVE stay as separate detail blocks — their descriptions differ.
 */
export interface VulnDetailRow {
  vuln: HostVulnerability;
  ports: number[];
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
  /** Deduplicated detail blocks to render — members that differ only by port
   *  are collapsed to one, so a multi-port plugin shows its description once. */
  detailRows: VulnDetailRow[];
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
  /** Distinct ports carrying an *exploitable* member, ascending. Drives the
   *  exploit-on-port pivot — a subset of `ports` (empty when the exploitable
   *  finding is host-level, or nothing is exploitable). Must be every such
   *  port, not one representative's: a plugin exploitable on 80/443/8080 has to
   *  pivot on all three, or the button silently queries one arbitrary port. */
  exploitPorts: number[];
  /** Most CVEs any one member names (a Nessus plugin often names twenty,
   *  while `cveId` shows only the first). 0 when not known. */
  cveCount: number;
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
  // Prefer the key the BACKEND computed. It is the same value the Finding
  // spine dedups on, so grouping by it guarantees the UI never claims a merge
  // the database wouldn't make. The local derivation below is the fallback for
  // payloads predating the field.
  const served = v.issue_key?.trim();
  if (served) {
    return {
      key: served,
      kind: served.startsWith('cve:') ? 'cve' : 'title',
    };
  }
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

    // Ports whose member is itself exploitable — the exploit-on-port pivot must
    // target every one of these, not the single port of members[0].
    const exploitPorts = [
      ...new Set(
        ordered
          .filter((m) => m.exploitable === true)
          .map((m) => m.port_number)
          .filter((p): p is number => typeof p === 'number'),
      ),
    ].sort((a, b) => a - b);

    // Collapse rows that are the same detail on different ports. Key on
    // (scanner, plugin) — the same plugin_id has identical description/solution,
    // so re-rendering it per port is pure repetition; different plugins (even
    // sharing a CVE) keep distinct blocks because their write-ups differ. The
    // representative prefers a finding-covered row so the badge/promote state is
    // accurate across the collapsed ports.
    const detailBuckets = new Map<string, { vuln: HostVulnerability; ports: Set<number> }>();
    ordered.forEach((m) => {
      const source = (m.source ?? 'unknown').toLowerCase();
      const ident = m.plugin_id?.trim()
        || (m.title ? normalizeVulnTitle(m.title) : `row:${m.id}`);
      const dk = `${source} ${ident}`;
      const bucket = detailBuckets.get(dk);
      if (bucket) {
        if (typeof m.port_number === 'number') bucket.ports.add(m.port_number);
        if (!bucket.vuln.finding_id && m.finding_id) bucket.vuln = m;
      } else {
        detailBuckets.set(dk, {
          vuln: m,
          ports: new Set(typeof m.port_number === 'number' ? [m.port_number] : []),
        });
      }
    });
    const detailRows: VulnDetailRow[] = [...detailBuckets.values()].map((b) => ({
      vuln: b.vuln,
      ports: [...b.ports].sort((a, c) => a - c),
    }));

    groups.push({
      key,
      keyKind: kind,
      cveId: worst.cve_id?.trim() ? worst.cve_id.trim().toUpperCase() : null,
      title: worst.title || worst.plugin_id || 'Unnamed finding',
      members: ordered,
      detailRows,
      reports,
      severity: (worst.severity ?? 'unknown').toLowerCase(),
      severityDisagreement: new Set(reports.map((r) => r.severity)).size > 1,
      exploitable: ordered.some((m) => m.exploitable === true),
      ports,
      exploitPorts,
      cveCount: Math.max(0, ...ordered.map((m) => m.cve_count ?? 0)),
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

/**
 * v5.292.0 — several ISSUES about one product on the same ports, folded into
 * one line.
 *
 * Nessus checks an outdated product once per advisory range: a Tomcat 9.0.13
 * install produced a dozen "Apache Tomcat 9.0.x < 9.0.y" critical rows, each a
 * separate issue, and they buried everything else on the host. They share one
 * cause and usually one fix.
 *
 * The key is STRUCTURAL — the scanner's CPE (`a:apache:tomcat`) plus the ports
 * — never the titles: a wrong merge hides a finding (see the note at the top of
 * this file). Titles are used only to NAME the group. Issue identity is
 * untouched: every issue inside keeps its own row, finding and actions.
 */
export interface ProductGroup {
  /** Stable key for React and expand state. */
  key: string;
  cpe: string;
  /** "Apache Tomcat" — the members' shared title prefix, else from the CPE. */
  product: string;
  /** Installed version when every member that names one agrees. */
  installedVersion: string | null;
  /** The one version that satisfies every member's fix — only when EVERY
   *  member names a comparable fixed version, so the claim is never wrong. */
  closingVersion: string | null;
  groups: VulnGroup[];
  severity: string;
  ports: number[];
  exploitPorts: number[];
  /** Issues inside with an exploit. */
  exploitableCount: number;
  /** Distinct scanner sources across the members (lower-cased). */
  sources: string[];
}

export type ObservationItem =
  | { kind: 'issue'; group: VulnGroup }
  | { kind: 'product'; product: ProductGroup };

/** The one CPE every member naming one agrees on, or null. */
const groupCpe = (g: VulnGroup): string | null => {
  const cpes = new Set(g.members.map((m) => m.cpe?.trim().toLowerCase()).filter(Boolean) as string[]);
  return cpes.size === 1 ? [...cpes][0] : null;
};

const versionParts = (v: string): number[] | null => {
  const parts = v.match(/\d+/g);
  return parts ? parts.map(Number) : null;
};

const compareParts = (a: number[], b: number[]): number => {
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const d = (a[i] ?? 0) - (b[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
};

/**
 * The fixed version for one row, on the installed branch. Nessus lists one per
 * branch ("9.0.120 / 10.1.40"); the one sharing the installed major version is
 * the upgrade in question. Null when it cannot be decided.
 */
const fixForRow = (fixed: string | null | undefined, installed: string | null | undefined): string | null => {
  const candidates = (fixed ?? '').split(/\s*[/,]\s*|\s+or\s+/i).map((c) => c.trim()).filter((c) => versionParts(c));
  if (candidates.length === 0) return null;
  if (candidates.length === 1) return candidates[0];
  const major = installed ? versionParts(installed)?.[0] : undefined;
  const onBranch = candidates.filter((c) => versionParts(c)?.[0] === major);
  return onBranch.length === 1 ? onBranch[0] : null;
};

export const closingVersionFor = (members: HostVulnerability[]): string | null => {
  let best: string | null = null;
  for (const m of members) {
    const fix = fixForRow(m.fixed_version, m.installed_version);
    if (!fix) return null;
    if (!best || compareParts(versionParts(fix)!, versionParts(best)!) > 0) best = fix;
  }
  return best;
};

const titleCase = (s: string): string =>
  s.split(/[_\s]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');

/** Name the product: the words every member title starts with, stopping at
 *  the first version-like token; else vendor + product from the CPE. */
export const productLabel = (cpe: string, titles: string[]): string => {
  const split = titles.map((t) => t.trim().split(/\s+/));
  const prefix: string[] = [];
  for (let i = 0; split.length > 0 && i < split[0].length; i += 1) {
    const word = split[0][i];
    if (/\d/.test(word) || !split.every((w) => w[i]?.toLowerCase() === word.toLowerCase())) break;
    prefix.push(word);
  }
  if (prefix.length > 0) return prefix.join(' ');
  const [, vendor = '', product = ''] = cpe.split(':');
  const name = titleCase(product);
  return vendor && !product.toLowerCase().startsWith(vendor.toLowerCase())
    ? `${titleCase(vendor)} ${name}`
    : name || cpe;
};

const uniqSorted = (xs: number[]): number[] => [...new Set(xs)].sort((a, b) => a - b);

/**
 * Fold issue groups (already worst-first) into product groups where two or
 * more share a CPE and ports. A product group takes the place of its worst
 * issue, so the list stays worst-first; a lone issue is returned as itself.
 */
export function groupByProduct(groups: VulnGroup[]): ObservationItem[] {
  const byKey = new Map<string, VulnGroup[]>();
  const keyOf = new Map<VulnGroup, string>();
  groups.forEach((g) => {
    const cpe = groupCpe(g);
    if (!cpe) return;
    const key = `product:${cpe}@${g.ports.join(',')}`;
    keyOf.set(g, key);
    byKey.set(key, [...(byKey.get(key) ?? []), g]);
  });

  const emitted = new Set<string>();
  const items: ObservationItem[] = [];
  groups.forEach((g) => {
    const key = keyOf.get(g);
    const members = key ? byKey.get(key)! : null;
    if (!key || !members || members.length < 2) {
      items.push({ kind: 'issue', group: g });
      return;
    }
    if (emitted.has(key)) return;
    emitted.add(key);
    const rows = members.flatMap((m) => m.members);
    const installed = new Set(rows.map((r) => r.installed_version?.trim()).filter(Boolean) as string[]);
    items.push({
      kind: 'product',
      product: {
        key,
        cpe: groupCpe(members[0])!,
        product: productLabel(groupCpe(members[0])!, members.map((m) => m.title)),
        installedVersion: installed.size === 1 ? [...installed][0] : null,
        closingVersion: closingVersionFor(rows),
        groups: members,
        severity: members[0].severity,
        ports: members[0].ports,
        exploitPorts: uniqSorted(members.flatMap((m) => m.exploitPorts)),
        exploitableCount: members.filter((m) => m.exploitable).length,
        sources: [...new Set(rows.map((r) => (r.source ?? 'unknown').toLowerCase()))],
      },
    });
  });
  return items;
}
