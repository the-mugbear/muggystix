/**
 * Shared finding-status UI constants.
 *
 * Single source for the status labels AND the terminal-disposition policy so
 * the /findings list and the finding detail page can't drift — both prompt
 * for a "why" summary on the same set of terminal moves.
 */
import { FindingHostStatus, FindingStatus } from '../services/api';

export const STATUS_LABEL: Record<FindingStatus, string> = {
  open: 'Open',
  confirmed: 'Confirmed',
  false_positive: 'False positive',
  accepted_risk: 'Accepted risk',
  remediated: 'Remediated',
  retest: 'Retest',
};

/**
 * Terminal dispositions — moving a finding here prompts for a "why" summary
 * that lands on the disposition-history trail as the audit rationale. The
 * summary is optional (the prompt offers Skip), but the prompt itself always
 * appears for these moves so the rationale is never silently lost.
 */
export const TERMINAL_STATUSES = new Set<FindingStatus>([
  'false_positive',
  'accepted_risk',
  'remediated',
]);

/**
 * v5.225.0 — one vocabulary for the three populations (design review item
 * 7), so no page has to explain which count it shows:
 *
 *   scanner observations   what tools reported, per host, not yet judged
 *                          (the inspector's card; the /scans contribution)
 *   under investigation    a promoted finding still being worked
 *                          (open / retest)
 *   confirmed              a finding an analyst validated
 *   closed                 false positive / accepted risk / remediated
 *
 * The transitions between them: Promote (observation → finding), Confirm,
 * and a terminal disposition with a justification.
 */
export type FindingPopulation = 'investigating' | 'confirmed' | 'closed';

export const POPULATION_LABEL: Record<FindingPopulation, string> = {
  investigating: 'Under investigation',
  confirmed: 'Confirmed',
  closed: 'Closed',
};

export const SCANNER_OBSERVATIONS_LABEL = 'Scanner observations';

export const populationOf = (status: FindingStatus): FindingPopulation =>
  status === 'confirmed' ? 'confirmed' : TERMINAL_STATUSES.has(status) ? 'closed' : 'investigating';

/** The per-endpoint state on an affected host — separate from the finding's. */
export const ENDPOINT_STATUS_LABEL: Record<FindingHostStatus, string> = {
  open: 'Open here',
  remediated: 'Remediated here',
  retest: 'Retest here',
};

/**
 * "Open on 3 of 5 · 1 remediated · 1 retest" — how the endpoints stand, so
 * a finding's status is never read as one state for every host.  Null when
 * every endpoint is open (nothing to add to the count).
 */
export const describeEndpointStates = (
  counts: Partial<Record<string, number>> | null | undefined,
  total: number,
): string | null => {
  if (!counts || total === 0) return null;
  const open = counts.open ?? 0;
  const remediated = counts.remediated ?? 0;
  const retest = counts.retest ?? 0;
  if (open === total) return null;
  const parts: string[] = [`open on ${open} of ${total}`];
  if (remediated) parts.push(`${remediated} remediated`);
  if (retest) parts.push(`${retest} retest`);
  return parts.join(' · ');
};

/**
 * Does a finding's status satisfy a list-filter value? The filter may be a real
 * status, the keyword 'all', or a group ('active' = working set / 'resolved' =
 * terminal). Mirrors the backend's `_apply_status_filter` so a list row drops
 * from view exactly when the server would have excluded it.
 */
export const matchesStatusFilter = (
  status: FindingStatus,
  filter: string,
): boolean => {
  if (filter === 'all') return true;
  if (filter === 'active') return !TERMINAL_STATUSES.has(status);
  if (filter === 'resolved') return TERMINAL_STATUSES.has(status);
  return status === filter;
};
