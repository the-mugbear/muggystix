/**
 * "Since your last visit" as a change inbox (v5.242.0).
 *
 * The banner's counts were passive badges: "3 new critical" told the analyst
 * something had happened and left them to find it. Each count is now a link to
 * exactly the hosts it counted — the backend computes the counts and resolves
 * the DSL fields below from ONE window definition, over the same
 * (last_viewed_at, as_of] window, so a chip that says 12 opens 12.
 *
 * Pure: the page renders what this returns.
 */
import type { SinceLastVisit } from '../services/api';
import { buildHostsUrl } from './drilldownLinks';

export interface SinceChip {
  key: 'scans' | 'new-hosts' | 'changed-hosts' | 'critical' | 'high';
  label: string;
  tone: 'info' | 'secondary' | 'destructive' | 'warning';
  /** Where the counted records are; absent when there is nothing to open. */
  href?: string;
  /** What the link lists, for the tooltip / accessible name. */
  hint: string;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The window the counts were taken over, as a DSL value; null on a first
 *  visit (no cursor) or from a backend that predates `as_of`. */
export const sinceWindow = (since: SinceLastVisit): string | null =>
  since.last_viewed_at && since.as_of ? `${since.last_viewed_at}..${since.as_of}` : null;

export function sinceChips(since: SinceLastVisit): SinceChip[] {
  const w = sinceWindow(since);
  const hosts = (q: string) => (w ? buildHostsUrl({ q }) : undefined);
  const chips: SinceChip[] = [];

  if (since.new_scan_count > 0) {
    chips.push({
      key: 'scans', tone: 'info',
      label: plural(since.new_scan_count, 'new import', 'new imports'),
      href: '/scans',
      hint: 'Open the import history, newest first',
    });
  }
  if (since.new_host_count > 0) {
    chips.push({
      key: 'new-hosts', tone: 'secondary',
      label: plural(since.new_host_count, 'new host', 'new hosts'),
      href: hosts(`firstseen:"${w}"`),
      hint: 'Hosts first observed since your last visit',
    });
  }
  const changed = since.changed_host_count ?? 0;
  if (changed > 0) {
    chips.push({
      key: 'changed-hosts', tone: 'secondary',
      label: plural(changed, 'known host changed', 'known hosts changed'),
      href: hosts(`changedsince:"${w}"`),
      hint: 'Hosts you already had that gained a port or a scanner observation',
    });
  }
  // Scanner observations, not findings: nothing here has been judged. The
  // count is observations; the link lists the hosts carrying them, so the
  // chip says both rather than letting "3" open a list of 2.
  const observed = (n: number, hostCount: number | undefined, sev: 'critical' | 'high', key: SinceChip['key'], tone: SinceChip['tone']) => {
    if (n <= 0) return;
    chips.push({
      key, tone,
      label: `${plural(n, `new ${sev} observation`, `new ${sev} observations`)}`
        + (hostCount != null && hostCount > 0 ? ` · ${plural(hostCount, 'host', 'hosts')}` : ''),
      href: hosts(`vulnsince:"${sev}@${w}"`),
      hint: `Hosts with a ${sev} scanner observation recorded since your last visit — not yet judged`,
    });
  };
  observed(since.new_critical_findings, since.new_critical_hosts, 'critical', 'critical', 'destructive');
  observed(since.new_high_findings, since.new_high_hosts, 'high', 'high', 'warning');

  return chips;
}
