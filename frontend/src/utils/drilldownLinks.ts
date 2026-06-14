/**
 * Drill-down link builder — the ONE place a dashboard metric becomes a filtered
 * /hosts or /findings URL.
 *
 * Centralising this is the §26 drill-down contract: every summary surface uses
 * the SAME param names the destination pages read, so a displayed count and the
 * list it opens reconcile (no "click 12 critical → land on an unfiltered page").
 * Builders emit plain in-app paths — render them with react-router <Link> so
 * modifier-click / history / link-preview behave like ordinary links.
 *
 * Hosts params mirror the whitelist Hosts.tsx restores from the URL; findings
 * params mirror what Findings.tsx restores. The `q` escape hatch carries a raw
 * DSL predicate for conditions that have no structured param (e.g. follow:none).
 */
import type { FindingSeverity, FindingSource, FindingStatus } from '../services/api';

export type HostSeverity = 'critical' | 'high' | 'medium' | 'low';

export interface HostsUrlParams {
  /** Site name (Hosts accepts a comma list). */
  sites?: string;
  /** Subnet CIDR. */
  subnets?: string;
  scanIds?: number | string;
  /** Raw DSL predicate — the escape hatch for conditions with no param. */
  q?: string;
  assignedTo?: 'me';
  outOfScopeOnly?: boolean;
  hasOpenPorts?: boolean;
  hasTestExecution?: boolean;
  /** Shorthand for has_<sev>_vulns=true (host has ≥1 vuln of this severity). */
  severity?: HostSeverity;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
}

/** Build a /hosts URL the Hosts page can restore its filters from. */
export const buildHostsUrl = (params: HostsUrlParams): string => {
  const sp = new URLSearchParams();
  if (params.sites) sp.set('sites', params.sites);
  if (params.subnets) sp.set('subnets', params.subnets);
  if (params.scanIds != null) sp.set('scan_ids', String(params.scanIds));
  if (params.q) sp.set('q', params.q);
  if (params.assignedTo) sp.set('assigned_to', params.assignedTo);
  if (params.outOfScopeOnly) sp.set('out_of_scope_only', 'true');
  if (params.hasOpenPorts) sp.set('has_open_ports', 'true');
  if (params.hasTestExecution) sp.set('has_test_execution', 'true');
  if (params.severity) sp.set(`has_${params.severity}_vulns`, 'true');
  if (params.sortBy) sp.set('sort_by', params.sortBy);
  if (params.sortOrder) sp.set('sort_order', params.sortOrder);
  const qs = sp.toString();
  return qs ? `/hosts?${qs}` : '/hosts';
};

/** /hosts filtered to reviewed / unreviewed hosts (team-wide review state). */
export const reviewedHostsUrl = (reviewed: boolean): string =>
  buildHostsUrl({ q: reviewed ? 'follow:reviewed' : 'follow:none' });

/**
 * Findings status filter — the real statuses plus two server-side groups that
 * the posture/disposition surfaces count against:
 *   active   = open | confirmed | retest
 *   resolved = remediated | false_positive | accepted_risk
 */
export type FindingStatusFilter = FindingStatus | 'active' | 'resolved';

export interface FindingsUrlParams {
  status?: FindingStatusFilter;
  severity?: FindingSeverity;
  source?: FindingSource;
  /** Owner facet: the caller, or findings with no owner. */
  owner?: 'me' | 'unowned';
}

/** Build a /findings URL the Findings page can restore its filters from. */
export const buildFindingsUrl = (params: FindingsUrlParams): string => {
  const sp = new URLSearchParams();
  if (params.status) sp.set('status', params.status);
  if (params.severity) sp.set('severity', params.severity);
  if (params.source) sp.set('source', params.source);
  if (params.owner) sp.set('owner', params.owner);
  const qs = sp.toString();
  return qs ? `/findings?${qs}` : '/findings';
};
