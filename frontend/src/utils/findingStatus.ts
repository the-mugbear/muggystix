/**
 * Shared finding-status UI constants.
 *
 * Single source for the status labels AND the terminal-disposition policy so
 * the /findings list and the finding detail page can't drift — both prompt
 * for a "why" summary on the same set of terminal moves.
 */
import { FindingStatus } from '../services/api';

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
