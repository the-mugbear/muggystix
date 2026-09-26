/**
 * What is known about one service (v5.297.0): the host inspector's Services
 * section groups every piece of a host's evidence under the port it is about
 * — its weaknesses, what logged in, its web pages and paths, its scripts —
 * instead of one section per tool.  Pure, so the grouping is tested without
 * rendering.
 */
import type { HostVulnerability, NetexecResult, Port, WebInterface, WebPath } from '../services/api';
import { SEVERITY_RANK } from './severity';

export interface ServiceEvidence {
  weaknesses: HostVulnerability[];
  access: NetexecResult[];
  web: WebInterface[];
  paths: WebPath[];
}

export interface HostEvidence {
  vulnerabilities: HostVulnerability[];
  netexec: NetexecResult[];
  web: WebInterface[];
  paths: WebPath[];
}

const rank = (v: HostVulnerability) => SEVERITY_RANK[(v.severity ?? '').toLowerCase()] ?? 9;

/** One row per ISSUE, like the Weaknesses section (the backend's
 *  ``issue_key``; the title when absent), keeping its worst-rated row: two
 *  scanners — or one scanner twice — reporting the same issue on a port is
 *  one weakness, not two lines. */
export function oneRowPerIssue(rows: HostVulnerability[]): HostVulnerability[] {
  const byIssue = new Map<string, HostVulnerability>();
  for (const v of [...rows].sort((a, b) => rank(a) - rank(b))) {
    const key = v.issue_key || `title:${(v.title ?? '').trim().toLowerCase()}`;
    if (!byIssue.has(key)) byIssue.set(key, v);
  }
  return [...byIssue.values()];
}

/** A port's share of the host's evidence.  A web interface is the port's by
 *  ``port_id``, or by number when the web tool created no port row. */
export function evidenceForPort(port: Port, all: HostEvidence): ServiceEvidence {
  return {
    weaknesses: oneRowPerIssue(all.vulnerabilities.filter((v) => v.port_id === port.id)),
    access: all.netexec.filter((r) => r.port === port.port_number),
    web: all.web.filter((w) => w.port_id === port.id || (w.port_id == null && w.port === port.port_number)),
    paths: all.paths.filter((p) => p.port === port.port_number),
  };
}

/** Evidence that belongs to no OPEN port: its port is closed or filtered now,
 *  is not in the host's port list (a web tool reached it; nothing scanned
 *  it), or it names no port.  One group per port number, the port row (and
 *  its state) when the host has one. */
export interface UnplacedGroup {
  portNumber: number | null;
  /** The host's row for this number, when it has one (a closed / filtered port). */
  port: Port | null;
  evidence: ServiceEvidence;
}

/** v5.299.0 — the Services table shows evidence under open ports only, so a
 *  web page on a port nothing inventoried, or on a port since closed, was
 *  loaded and never shown.  Weaknesses are left out: the Weaknesses section
 *  lists every one on the host. */
export function unplacedEvidence(openPorts: Port[], allPorts: Port[], all: HostEvidence): UnplacedGroup[] {
  const openIds = new Set(openPorts.map((p) => p.id));
  const openNumbers = new Set(openPorts.map((p) => p.port_number));
  const groups = new Map<number | null, UnplacedGroup>();
  const group = (n: number | null | undefined): UnplacedGroup => {
    const key = n ?? null;
    let g = groups.get(key);
    if (!g) {
      g = {
        portNumber: key,
        port: key == null ? null : allPorts.find((p) => p.port_number === key && !openIds.has(p.id)) ?? null,
        evidence: { weaknesses: [], access: [], web: [], paths: [] },
      };
      groups.set(key, g);
    }
    return g;
  };
  for (const r of all.netexec) {
    if (r.port == null || !openNumbers.has(r.port)) group(r.port).evidence.access.push(r);
  }
  for (const w of all.web) {
    const placed = w.port_id != null ? openIds.has(w.port_id) : w.port != null && openNumbers.has(w.port);
    if (!placed) group(w.port).evidence.web.push(w);
  }
  for (const p of all.paths) {
    if (p.port == null || !openNumbers.has(p.port)) group(p.port).evidence.paths.push(p);
  }
  // Numbered ports in order, "no port" last.
  return [...groups.values()].sort((a, b) =>
    (a.portNumber ?? Number.MAX_SAFE_INTEGER) - (b.portNumber ?? Number.MAX_SAFE_INTEGER));
}

/** The worst severity among a service's weaknesses and how many share it. */
export function worstSeverity(weaknesses: HostVulnerability[]): { severity: string; count: number } | null {
  if (weaknesses.length === 0) return null;
  const worst = (weaknesses[0].severity ?? 'unknown').toLowerCase();
  return { severity: worst, count: weaknesses.filter((v) => (v.severity ?? 'unknown').toLowerCase() === worst).length };
}

export interface AccessSummary<T> {
  /** Logins that worked (null / guest / anonymous included). */
  worked: T[];
  failed: T[];
  /** Banners, flags, action lines — results that are not a login. */
  other: T[];
}

export function summariseAccess<T extends { auth_success?: boolean | null }>(rows: T[]): AccessSummary<T> {
  return {
    worked: rows.filter((r) => r.auth_success === true),
    failed: rows.filter((r) => r.auth_success === false),
    other: rows.filter((r) => r.auth_success == null),
  };
}
