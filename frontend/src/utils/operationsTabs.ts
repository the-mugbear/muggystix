/**
 * Operations' tabs (5.331.0): which exist, what each counts, which one opens.
 *
 * Pure — the page reads the workbench's counts and the URL, and these decide.
 * The client-free half of `components/operations/OperationsTabs`.
 */
import type { FindingNeed, MyTaskReason, WorkbenchResponse } from '../services/api';

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
export const OPERATIONS_PAGE_SIZE = 10;

/** The three kinds of test the Tests tab lists — a test is under ONE, its
 *  strongest reason. */
export const TEST_KINDS: MyTaskReason[] = ['assigned', 'in_review', 'triage'];
export const TEST_KIND_LABEL: Record<MyTaskReason, string> = {
  assigned: 'Assigned to me',
  in_review: 'On a host I review',
  triage: 'Free to claim',
};

/** The two kinds of work the Findings tab lists — a finding is under ONE: a
 *  decision comes before the writing. */
export const FINDING_NEEDS: FindingNeed[] = ['decide', 'write'];
export const FINDING_NEED_LABEL: Record<FindingNeed, string> = {
  decide: 'Needs a decision',
  write: 'Needs report text',
};

/**
 * What each tab counts.  `null` = not known: still loading, or the server
 * could not check — never shown as 0.
 */
export interface OperationsTabCounts {
  /** Findings the reader owns that need something. */
  findings: number | null;
  /** …of which: under investigation, or a proposal waits for a decision. */
  findingsDecide: number | null;
  /** …of which: only required report text is missing. */
  findingsWrite: number | null;
  /** Hosts the reader has In Review. */
  hosts: number | null;
  /** Tests that are the reader's: assigned to them, or on a host they review. */
  tests: number | null;
  /** …of which: assigned to the reader. */
  testsAssigned: number | null;
  /** …of which: on a host the reader reviews, and not assigned to them. */
  testsInReview: number | null;
  /** Unassigned critical / high tests — listed on the Tests tab, NOT counted
   *  as the reader's. */
  toClaim: number | null;
  /** Finished reviews of the reader's that are not done. */
  changed: number | null;
  /** Untouched hosts with a reason to look. */
  pickup: number | null;
}

export const NO_COUNTS: OperationsTabCounts = {
  findings: null, findingsDecide: null, findingsWrite: null,
  hosts: null,
  tests: null, testsAssigned: null, testsInReview: null,
  toClaim: null, changed: null, pickup: null,
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
    findingsDecide: work?.findings_to_decide ?? null,
    findingsWrite: work?.findings_to_write ?? null,
    hosts: work?.hosts_in_review ?? workbench.my_queue?.in_review_count ?? null,
    tests: work
      ? work.tests_assigned + work.tests_on_hosts_in_review
      : groups ? groups.assigned + groups.in_review : null,
    testsAssigned: work?.tests_assigned ?? groups?.assigned ?? null,
    testsInReview: work?.tests_on_hosts_in_review ?? groups?.in_review ?? null,
    toClaim: work?.to_claim ?? groups?.triage ?? null,
    changed: workbench.followups_unavailable ? null : (workbench.followups?.total ?? null),
    pickup,
  };
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

/** `?need=` — the Findings tab's filter; null for both kinds. */
export function needFromParams(params: URLSearchParams): FindingNeed | null {
  const v = params.get('need');
  return (FINDING_NEEDS as string[]).includes(v ?? '') ? (v as FindingNeed) : null;
}

/** `?tier=N` — the tier the Pick up queue is narrowed to. */
export function tierFromParams(params: URLSearchParams): number | null {
  const v = Number(params.get('tier'));
  return Number.isInteger(v) && v >= 1 && v <= 5 ? v : null;
}

/** A tab's own filter: the Tests tab's kind, the Findings tab's need. */
export type TabFilter = MyTaskReason | FindingNeed;

/** The search string that opens a tab of this page, keeping its other
 *  parameters.  `filter` is the tab's own — `kind` on Tests, `need` on
 *  Findings: a value sets it, `null` clears it, none asked keeps what the
 *  address has.  Each is dropped on every other tab. */
export function tabSearch(
  current: URLSearchParams | string,
  tab: OperationsTab,
  filter?: TabFilter | null,
): string {
  const next = new URLSearchParams(current);
  next.set('tab', tab);
  const own = (param: 'kind' | 'need', owner: OperationsTab, values: string[]) => {
    if (tab !== owner || filter === null) next.delete(param);
    else if (filter !== undefined && values.includes(filter)) next.set(param, filter);
  };
  own('kind', 'tests', TEST_KINDS);
  own('need', 'findings', FINDING_NEEDS);
  return `?${next.toString()}`;
}
