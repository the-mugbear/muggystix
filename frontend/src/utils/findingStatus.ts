/**
 * Shared finding-status UI constants.
 *
 * Single source for the status labels AND the terminal-disposition policy so
 * the /findings list and the finding detail page can't drift — both prompt
 * for a "why" summary on the same set of terminal moves.
 */
import { FindingHostStatus, FindingSource, FindingStatus } from '../services/api';

/**
 * Where a finding came from, as the pages say it (the Findings list's Source
 * filter, the finding page's Source line).  The keys are the STORED values and
 * stay as they are — rows hold them:
 *
 *   scanner    a promoted scanner observation
 *   execution  made from a test's result (evidence → finding).  The value's
 *              name dates from execution runs; a test and its evidence is
 *              what writes it today, so it reads "Test result".
 *   manual     written directly ("Add finding", an accepted proposal)
 *   note       made from a host note.  Nothing can make one any more (notes
 *              are discussion); findings made that way earlier keep the
 *              value, so the filter still offers it.
 */
export const SOURCE_LABEL: Record<FindingSource, string> = {
  scanner: 'Scanner',
  execution: 'Test result',
  manual: 'Manual',
  note: 'Note',
};
/** The Source filter's options, in the order shown. */
export const SOURCE_OPTIONS: FindingSource[] = ['scanner', 'execution', 'manual', 'note'];

export const STATUS_LABEL: Record<FindingStatus, string> = {
  open: 'Open',
  confirmed: 'Confirmed',
  false_positive: 'False positive',
  accepted_risk: 'Accepted risk',
  remediated: 'Remediated',
};

/**
 * Terminal dispositions — moving a finding here prompts for a "why" summary
 * that lands on the disposition-history trail as the audit rationale. The
 * summary is optional (the prompt offers "Save without a reason"), but the prompt itself always
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
 *                          (open; a retest is per host since 5.395.0)
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

export const populationOf = (status: FindingStatus): FindingPopulation =>
  status === 'confirmed' ? 'confirmed' : TERMINAL_STATUSES.has(status) ? 'closed' : 'investigating';

/**
 * The per-endpoint state on an affected host — separate from the finding's.
 *
 * v5.290.0 — the API value `open` reads "Still present" (the host still has
 * the issue), never "Open": beside a finding labelled Confirmed, "Open here"
 * read as the finding status Open.  Display only; the API values are unchanged.
 *
 * 5.395.0 — `retest` reads "Retest pending" (owner, 2026-10-14: the three
 * layers — the finding, each host, the contact's claim — each have their own
 * words; "Retest" was also a finding status).  A pending retest is closed
 * with its result ("Record retest…"), never by the picker alone.
 */
export const ENDPOINT_STATUS_LABEL: Record<FindingHostStatus, string> = {
  open: 'Still present',
  remediated: 'Remediated here',
  retest: 'Retest pending',
  // v5.238.0 — about this endpoint only; the finding's other hosts keep theirs.
  false_positive: 'False positive here',
};

/** The short form, where the context already says "on this endpoint": the
 *  filter chips, a network's counts, the state bar. */
export const ENDPOINT_STATUS_SHORT_LABEL: Record<FindingHostStatus, string> = {
  open: 'Still present',
  retest: 'Retest pending',
  remediated: 'Remediated',
  false_positive: 'False positive',
};

/** The Findings list's host filter (`host_state=`, 5.395.0): a finding with
 *  at least one host in this state.  "Awaiting a retest" is the project's
 *  list of pending retests. */
export const HOST_STATE_FILTER_LABEL: Record<FindingHostStatus, string> = {
  open: 'A host still present',
  retest: 'A host awaiting a retest',
  remediated: 'A host remediated',
  false_positive: 'A host false positive',
};

/** The read-only badge's tone for each endpoint state. */
export const ENDPOINT_STATUS_TONE: Record<FindingHostStatus, 'warning' | 'success' | 'outline' | 'info'> = {
  open: 'warning', remediated: 'success', false_positive: 'outline', retest: 'info',
};

/**
 * The same tones as fills, for the state bar.  False positive is hatched as
 * well as neutral, so it is told from remediated by more than its hue.
 */
export const ENDPOINT_STATUS_FILL: Record<FindingHostStatus, string> = {
  open: 'bg-warning',
  retest: 'bg-info',
  remediated: 'bg-success',
  false_positive:
    'bg-[repeating-linear-gradient(135deg,hsl(var(--muted-foreground))_0_2px,hsl(var(--muted-foreground)/0.25)_2px_5px)]',
};

/**
 * "Still present on 3 of 5 · 1 remediated · 1 awaiting a retest" — how the endpoints
 * stand, so a finding's status is never read as one state for every host.
 * Null when every endpoint is still present (nothing to add to the count).
 */
export const describeEndpointStates = (
  counts: Partial<Record<string, number>> | null | undefined,
  total: number,
): string | null => {
  if (!counts || total === 0) return null;
  const open = counts.open ?? 0;
  const remediated = counts.remediated ?? 0;
  const retest = counts.retest ?? 0;
  const falsePositive = counts.false_positive ?? 0;
  if (open === total) return null;
  const parts: string[] = [`still present on ${open} of ${total}`];
  if (remediated) parts.push(`${remediated} remediated`);
  if (retest) parts.push(`${retest} awaiting a retest`);
  if (falsePositive) parts.push(`${falsePositive} false positive there`);
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
