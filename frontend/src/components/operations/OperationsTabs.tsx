/**
 * Operations' tab bar and the one list on screen (5.331.0; UI_STYLE_GUIDE §42).
 *
 * The page was six lists stacked at the same weight, each a five-row sample
 * with its own "more".  It is now a bar of five tabs with their counts, and
 * the ONE selected list as a full table, a page at a time:
 *
 *   Findings · Hosts · Tests · Changed since review · Pick up
 *
 * Counts and rows are separate requests.  The counts are the page's light
 * workbench call (and the queue's total); a tab's rows are fetched only when
 * the tab is opened — Radix mounts the active panel alone — through
 * `usePagedList` (§39: a failed read is "could not be checked", never an
 * empty list).  Each list route is the function that
 * produced its tab's count, so the count is the size of the list paged here
 * (`backend/tests/test_operations_tabs.py`).
 *
 * A count that is not known reads "—" — never 0.
 */
import React from 'react';

import {
  getInvestigationQueue,
  getMyFindingsPage,
  getMyReviewHostsPage,
  getMyTestsPage,
  getReviewFollowupsPage,
} from '../../services/api';
import type {
  FindingNeed, InvestigateRow, InvestigationQueueResponse, MyAttentionHost, MyAttentionResponse,
  MyFindingItem, MyFindingsResponse, MyTaskItem, MyTaskReason, MyTasksReasonCounts, MyTasksResponse,
  ReviewFollowupRow, ReviewFollowupsResponse,
} from '../../services/api';
import { usePagedList, type PagedList } from '../../hooks/usePagedList';
import { useProjectId } from '../../hooks/useProjectId';
import { useUrlPage } from '../../hooks/useUrlPage';
import type { ListPage } from '../../hooks/useListQuery';
import {
  OPERATIONS_PAGE_SIZE, OPERATIONS_TABS, OPERATIONS_TAB_LABEL,
  type OperationsTab, type OperationsTabCounts,
} from '../../utils/operationsTabs';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import ChangedSinceReviewSection from './ChangedSinceReviewSection';
import FindingsNeedingMeTable from './FindingsNeedingMeTable';
import MyTestsTable from './MyTestsTable';
import ReviewHostsTable from './ReviewHostsTable';
import UntouchedQueueSection from './UntouchedQueueSection';
import type { ListState, Pager } from './QueueParts';

/** Every tab pages the same way, and keeps its page in the address (only the
 *  open tab's panel is mounted, so `?page=` is that tab's). */
const usePage = () => ({ pageSize: OPERATIONS_PAGE_SIZE, page: useUrlPage() });

/** What a table needs of a paged list. */
function listProps<T, P extends ListPage<T>>(
  list: PagedList<T, P>,
): { rows: T[] | null; state: ListState; pager: Pager } {
  return {
    rows: list.rows,
    state: { loading: list.loading, error: list.error, onRetry: () => void list.reload() },
    pager: { page: list.page, pageSize: list.pageSize, total: list.total, onPage: list.setPage },
  };
}

/** A panel reads its own list (by the API function's name: an action in a
 *  list and the page's Refresh say which reads are out of date by that name
 *  — `QueueParts.OPERATIONS_READS`). */
interface PanelProps {
  /** The reader's project role allows writes (hooks/useProjectRole). */
  canWrite: boolean;
}

const FindingsPanel: React.FC<{
  need: FindingNeed | null;
  onNeed: (need: FindingNeed | null) => void;
  /** The workbench's count per kind of work, until this list has its own. */
  pageNeeds: Record<FindingNeed, number | null>;
}> = ({ need, onNeed, pageNeeds }) => {
  const projectId = useProjectId();
  const list = usePagedList<MyFindingItem, MyFindingsResponse & ListPage<MyFindingItem>>(
    'getMyFindingsPage',
    async (req) => {
      const r = await getMyFindingsPage(projectId, need, req);
      // The size of the list being paged: both kinds, or the chosen one.
      return { ...r, total: need ? (r.need_counts?.[need] ?? r.items.length) : r.total_open };
    },
    [projectId, need],
    usePage(),
  );
  // The chips' counts are this list's own (the same statement as the rows'
  // total); until it has answered, the page's.
  const needCounts = (list.response ?? list.lastResponse)?.need_counts ?? pageNeeds;
  return <FindingsNeedingMeTable {...listProps(list)} needCounts={needCounts} need={need} onNeed={onNeed} />;
};

const HostsPanel: React.FC<PanelProps> = ({ canWrite }) => {
  const projectId = useProjectId();
  const list = usePagedList<MyAttentionHost, MyAttentionResponse & ListPage<MyAttentionHost>>(
    'getMyReviewHostsPage',
    async (req) => {
      const r = await getMyReviewHostsPage(projectId, req);
      return { ...r, total: r.in_review_count };
    },
    [projectId],
    usePage(),
  );
  return <ReviewHostsTable {...listProps(list)} canWrite={canWrite} />;
};

const TestsPanel: React.FC<PanelProps & {
  kind: MyTaskReason | null;
  onKind: (kind: MyTaskReason | null) => void;
  /** The workbench's count per kind, until this list has its own. */
  pageGroups: MyTasksReasonCounts | null;
}> = ({ canWrite, kind, onKind, pageGroups }) => {
  const projectId = useProjectId();
  const list = usePagedList<MyTaskItem, MyTasksResponse & ListPage<MyTaskItem>>(
    'getMyTestsPage',
    async (req) => {
      const r = await getMyTestsPage(projectId, kind, req);
      const groups = r.group_counts;
      // The size of the list being paged: every kind, or the chosen one.
      return { ...r, total: kind ? (groups?.[kind] ?? r.items.length) : r.total_open };
    },
    [projectId, kind],
    usePage(),
  );
  // The chips' counts are this list's own (the same statement as the rows'
  // total); until it has answered, the page's.
  const groups = (list.response ?? list.lastResponse)?.group_counts ?? pageGroups;
  const kindCounts: Record<MyTaskReason, number | null> = groups
    ? { assigned: groups.assigned, in_review: groups.in_review, triage: groups.triage }
    : { assigned: null, in_review: null, triage: null };
  return (
    <MyTestsTable
      {...listProps(list)}
      kindCounts={kindCounts}
      kind={kind}
      onKind={onKind}
      canWrite={canWrite}
    />
  );
};

const ChangedPanel: React.FC<PanelProps> = ({ canWrite }) => {
  const projectId = useProjectId();
  const list = usePagedList<ReviewFollowupRow, ReviewFollowupsResponse>(
    'getReviewFollowupsPage',
    (req) => getReviewFollowupsPage(projectId, req),
    [projectId],
    usePage(),
  );
  return <ChangedSinceReviewSection {...listProps(list)} canWrite={canWrite} />;
};

const PickUpPanel: React.FC<PanelProps & {
  tier: number | null;
  onTier: (tier: number | null) => void;
}> = ({ canWrite, tier, onTier }) => {
  const projectId = useProjectId();
  const list = usePagedList<InvestigateRow, InvestigationQueueResponse & ListPage<InvestigateRow>>(
    'getInvestigationQueue',
    async (req) => {
      const r = await getInvestigationQueue(projectId, tier, req);
      // The size of the list being paged: the whole queue, or the tier's hosts.
      return { ...r, total: tier != null ? (r.tier_counts?.[tier - 1] ?? r.items.length) : r.queue_total };
    },
    [projectId, tier],
    usePage(),
  );
  return (
    <UntouchedQueueSection
      {...listProps(list)}
      data={list.response ?? list.lastResponse}
      tier={tier}
      onTier={onTier}
      canWrite={canWrite}
    />
  );
};

/** A tab's count: the number, "…" while it is on its way, "—" when it could
 *  not be checked. */
const TabCount: React.FC<{ value: number | null; loading: boolean }> = ({ value, loading }) => (
  <span className="ml-xs tabular-nums text-muted-foreground" data-testid="tab-count">
    {value != null ? value.toLocaleString() : loading ? '…' : '—'}
  </span>
);

const countWords = (value: number | null, loading: boolean): string =>
  value != null ? value.toLocaleString() : loading ? 'loading' : 'could not be checked';

export interface OperationsTabsProps {
  /** The tab on screen; null while the default is not decided yet. */
  tab: OperationsTab | null;
  onTab: (tab: OperationsTab) => void;
  counts: OperationsTabCounts;
  /** The workbench (findings, hosts, tests, changed) is still loading. */
  countsLoading: boolean;
  /** The queue's total (pick up) is still loading. */
  pickupLoading: boolean;
  canWrite: boolean;
  tier: number | null;
  onTier: (tier: number | null) => void;
  testKind: MyTaskReason | null;
  onTestKind: (kind: MyTaskReason | null) => void;
  /** The workbench's test counts per kind (`my_tasks.group_counts`). */
  testGroups?: MyTasksReasonCounts | null;
  /** The Findings tab's filter (`?need=`). */
  findingNeed: FindingNeed | null;
  onFindingNeed: (need: FindingNeed | null) => void;
}

export const OperationsTabs: React.FC<OperationsTabsProps> = ({
  tab, onTab, counts, countsLoading, pickupLoading, canWrite,
  tier, onTier, testKind, onTestKind, testGroups = null, findingNeed, onFindingNeed,
}) => {
  const panel: PanelProps = { canWrite };
  const pageNeeds = { decide: counts.findingsDecide, write: counts.findingsWrite };
  const loadingOf = (t: OperationsTab) => (t === 'pickup' ? pickupLoading : countsLoading);
  return (
    <Tabs value={tab ?? ''} onValueChange={(v) => onTab(v as OperationsTab)} className="min-w-0">
      {/* The bar wraps before it widens the page. */}
      <TabsList aria-label="Your work, one list at a time" className="h-auto max-w-full flex-wrap justify-start">
        {OPERATIONS_TABS.map((t) => {
          const loading = loadingOf(t);
          const value = counts[t];
          const claim = t === 'tests' ? counts.toClaim : null;
          return (
            <TabsTrigger
              key={t}
              value={t}
              aria-label={t === 'tests'
                ? `Tests: ${countWords(value, loading)} yours${claim ? `, ${claim.toLocaleString()} free to claim` : ''}`
                : `${OPERATIONS_TAB_LABEL[t]}: ${countWords(value, loading)}`}
            >
              {OPERATIONS_TAB_LABEL[t]}
              <TabCount value={value} loading={loading} />
              {/* Shared work, listed on the tab and NOT part of its count. */}
              {claim != null && claim > 0 && (
                <span className="ml-xs text-caption font-normal text-muted-foreground"
                  title="Unassigned critical and high priority tests anyone may claim — listed on this tab, not counted as yours.">
                  + {claim.toLocaleString()} to claim
                </span>
              )}
            </TabsTrigger>
          );
        })}
      </TabsList>

      {tab == null && (
        <div role="status" aria-label="Loading your work…" className="mt-md flex flex-col gap-xs">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-6 animate-pulse rounded-control bg-muted" aria-hidden />
          ))}
        </div>
      )}
      <TabsContent value="findings" className="min-w-0">
        <FindingsPanel need={findingNeed} onNeed={onFindingNeed} pageNeeds={pageNeeds} />
      </TabsContent>
      <TabsContent value="hosts" className="min-w-0"><HostsPanel {...panel} /></TabsContent>
      <TabsContent value="tests" className="min-w-0">
        <TestsPanel {...panel} kind={testKind} onKind={onTestKind} pageGroups={testGroups} />
      </TabsContent>
      <TabsContent value="changed" className="min-w-0"><ChangedPanel {...panel} /></TabsContent>
      <TabsContent value="pickup" className="min-w-0">
        <PickUpPanel {...panel} tier={tier} onTier={onTier} />
      </TabsContent>
    </Tabs>
  );
};

export default OperationsTabs;
