/**
 * "My work" — what is waiting on the reader, and nothing else.
 *
 * One /workbench response, five groups, one line per row:
 *   - Tests assigned to me            host tests assigned to the caller
 *   - Findings that need me           owned findings with something owed —
 *                                     under investigation, required report
 *                                     text missing, a proposal to decide;
 *                                     the row says which
 *   - Hosts I am reviewing            hosts the caller marked In Review
 *   - Tests on hosts I am reviewing   tests to do on those hosts
 *   - Available to claim              unassigned critical / high tests —
 *                                     shared work, outside the personal total
 *
 * Design review 2026-10-02 (5.329.0):
 *  - A finding is listed for a REASON.  Owning a confirmed, written-up finding
 *    is a state, not a task; 24 of them were inflating "my queue".
 *  - The heading's total is the sum of the groups listed (the server's
 *    `my_work`), and "N to claim" always has its list on the page — one limit
 *    over the merged test list used to let a busy group take every row.
 *  - One "more" pattern: "Open all N" where a page lists exactly the group
 *    (the caller's hosts in review), otherwise "Show N more" in place — no
 *    page lists tests across hosts, and Findings has no "needs me" filter.
 *  - Hosts and tests are separate groups, each row's kind named for a screen
 *    reader; a test's PRIORITY is an outline badge, not a severity colour.
 *
 * Every row deep-links to its exact artifact — a test to its row on the host
 * (/hosts/:id#host-test-:id) — so the analyst lands where they left off.
 * The two team queues that used to live in this file are
 * `operations/ChangedSinceReviewSection` and `operations/UntouchedQueueSection`.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import {
  ClipboardList,
  Loader2,
  RefreshCw,
  ServerIcon,
  ShieldAlert,
} from 'lucide-react';
import type {
  MyAttentionResponse,
  MyFindingsResponse,
  MyTaskReason,
  MyTasksResponse,
  MyWorkTotals,
} from '../services/api';
import { updateHostTest } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { PostureSection, SectionCount } from './posture/PostureSection';
import { ListFooter } from './operations/QueueParts';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { cn } from '../utils/cn';
import { formatRelativeTime } from '../utils/relativeTime';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { MY_REVIEW_QUERY, fromOperationsQueue, hostIdOf } from '../utils/operationsQueue';

type BadgeVariant = React.ComponentProps<typeof Badge>['variant'];

type GroupKey = 'assigned' | 'findings' | 'in_review' | 'review_tests' | 'triage';

const GROUP_META: Record<GroupKey, { label: string; rank: number; kind: string }> = {
  assigned: { label: 'Tests assigned to me', rank: 1, kind: 'Test' },
  findings: { label: 'Findings that need me', rank: 2, kind: 'Finding' },
  in_review: { label: 'Hosts I am reviewing', rank: 3, kind: 'Host' },
  review_tests: { label: 'Tests on hosts I am reviewing', rank: 4, kind: 'Test' },
  // Shared, unowned work — kept last and out of the personal total.
  triage: { label: 'Available to claim', rank: 5, kind: 'Test' },
};
const GROUP_ORDER = (Object.keys(GROUP_META) as GroupKey[]).sort(
  (a, b) => GROUP_META[a].rank - GROUP_META[b].rank,
);

/** Element ids the page's lead and measures jump to. */
export const MY_WORK_ID = 'my-work';
export const TO_CLAIM_ID = 'available-to-claim';

const PRIORITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
/** A finding's severity wears the severity ramp; nothing else here does. */
const SEVERITY_VARIANT: Record<string, BadgeVariant> = {
  critical: 'severity-critical',
  high: 'severity-high',
  medium: 'severity-medium',
  low: 'severity-low',
  info: 'severity-info',
};

const tsOf = (v?: string | null): number => {
  if (!v) return 0;
  const t = new Date(v).getTime();
  return Number.isNaN(t) ? 0 : t;
};

/** Compact age ("5m"). Takes epoch ms because the callers sort on it first. */
function fmtAgo(ms: number): string {
  if (!ms) return '';
  return formatRelativeTime(ms, { style: 'compact' });
}

interface WorkItem {
  key: string;
  group: GroupKey;
  Icon: typeof ServerIcon;
  to: string;
  primary: string;
  primaryMono: boolean;
  chip: { label: string; variant: BadgeVariant } | null;
  /** What is owed, when the row can say (a finding's reasons). */
  owed?: string;
  meta: string;
  /** How long it has been waiting, compact; '' when nothing recorded it. */
  waiting: string;
  priorityRank: number;
  tsEpoch: number;
  // Present on "Available to claim" rows — claiming assigns the test to the
  // caller (moving it into "Tests assigned to me").
  claim?: { testId: number; revision: number };
}

function buildItems(
  queue: MyAttentionResponse | null,
  tasks: MyTasksResponse | null,
  findings: MyFindingsResponse | null,
): WorkItem[] {
  const items: WorkItem[] = [];

  for (const h of queue?.items ?? []) {
    const sev = h.critical_vulns > 0 ? 'critical' : h.high_vulns > 0 ? 'high' : 'low';
    const observations =
      h.critical_vulns || h.high_vulns
        ? `${h.critical_vulns ? `${h.critical_vulns} critical` : ''}${h.critical_vulns && h.high_vulns ? ' · ' : ''}${h.high_vulns ? `${h.high_vulns} high` : ''}`
        : 'no critical or high observation';
    items.push({
      key: `host-${h.host_id}`,
      group: 'in_review',
      Icon: ServerIcon,
      to: `/hosts/${h.host_id}`,
      primary: h.ip_address,
      primaryMono: true,
      chip: null,
      meta: `${h.hostname ? `${h.hostname} · ` : ''}${observations} · ${h.open_port_count} open port${h.open_port_count === 1 ? '' : 's'}`,
      waiting: fmtAgo(tsOf(h.follow_updated_at)),
      priorityRank: PRIORITY_RANK[sev] ?? 5,
      tsEpoch: tsOf(h.follow_updated_at),
    });
  }

  for (const f of findings?.items ?? []) {
    items.push({
      key: `finding-${f.finding_id}`,
      group: 'findings',
      Icon: ShieldAlert,
      // The finding's page is where each of the three is resolved: its
      // status, its report text (an empty section opens in the editor) and
      // its Proposals section.
      to: `/findings/${f.finding_id}`,
      primary: `#${f.finding_id}`,
      primaryMono: false,
      chip: { label: f.severity, variant: SEVERITY_VARIANT[f.severity] ?? 'muted' },
      owed: (f.needs ?? []).map((n) => n.text).join(' · ') || undefined,
      meta: `${f.title} · ${f.host_count} host${f.host_count === 1 ? '' : 's'}`,
      waiting: fmtAgo(tsOf(f.updated_at)),
      priorityRank: PRIORITY_RANK[f.severity] ?? 5,
      tsEpoch: tsOf(f.updated_at),
    });
  }

  for (const t of tasks?.items ?? []) {
    const reasons = (t.reasons && t.reasons.length ? t.reasons : ['triage']) as MyTaskReason[];
    const group: GroupKey = reasons.includes('assigned')
      ? 'assigned'
      : reasons.includes('in_review') ? 'review_tests' : 'triage';
    items.push({
      key: `task-${t.test_id}`,
      group,
      Icon: ClipboardList,
      to: `/hosts/${t.host_id}#host-test-${t.test_id}`,
      primary: t.host_ip,
      primaryMono: true,
      // A priority, not a severity: an outline badge that says so.
      chip: { label: `${t.priority} priority`, variant: 'outline' },
      meta: [t.label, t.description].filter(Boolean).join(' · '),
      waiting: fmtAgo(tsOf(t.updated_at)),
      priorityRank: PRIORITY_RANK[t.priority] ?? 5,
      tsEpoch: tsOf(t.updated_at),
      claim: group === 'triage' ? { testId: t.test_id, revision: t.revision } : undefined,
    });
  }

  items.sort((a, b) => {
    const g = GROUP_META[a.group].rank - GROUP_META[b.group].rank;
    if (g !== 0) return g;
    if (a.priorityRank !== b.priorityRank) return a.priorityRank - b.priorityRank;
    return b.tsEpoch - a.tsEpoch;
  });
  return items;
}

/** The server's count for each group — each test under ONE group, so the
 *  personal groups add up to the heading's total. */
function groupTotals(
  queue: MyAttentionResponse | null,
  tasks: MyTasksResponse | null,
  findings: MyFindingsResponse | null,
): Record<GroupKey, number> {
  // An older server sent overlapping `reason_counts` only: its triage count
  // is the nearest answer, and the rest state what is loaded.
  const groups = tasks?.group_counts;
  const loaded = (reason: MyTaskReason) =>
    (tasks?.items ?? []).filter((t) => (t.reasons?.[0] ?? 'triage') === reason).length;
  return {
    assigned: groups?.assigned ?? loaded('assigned'),
    findings: findings?.total_open ?? 0,
    in_review: queue?.in_review_count ?? 0,
    review_tests: groups?.in_review ?? loaded('in_review'),
    triage: groups?.triage ?? tasks?.reason_counts?.triage ?? 0,
  };
}

/**
 * What is waiting on the reader, as one number, and what is free to claim.
 * The server adds it up (`my_work`, v2.450.0); the fallback is the same sum
 * over the sections.  Shared with the Operations lead, so the two cannot
 * disagree.
 */
export function personalWorkCounts(
  queue: MyAttentionResponse | null,
  tasks: MyTasksResponse | null,
  findings: MyFindingsResponse | null,
  totals?: MyWorkTotals | null,
): { total: number; available: number } {
  if (totals) return { total: totals.total, available: totals.to_claim };
  const g = groupTotals(queue, tasks, findings);
  return { total: g.assigned + g.findings + g.in_review + g.review_tests, available: g.triage };
}

export interface MyWorkCardProps {
  queue: MyAttentionResponse | null;
  tasks: MyTasksResponse | null;
  findings: MyFindingsResponse | null;
  /** The server's sum of the personal groups (`WorkbenchResponse.my_work`). */
  totals?: MyWorkTotals | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** After a claim (or its undo): refresh without blanking the section.
   *  Falls back to `onRetry`. */
  onChanged?: () => void;
  /** The reader's project role allows writes — Claim is hidden otherwise. */
  canWrite?: boolean;
  /** "updated …" beside the heading (components/UpdatedAt). */
  updated?: React.ReactNode;
}

/** Carried to the host page so it offers "Back to my work" (v5.237.0): a host
 *  opened from Operations used to offer only "Back to Hosts". */
export const FROM_OPERATIONS = { state: { fromOperations: true } } as const;

/** Rows shown per group before its footer. */
const GROUP_PREVIEW = 5;

const jumpTo = (id: string) => (e: React.MouseEvent) => {
  e.preventDefault();
  document.getElementById(id)?.scrollIntoView?.({ block: 'start' });
};

export const MyWorkCard: React.FC<MyWorkCardProps> = ({
  queue, tasks, findings, totals = null,
  loading, error, onRetry, onChanged, canWrite = true, updated,
}) => {
  const { user } = useAuth();
  const toast = useToast();
  const changed = onChanged ?? onRetry;
  // Each group expands by itself (v5.237.0).
  const [expandedGroups, setExpandedGroups] = React.useState<Set<GroupKey>>(new Set());
  const [claimingId, setClaimingId] = React.useState<number | null>(null);

  const items = React.useMemo(
    () => buildItems(queue, tasks, findings),
    [queue, tasks, findings],
  );
  const groups = React.useMemo(
    () => GROUP_ORDER
      .map((key) => ({ key, rows: items.filter((it) => it.group === key) }))
      .filter((g) => g.rows.length > 0),
    [items],
  );
  const totalOf = groupTotals(queue, tasks, findings);

  const handleClaim = async (c: { testId: number; revision: number }) => {
    if (user?.id == null) return;
    setClaimingId(c.testId);
    try {
      const claimed = await updateHostTest(c.testId, {
        assigned_to_id: user.id,
        expected_revision: c.revision,
      });
      // Undoable: the test was unassigned before the claim.
      toast.success("Claimed — it's now in your assigned tests", {
        autoHideMs: 6000,
        action: {
          label: 'Undo',
          onClick: () => {
            updateHostTest(c.testId, {
              assigned_to_id: null,
              expected_revision: claimed.revision,
            })
              .then(changed)
              .catch((err) => toast.error(formatApiError(err, 'Could not undo the claim.')));
          },
        },
      });
      changed(); // refetch so it moves from Available → Assigned
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to claim.'));
    } finally {
      setClaimingId(null);
    }
  };

  const { total: totalCount, available: availableCount } =
    personalWorkCounts(queue, tasks, findings, totals);

  const countLink = 'rounded hover:text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

  return (
    <div id={MY_WORK_ID} className="flex min-w-0 scroll-mt-md flex-col gap-lg">
      <PostureSection
        title={<>
          <span>My work</span>
          {(totalCount > 0 || availableCount > 0) && (
            <SectionCount>
              {totalCount > 0 && (
                <span>{totalCount.toLocaleString()} waiting on you</span>
              )}
              {totalCount > 0 && availableCount > 0 && ' · '}
              {availableCount > 0 && (
                // The count jumps to its list, which is always on the page.
                <a href={`#${TO_CLAIM_ID}`} onClick={jumpTo(TO_CLAIM_ID)} className={countLink}>
                  {availableCount.toLocaleString()} to claim
                </a>
              )}
            </SectionCount>
          )}
        </>}
        actions={<>
          {updated}
          {/* The feed of what the reader already did lives on Collaboration;
              it had a third of this page's first screen. */}
          <Link to="/activity?author=me" className="text-info hover:underline">My activity</Link>
        </>}
      >
        {loading ? (
          <div className="flex items-center gap-xs" role="status" aria-live="polite">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Loading your work…</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTitle>Couldn't load your work</AlertTitle>
            <AlertDescription>
              <p className="break-words">{error}</p>
              <Button size="sm" variant="outline" className="mt-xs" onClick={onRetry}>
                <RefreshCw className="size-3.5" aria-hidden /> Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : items.length === 0 ? (
          <p className="text-metadata text-muted-foreground">
            Nothing is waiting on you.{' '}
            {canWrite
              ? <>Work shows here when you take a host into review, a test is assigned to you, or a finding you own needs something — it is under investigation, its report text is incomplete, or a proposal about it is waiting.</>
              : <>Work shows here when a test is assigned to you or a finding you own needs something.</>}
          </p>
        ) : (
          <div className="flex flex-col gap-md">
            {groups.map((g) => {
              const meta = GROUP_META[g.key];
              const open = expandedGroups.has(g.key);
              const rows = open ? g.rows : g.rows.slice(0, GROUP_PREVIEW);
              const total = Math.max(totalOf[g.key], g.rows.length);
              const beyond = total - g.rows.length;
              // Only the hosts group has a page that lists exactly it.
              const openAll = g.key === 'in_review'
                ? { to: buildHostsUrl({ q: MY_REVIEW_QUERY }), label: `Open all ${total.toLocaleString()} in Hosts` }
                : undefined;
              const hidden = g.rows.length - rows.length;
              const toggle = () => setExpandedGroups((prev) => {
                const next = new Set(prev);
                if (next.has(g.key)) next.delete(g.key); else next.add(g.key);
                return next;
              });
              return (
                <section
                  key={g.key}
                  aria-label={meta.label}
                  id={g.key === 'triage' ? TO_CLAIM_ID : undefined}
                  className="scroll-mt-md"
                >
                  <div className="mb-xxs flex flex-wrap items-baseline gap-xs">
                    <h3 className="text-metadata font-semibold text-foreground">{meta.label}</h3>
                    {/* The server's count; never the rows that happen to be on screen. */}
                    <span className="text-caption tabular-nums text-muted-foreground">{total.toLocaleString()}</span>
                    {g.key === 'triage' && (
                      <span className="text-caption text-muted-foreground">
                        — unassigned critical and high tests; not counted as yours
                      </span>
                    )}
                    {/* The label of the right-hand column of every row below. */}
                    <span className="ml-auto text-caption text-muted-foreground" aria-hidden
                      title="How long since the row last changed: since you took the host into review, or since the test or finding was last updated.">
                      waiting
                    </span>
                  </div>
                  <ul className="flex flex-col">
                    {rows.map((it) => (
                      <li key={it.key}>
                        <div className="flex min-w-0 items-center gap-xxs">
                          {/* A link, so a middle-click opens it in a tab. */}
                          <Link
                            to={it.to}
                            // A host row carries its group's hosts as the queue;
                            // a finding / test row is not a host page.
                            state={g.key === 'in_review' && hostIdOf(it.to) != null
                              ? fromOperationsQueue(g.rows.map((r) => hostIdOf(r.to)), meta.label,
                                { partial: beyond > 0 }).state
                              : undefined}
                            className={cn(
                              'flex min-w-0 flex-1 items-center gap-xs px-xs py-xxs text-left',
                              'rounded-control hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                            )}
                          >
                            <it.Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                            <span className="sr-only">{meta.kind}:</span>
                            {it.chip && (
                              <Badge variant={it.chip.variant} className="shrink-0 whitespace-nowrap">{it.chip.label}</Badge>
                            )}
                            <span
                              className={cn(
                                'shrink-0 text-metadata font-medium text-foreground',
                                it.primaryMono && 'font-mono',
                              )}
                            >
                              {it.primary}
                            </span>
                            <span
                              className="min-w-0 flex-1 truncate text-metadata text-muted-foreground"
                              title={[it.owed, it.meta].filter(Boolean).join(' — ')}
                            >
                              {it.owed && <span className="font-medium text-foreground">{it.owed}</span>}
                              {' — '}{it.meta}
                            </span>
                            <span
                              className="w-10 shrink-0 text-right text-caption tabular-nums text-muted-foreground"
                              aria-label={it.waiting ? `waiting ${it.waiting}` : 'waiting time not recorded'}
                              title={it.waiting ? `Waiting ${it.waiting}` : 'No change was recorded for this row.'}
                            >
                              {it.waiting || '—'}
                            </span>
                          </Link>
                          {it.claim && canWrite && (
                            <Button
                              size="sm" variant="ghost" className="h-7 shrink-0 text-info"
                              disabled={claimingId === it.claim.testId}
                              onClick={() => handleClaim(it.claim!)}
                            >
                              {claimingId === it.claim.testId ? 'Claiming…' : 'Claim'}
                            </Button>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                  <div className="pl-xs">
                    <ListFooter
                      shown={rows.length}
                      total={total}
                      openAll={openAll}
                      // In place only where no page lists the group.
                      more={!openAll && (hidden > 0 || open)
                        ? { label: open ? 'Show fewer' : `Show ${hidden} more`, onClick: toggle }
                        : undefined}
                    >
                      {!openAll && open && beyond > 0 && (
                        <span className="text-caption text-muted-foreground">
                          {beyond.toLocaleString()} more {beyond === 1 ? 'is' : 'are'} not loaded — they take these rows' places as these are done.
                        </span>
                      )}
                      {g.key === 'findings' && (
                        <Link to="/findings?owner=me" className="text-caption text-muted-foreground hover:text-info hover:underline">
                          All findings I own
                        </Link>
                      )}
                    </ListFooter>
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </PostureSection>
    </div>
  );
};

export default MyWorkCard;
