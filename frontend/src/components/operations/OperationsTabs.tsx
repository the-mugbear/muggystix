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
 * `usePagedList` (§39: the latest request wins, a failed read is "could not
 * be checked", never an empty list).  Each list route is the function that
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
  InvestigateRow, InvestigationQueueResponse, MyAttentionHost, MyAttentionResponse,
  MyFindingItem, MyFindingsResponse, MyTaskItem, MyTaskReason, MyTasksReasonCounts, MyTasksResponse,
  ReviewFollowupRow, ReviewFollowupsResponse,
} from '../../services/api';
import { usePagedList, type PagedList } from '../../hooks/usePagedList';
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

const PAGE = { pageSize: OPERATIONS_PAGE_SIZE };

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

interface PanelProps {
  /** The reader's project role allows writes (hooks/useProjectRole). */
  canWrite: boolean;
  /** The page's Refresh: read the list again from its first page. */
  refreshKey: number;
  /** After an action: the page re-reads the counts (quietly). */
  onCountsChanged: () => void;
}

/** After an action in a list: its rows in place, and the page's counts. */
const useChanged = (reload: () => Promise<void>, onCountsChanged: () => void) =>
  React.useCallback(() => {
    void reload();
    onCountsChanged();
  }, [reload, onCountsChanged]);

const FindingsPanel: React.FC<PanelProps> = ({ refreshKey }) => {
  const list = usePagedList<MyFindingItem, MyFindingsResponse & ListPage<MyFindingItem>>(
    async (req) => {
      const r = await getMyFindingsPage(req);
      return { ...r, total: r.total_open };
    },
    [refreshKey],
    PAGE,
  );
  return <FindingsNeedingMeTable {...listProps(list)} />;
};

const HostsPanel: React.FC<PanelProps> = ({ refreshKey, canWrite }) => {
  const list = usePagedList<MyAttentionHost, MyAttentionResponse & ListPage<MyAttentionHost>>(
    async (req) => {
      const r = await getMyReviewHostsPage(req);
      return { ...r, total: r.in_review_count };
    },
    [refreshKey],
    PAGE,
  );
  return <ReviewHostsTable {...listProps(list)} canWrite={canWrite} />;
};

const TestsPanel: React.FC<PanelProps & {
  kind: MyTaskReason | null;
  onKind: (kind: MyTaskReason | null) => void;
  /** The workbench's count per kind, until this list has its own. */
  pageGroups: MyTasksReasonCounts | null;
}> = ({ refreshKey, canWrite, onCountsChanged, kind, onKind, pageGroups }) => {
  const list = usePagedList<MyTaskItem, MyTasksResponse & ListPage<MyTaskItem>>(
    async (req) => {
      const r = await getMyTestsPage(kind, req);
      const groups = r.group_counts;
      // The size of the list being paged: every kind, or the chosen one.
      return { ...r, total: kind ? (groups?.[kind] ?? r.items.length) : r.total_open };
    },
    [kind, refreshKey],
    PAGE,
  );
  // The chips' counts are this list's own (the same statement as the rows'
  // total); until it has answered, the page's.
  const groups = (list.response ?? list.lastResponse)?.group_counts ?? pageGroups;
  const kindCounts: Record<MyTaskReason, number | null> = groups
    ? { assigned: groups.assigned, in_review: groups.in_review, triage: groups.triage }
    : { assigned: null, in_review: null, triage: null };
  const onChanged = useChanged(list.reload, onCountsChanged);
  return (
    <MyTestsTable
      {...listProps(list)}
      kindCounts={kindCounts}
      kind={kind}
      onKind={onKind}
      canWrite={canWrite}
      onChanged={onChanged}
    />
  );
};

const ChangedPanel: React.FC<PanelProps> = ({ refreshKey, canWrite, onCountsChanged }) => {
  const list = usePagedList<ReviewFollowupRow, ReviewFollowupsResponse>(
    (req) => getReviewFollowupsPage(req),
    [refreshKey],
    PAGE,
  );
  const onChanged = useChanged(list.reload, onCountsChanged);
  return <ChangedSinceReviewSection {...listProps(list)} canWrite={canWrite} onChanged={onChanged} />;
};

const PickUpPanel: React.FC<PanelProps & {
  tier: number | null;
  onTier: (tier: number | null) => void;
}> = ({ refreshKey, canWrite, onCountsChanged, tier, onTier }) => {
  const list = usePagedList<InvestigateRow, InvestigationQueueResponse & ListPage<InvestigateRow>>(
    async (req) => {
      const r = await getInvestigationQueue(tier, req);
      // The size of the list being paged: the whole queue, or the tier's hosts.
      return { ...r, total: tier != null ? (r.tier_counts?.[tier - 1] ?? r.items.length) : r.queue_total };
    },
    [tier, refreshKey],
    PAGE,
  );
  const onChanged = useChanged(list.reload, onCountsChanged);
  return (
    <UntouchedQueueSection
      {...listProps(list)}
      data={list.response ?? list.lastResponse}
      tier={tier}
      onTier={onTier}
      canWrite={canWrite}
      onChanged={onChanged}
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
  refreshKey: number;
  onCountsChanged: () => void;
  tier: number | null;
  onTier: (tier: number | null) => void;
  testKind: MyTaskReason | null;
  onTestKind: (kind: MyTaskReason | null) => void;
  /** The workbench's test counts per kind (`my_tasks.group_counts`). */
  testGroups?: MyTasksReasonCounts | null;
}

export const OperationsTabs: React.FC<OperationsTabsProps> = ({
  tab, onTab, counts, countsLoading, pickupLoading, canWrite, refreshKey, onCountsChanged,
  tier, onTier, testKind, onTestKind, testGroups = null,
}) => {
  const panel: PanelProps = { canWrite, refreshKey, onCountsChanged };
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
      <TabsContent value="findings" className="min-w-0"><FindingsPanel {...panel} /></TabsContent>
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
