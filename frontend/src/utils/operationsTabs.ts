/**
 * Operations' tabs (5.331.0): which exist, what each counts, which one opens.
 *
 * Pure — the page reads the workbench's counts and the URL, and these decide.
 * The client-free half of `components/operations/OperationsTabs`.
 */
import type { MyTaskReason, WorkbenchResponse } from '../services/api';

/** The tabs, in bar order. */
export const OPERATIONS_TABS = ['findings', 'hosts', 'tests', 'changed', 'pickup'] as const;
export type OperationsTab = (typeof OPERATIONS_TABS)[number];

export const OPERATIONS_TAB_LABEL: Record<OperationsTab, string> = {
  findings: 'Findings',
  hosts: 'Hosts',
  tests: 'Tests',
  changed: 'Changed since review',
  pickup: 'Pick up',
};

/** Rows per page, on every tab. */
export const OPERATIONS_PAGE_SIZE = 25;

/** The three kinds of test the Tests tab lists — a test is under ONE, its
 *  strongest reason. */
export const TEST_KINDS: MyTaskReason[] = ['assigned', 'in_review', 'triage'];
export const TEST_KIND_LABEL: Record<MyTaskReason, string> = {
  assigned: 'Assigned to me',
  in_review: 'On a host I review',
  triage: 'Free to claim',
};

/**
 * What each tab counts.  `null` = not known: still loading, or the server
 * could not check — never shown as 0.
 */
export interface OperationsTabCounts {
  /** Findings the reader owns that need something. */
  findings: number | null;
  /** Hosts the reader has In Review. */
  hosts: number | null;
  /** Tests that are the reader's: assigned to them, or on a host they review. */
  tests: number | null;
  /** Unassigned critical / high tests — listed on the Tests tab, NOT counted
   *  as the reader's. */
  toClaim: number | null;
  /** Finished reviews of the reader's that are not done. */
  changed: number | null;
  /** Untouched hosts with a reason to look. */
  pickup: number | null;
}

export const NO_COUNTS: OperationsTabCounts = {
  findings: null, hosts: null, tests: null, toClaim: null, changed: null, pickup: null,
};

/** The tab bar's counts, from the workbench's own totals and the queue's. */
export function operationsTabCounts(
  workbench: WorkbenchResponse | null,
  pickup: number | null,
): OperationsTabCounts {
  if (!workbench) return { ...NO_COUNTS, pickup };
  const work = workbench.my_work;
  const groups = workbench.my_tasks?.group_counts;
  return {
    findings: work?.findings_needing_me ?? workbench.my_findings?.total_open ?? null,
    hosts: work?.hosts_in_review ?? workbench.my_queue?.in_review_count ?? null,
    tests: work
      ? work.tests_assigned + work.tests_on_hosts_in_review
      : groups ? groups.assigned + groups.in_review : null,
    toClaim: work?.to_claim ?? groups?.triage ?? null,
    changed: workbench.followups_unavailable ? null : (workbench.followups?.total ?? null),
    pickup,
  };
}

/** The reader's queue as one number — the sum of the three personal tabs. */
export function personalTotal(workbench: WorkbenchResponse | null): number {
  if (!workbench) return 0;
  if (workbench.my_work) return workbench.my_work.total;
  const c = operationsTabCounts(workbench, null);
  return (c.findings ?? 0) + (c.hosts ?? 0) + (c.tests ?? 0);
}

/** How many rows a tab lists (Tests lists the claimable ones too). */
export function tabListSize(tab: OperationsTab, counts: OperationsTabCounts): number | null {
  if (tab !== 'tests') return counts[tab];
  if (counts.tests == null && counts.toClaim == null) return null;
  return (counts.tests ?? 0) + (counts.toClaim ?? 0);
}

/**
 * The tab that opens when the URL names none: the first NON-EMPTY one in bar
 * order.  The queue's count arrives on its own request, so "Pick up" is the
 * fallback whatever it holds — the choice never waits for it and never flips.
 */
export function firstNonEmptyTab(counts: OperationsTabCounts): OperationsTab {
  for (const tab of OPERATIONS_TABS) {
    if (tab === 'pickup') break;
    if ((tabListSize(tab, counts) ?? 0) > 0) return tab;
  }
  return 'pickup';
}

/** `?tab=` — null for none or an unknown value. */
export function tabFromParams(params: URLSearchParams): OperationsTab | null {
  const v = params.get('tab');
  return (OPERATIONS_TABS as readonly string[]).includes(v ?? '') ? (v as OperationsTab) : null;
}

/** `?kind=` — the Tests tab's filter; null for every kind. */
export function testKindFromParams(params: URLSearchParams): MyTaskReason | null {
  const v = params.get('kind');
  return (TEST_KINDS as string[]).includes(v ?? '') ? (v as MyTaskReason) : null;
}

/** `?tier=N` — the tier the Pick up queue is narrowed to. */
export function tierFromParams(params: URLSearchParams): number | null {
  const v = Number(params.get('tier'));
  return Number.isInteger(v) && v >= 1 && v <= 5 ? v : null;
}

/** The search string that opens a tab of this page, keeping its other
 *  parameters.  `kind` is the Tests tab's; it is dropped elsewhere. */
export function tabSearch(
  current: URLSearchParams | string,
  tab: OperationsTab,
  kind?: MyTaskReason | null,
): string {
  const next = new URLSearchParams(current);
  next.set('tab', tab);
  if (tab === 'tests' && kind) next.set('kind', kind);
  else if (tab !== 'tests' || kind === null) next.delete('kind');
  return `?${next.toString()}`;
}
