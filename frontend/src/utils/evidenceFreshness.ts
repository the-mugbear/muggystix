/**
 * Evidence freshness beside the assertion (v5.224.0; design review item 4).
 *
 * The inspector used to show one "last seen" for the host, which made every
 * fact about it look as current as the newest scan.  These helpers put a
 * date, or an honest "not assessed", next to each kind of evidence.
 */
import type { HostAssessment, Port } from '../services/api';
import { formatRelativeTime } from './relativeTime';

const SAME_SWEEP_MS = 60 * 60 * 1000;

const ts = (iso: string | null | undefined): number => {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : t;
};

export const ago = (iso: string | null | undefined): string | null =>
  formatRelativeTime(iso, { fallback: null });

export interface PortFreshness {
  /** "3d ago" for the port's own last observation, or null when unknown. */
  seen: string | null;
  /** True when the host was observed more recently than this port, so "open"
   *  is older than the host's date suggests.  This is inferred from two
   *  timestamps and proves nothing about the port: the newer observation may
   *  be a DNS import, a web probe of another port, an SMB run — none of which
   *  looked here.  Say "not revalidated", never "closed" or "not seen". */
  notRevalidated: boolean;
}

export const NOT_REVALIDATED_LABEL = 'not revalidated';
export const NOT_REVALIDATED_TITLE =
  'Newer evidence exists for this host, but none of it re-observed this port. '
  + 'That does not mean the port was checked and found closed — the newer scan may not have probed it.';

export const portFreshness = (
  port: { last_seen?: string | null; first_seen?: string | null },
  hostLastSeen: string | null | undefined,
): PortFreshness => {
  const portSeen = ts(port.last_seen ?? port.first_seen);
  const hostSeen = ts(hostLastSeen);
  return {
    seen: ago(port.last_seen ?? port.first_seen),
    notRevalidated: portSeen > 0 && hostSeen > 0 && portSeen < hostSeen - SAME_SWEEP_MS,
  };
};

export interface FreshnessFact {
  key: string;
  label: string;
  /** What to print after the label. */
  value: string;
  /** How to read it: current, a gap, or not applicable. */
  tone: 'ok' | 'gap' | 'na' | 'warn';
  title: string;
}

/** The at-a-glance freshness line: one fact per assessment domain. */
export const freshnessFacts = (a: HostAssessment): FreshnessFact[] => {
  const facts: FreshnessFact[] = [];
  facts.push({
    key: 'observed',
    label: 'Observed',
    value: ago(a.last_observed_at) ?? 'never',
    tone: a.last_observed_at ? 'ok' : 'gap',
    title: 'The newest scan that saw this host at all. It says nothing about which checks that scan ran.',
  });
  facts.push(
    a.vuln_assessed
      ? {
          key: 'vulns',
          label: 'Vulnerabilities',
          value: ago(a.last_vuln_assessed_at) ?? 'assessed',
          tone: 'ok',
          title: 'Newest vulnerability observation on this host.',
        }
      : {
          key: 'vulns',
          label: 'Vulnerabilities',
          value: 'not assessed',
          tone: 'gap',
          title: 'No vulnerability scanner has reported on this host. "No findings" here means "not looked", not "clean".',
        },
  );
  if (!a.web_eligible) {
    facts.push({ key: 'web', label: 'Web / TLS', value: 'n/a', tone: 'na', title: 'No web port open on this host.' });
  } else if (a.web_assessed) {
    facts.push({
      key: 'web',
      label: 'Web / TLS',
      value: ago(a.last_web_assessed_at) ?? 'assessed',
      tone: 'ok',
      title: 'Newest web-interface or certificate evidence on this host.',
    });
  } else {
    facts.push({
      key: 'web',
      label: 'Web / TLS',
      value: 'not assessed',
      tone: 'gap',
      title: 'A web port is open but no httpx / testssl / EyeWitness evidence has been recorded for it.',
    });
  }
  if (!a.auth_eligible) {
    facts.push({ key: 'auth', label: 'SMB / AD', value: 'n/a', tone: 'na', title: 'No SMB / LDAP / Kerberos port open on this host.' });
  } else {
    facts.push({
      key: 'auth',
      label: 'SMB / AD',
      value: a.auth_assessed ? 'assessed' : 'not assessed',
      tone: a.auth_assessed ? 'ok' : 'gap',
      title: a.auth_assessed
        ? 'SMB signing or NetExec enumeration evidence is recorded.'
        : 'An SMB / LDAP port is open but no signing or enumeration evidence has been recorded.',
    });
  }
  facts.push(
    a.tests_executed > 0
      ? {
          key: 'tested',
          label: 'Tested',
          value: ago(a.last_tested_at) ?? `${a.tests_executed} result${a.tests_executed === 1 ? '' : 's'}`,
          tone: 'ok',
          title: `${a.tests_executed} executed test result${a.tests_executed === 1 ? '' : 's'} on this host's plan entries.`,
        }
      : {
          key: 'tested',
          label: 'Tested',
          value: 'never',
          tone: 'gap',
          title: 'No test on a plan entry for this host has been executed.',
        },
  );
  if (a.conflicts > 0) {
    facts.push({
      key: 'conflicts',
      label: 'Conflicts',
      value: String(a.conflicts),
      tone: 'warn',
      title: 'Values scans disagree on (OS, state, service). Reconcile them before relying on either.',
    });
  }
  if (a.open_ports_not_in_latest_scan > 0) {
    facts.push({
      key: 'stale_ports',
      label: 'Open ports not in latest scan',
      value: String(a.open_ports_not_in_latest_scan),
      tone: 'warn',
      title: 'Open ports whose own last observation is older than the host\'s. The latest sweep did not see them open; they may be closed now, or that sweep may not have probed them.',
    });
  }
  return facts;
};

export type SinceReviewPort = Pick<Port, 'id' | 'port_number' | 'protocol'> & { first_seen?: string | null };

export interface SinceReview {
  /** Ports first observed after the review. */
  newPorts: SinceReviewPort[];
  /** Vulnerabilities first observed after the review. */
  newVulns: { id: number; title: string | null; severity?: string | null }[];
  /** The host was re-observed after the review but nothing new was recorded. */
  reobservedOnly: boolean;
}

export const changesSinceReview = (
  reviewedAt: string | null | undefined,
  hostLastSeen: string | null | undefined,
  ports: SinceReviewPort[],
  vulns: { id: number; title: string | null; severity?: string | null; first_seen?: string | null }[],
): SinceReview | null => {
  const r = ts(reviewedAt);
  if (!r) return null;
  const newPorts = ports.filter((p) => ts(p.first_seen) > r);
  const newVulns = vulns.filter((v) => ts(v.first_seen) > r).map(({ id, title, severity }) => ({ id, title, severity }));
  const reobserved = ts(hostLastSeen) > r;
  if (!newPorts.length && !newVulns.length && !reobserved) return null;
  return { newPorts, newVulns, reobservedOnly: reobserved && !newPorts.length && !newVulns.length };
};
