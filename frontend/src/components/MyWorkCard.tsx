/**
 * Unified "My work" list — the analyst's single resume queue.  Merges four
 * personal surfaces from the one /workbench response into a grouped,
 * one-line-per-row worklist, ordered worst-first:
 *   - Overdue      = assigned notes past their due date
 *   - Handoffs     = note threads of type 'handoff' assigned to the caller
 *   - Assigned     = assigned notes + test-plan steps assigned to the caller
 *   - Findings     = active canonical findings the caller owns
 *   - In review    = hosts the caller marked In Review (+ in-review steps)
 *   - Available    = unassigned critical/high test-plan steps anyone may claim
 *
 * §27: owned findings ARE surfaced here (one resume surface — don't make the
 * analyst remember a second queue exists), and unassigned "Available" triage is
 * kept visually separate AND excluded from the personal total, so shared work
 * doesn't inflate the user's apparent load. Each Available row has a Claim.
 *
 * P0 (resume pass): every row deep-links to its EXACT artifact — a note to
 * its thread anchor (/hosts/:id#note-:id), a plan step to its entry
 * (/test-plans/:plan#entry-:entry) — so the analyst lands where they left off,
 * not on a generic host page.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ClipboardList,
  Loader2,
  MessageSquare,
  RefreshCw,
  ServerIcon,
  ShieldAlert,
} from 'lucide-react';
import type {
  InvestigateRow,
  InvestigationQueueResponse,
  MyAttentionResponse,
  MyFindingsResponse,
  MyNotesResponse,
  MyTaskReason,
  MyTasksResponse,
  ReviewFollowupRow,
  ReviewFollowupsResponse,
} from '../services/api';
import { followHost, updateTestPlanEntry } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { cn } from '../utils/cn';
import { formatRelativeTime } from '../utils/relativeTime';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { fromOperationsQueue, hostIdOf } from '../utils/operationsQueue';

type BadgeTone = 'destructive' | 'warning' | 'info' | 'muted' | 'secondary' | 'outline';

type GroupKey = 'overdue' | 'handoff' | 'assigned' | 'findings' | 'in_review' | 'triage';

const GROUP_META: Record<GroupKey, { label: string; rank: number; tone: BadgeTone }> = {
  overdue: { label: 'Overdue', rank: 0, tone: 'destructive' },
  handoff: { label: 'Handoffs', rank: 1, tone: 'info' },
  assigned: { label: 'Assigned', rank: 2, tone: 'info' },
  findings: { label: 'Findings I own', rank: 3, tone: 'info' },
  in_review: { label: 'In review', rank: 4, tone: 'muted' },
  // Shared, unowned work — kept last and out of the personal total.
  triage: { label: 'Available to claim', rank: 5, tone: 'warning' },
};
const GROUP_ORDER = (Object.keys(GROUP_META) as GroupKey[]).sort(
  (a, b) => GROUP_META[a].rank - GROUP_META[b].rank,
);

const PRIORITY_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const sevTone = (p: string): BadgeTone =>
  p === 'critical' ? 'destructive' : p === 'high' ? 'warning' : p === 'medium' ? 'info' : 'muted';

const tsOf = (v?: string | null): number => {
  if (!v) return 0;
  const t = new Date(v).getTime();
  return Number.isNaN(t) ? 0 : t;
};

/** Compact age ("5m") — the column header already says what it measures.
 *  Takes epoch ms because the callers sort on it first. */
function fmtAgo(ms: number): string {
  if (!ms) return '';
  return formatRelativeTime(ms, { style: 'compact' });
}

/** "Overdue 2d" / "Due today" / "Due 3d" from a due timestamp. */
function fmtDue(due: string | null): { label: string; tone: BadgeTone } | null {
  if (!due) return null;
  const t = new Date(due).getTime();
  if (Number.isNaN(t)) return null;
  const days = Math.round((t - Date.now()) / 86400000);
  if (days < 0) return { label: `Overdue ${Math.abs(days)}d`, tone: 'destructive' };
  if (days === 0) return { label: 'Due today', tone: 'warning' };
  return { label: `Due ${days}d`, tone: days <= 2 ? 'warning' : 'muted' };
}

interface WorkItem {
  key: string;
  group: GroupKey;
  Icon: typeof ServerIcon;
  to: string;
  primary: string;
  primaryMono: boolean;
  chip: { label: string; tone: BadgeTone } | null;
  meta: string;
  right: { label: string; tone: BadgeTone | null };
  priorityRank: number;
  tsEpoch: number;
  // Present on "Available to claim" rows — claiming assigns the entry to the
  // caller (moving it into Assigned).
  claim?: { planId: number; entryId: number; updatedAt: string | null };
}

function buildItems(
  queue: MyAttentionResponse | null,
  tasks: MyTasksResponse | null,
  notes: MyNotesResponse | null,
  findings: MyFindingsResponse | null,
): WorkItem[] {
  const items: WorkItem[] = [];

  // Notes — overdue / handoff / assigned, deep-linked to the thread anchor.
  for (const n of notes?.items ?? []) {
    const group: GroupKey = n.is_overdue ? 'overdue' : n.note_type === 'handoff' ? 'handoff' : 'assigned';
    const due = fmtDue(n.due_at);
    items.push({
      key: `note-${n.note_id}`,
      group,
      Icon: MessageSquare,
      to: n.host_id ? `/hosts/${n.host_id}#note-${n.note_id}` : '/operations',
      primary: n.host_ip || `Note #${n.note_id}`,
      primaryMono: !!n.host_ip,
      chip: n.note_type && n.note_type !== 'observation'
        ? { label: n.note_type, tone: 'secondary' }
        : null,
      meta: n.body_preview || '(no text)',
      right: due
        ? { label: due.label, tone: due.tone }
        : { label: fmtAgo(tsOf(n.updated_at)), tone: null },
      priorityRank: 2,
      tsEpoch: tsOf(n.due_at) || tsOf(n.updated_at),
    });
  }

  // In-review hosts.
  for (const h of queue?.items ?? []) {
    const sev = h.critical_vulns > 0 ? 'critical' : h.high_vulns > 0 ? 'high' : 'low';
    const findingsStr =
      h.critical_vulns || h.high_vulns
        ? `${h.critical_vulns ? `${h.critical_vulns} crit` : ''}${h.critical_vulns && h.high_vulns ? ' · ' : ''}${h.high_vulns ? `${h.high_vulns} high` : ''}`
        : 'no crit/high';
    items.push({
      key: `host-${h.host_id}`,
      group: 'in_review',
      Icon: ServerIcon,
      to: `/hosts/${h.host_id}`,
      primary: h.ip_address,
      primaryMono: true,
      chip: null,
      meta: `${h.hostname ? `${h.hostname} · ` : ''}${findingsStr} · ${h.open_port_count} port${h.open_port_count === 1 ? '' : 's'}`,
      right: { label: fmtAgo(tsOf(h.follow_updated_at)), tone: null },
      priorityRank: PRIORITY_RANK[sev] ?? 5,
      tsEpoch: tsOf(h.follow_updated_at),
    });
  }

  // Active findings the caller owns — their own group (link to the finding).
  for (const f of findings?.items ?? []) {
    items.push({
      key: `finding-${f.finding_id}`,
      group: 'findings',
      Icon: ShieldAlert,
      to: `/findings/${f.finding_id}`,
      primary: `#${f.finding_id}`,
      primaryMono: false,
      chip: { label: f.severity, tone: sevTone(f.severity) },
      meta: `${f.title} · ${f.host_count} host${f.host_count === 1 ? '' : 's'} · ${f.status.replace(/_/g, ' ')}`,
      right: { label: fmtAgo(tsOf(f.updated_at)), tone: null },
      priorityRank: PRIORITY_RANK[f.severity] ?? 5,
      tsEpoch: tsOf(f.updated_at),
    });
  }

  // Test-plan steps — assigned / in_review / triage; link to the entry.
  for (const t of tasks?.items ?? []) {
    const reasons = (t.reasons && t.reasons.length ? t.reasons : ['triage']) as MyTaskReason[];
    const primaryReason: MyTaskReason = reasons.includes('assigned')
      ? 'assigned'
      : reasons.includes('in_review')
        ? 'in_review'
        : 'triage';
    const group: GroupKey = primaryReason; // assigned|in_review|triage map 1:1
    items.push({
      key: `task-${t.entry_id}`,
      group,
      Icon: ClipboardList,
      to: `/test-plans/${t.plan_id}#entry-${t.entry_id}`,
      primary: t.host_ip,
      primaryMono: true,
      chip: { label: t.priority, tone: sevTone(t.priority) },
      meta: `${t.plan_title} · ${t.test_phase.replace(/_/g, ' ')}`,
      right: { label: fmtAgo(tsOf(t.updated_at)), tone: null },
      priorityRank: PRIORITY_RANK[t.priority] ?? 5,
      tsEpoch: tsOf(t.updated_at),
      // Only the unowned triage rows are claimable.
      claim: group === 'triage'
        ? { planId: t.plan_id, entryId: t.entry_id, updatedAt: t.updated_at }
        : undefined,
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

export interface MyWorkCardProps {
  queue: MyAttentionResponse | null;
  tasks: MyTasksResponse | null;
  notes: MyNotesResponse | null;
  findings: MyFindingsResponse | null;
  /** v5.223.0 — engagement-wide: untouched hosts worth a look (item 2). */
  investigate?: InvestigationQueueResponse | null;
  /** The server could not compute that queue: `investigate` is then an empty
   *  placeholder, which must not render as "every host has been touched". */
  investigateUnavailable?: boolean;
  /** v5.237.0 — reviewed hosts that are not done: "needs more evidence", or
   *  changed after the review. */
  followups?: ReviewFollowupsResponse | null;
  followupsUnavailable?: boolean;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** "updated …" beside the heading (components/UpdatedAt). */
  updated?: React.ReactNode;
}

const INVESTIGATE_PREVIEW = 5;

/** Carried to the host page so it offers "Back to my work" (v5.237.0): a host
 *  opened from Operations used to offer only "Back to Hosts". */
export const FROM_OPERATIONS = { state: { fromOperations: true } } as const;

const FOLLOWUPS_PREVIEW = 5;

/**
 * "Needs another look" (v5.237.0) — reviewed hosts that are not done.  A
 * review concluded "needs more evidence" is an open question stored as a
 * closed state, and a host that changed after its review has a conclusion
 * older than its evidence; both had left every queue.  The reviewer's own
 * come first.  Re-opening the review returns the host to the personal queue
 * and clears the stale conclusion.  Same stacked-row shape as "Worth a look":
 * the card is half the page wide.
 */
const FollowupsSection: React.FC<{
  data: ReviewFollowupsResponse;
  navigate: ReturnType<typeof useNavigate>;
  onReopened: () => void;
}> = ({ data, navigate, onReopened }) => {
  const toast = useToast();
  const [expanded, setExpanded] = React.useState(false);
  const [busyId, setBusyId] = React.useState<number | null>(null);
  const rows = expanded ? data.items : data.items.slice(0, FOLLOWUPS_PREVIEW);

  const reopen = async (row: ReviewFollowupRow) => {
    setBusyId(row.host_id);
    try {
      await followHost(row.host_id, 'in_review');
      toast.success(`${row.ip_address} is back in your review queue`, { autoHideMs: 2500 });
      onReopened();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not re-open the review.'));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="mt-md border-t border-border pt-sm">
      <div className="mb-xs flex flex-wrap items-center gap-xs">
        <p className="text-metadata font-semibold text-foreground">Needs another look</p>
        <Badge variant="warning">{data.total}</Badge>
        <span className="min-w-0 text-caption text-muted-foreground">
          reviewed hosts with an open question, or that changed after the review
          {data.total > data.mine_total ? ` — ${data.mine_total} yours` : ''}
        </span>
      </div>
      <ul className="divide-y divide-border">
        {rows.map((row) => (
          <li key={`${row.host_id}-${row.reviewer_id}`} className="flex items-start gap-sm py-xs">
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-baseline gap-xs">
                <button
                  type="button"
                  // The section's hosts ride along, so Next on the host page
                  // walks THIS list (v5.243.0).
                  onClick={() => navigate(
                    `/hosts/${row.host_id}`,
                    // The whole section, not the rows on screen: `rows` is the
                    // collapsed preview, and a queue built from it silently
                    // dropped the hosts behind "Show more".
                    fromOperationsQueue(data.items.map((r) => r.host_id), 'Needs another look',
                      { partial: data.total > data.items.length }),
                  )}
                  className="min-w-0 max-w-[60%] shrink-0 truncate rounded font-mono text-metadata text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  title={row.hostname ? `${row.ip_address} · ${row.hostname}` : row.ip_address}
                >
                  {row.ip_address}
                </button>
                {row.hostname && (
                  <span className="min-w-0 truncate text-caption text-muted-foreground" title={row.hostname}>
                    {row.hostname}
                  </span>
                )}
              </div>
              <p className="truncate text-caption text-muted-foreground">
                reviewed by {row.mine ? 'you' : row.reviewer || 'someone else'}
                {row.reviewed_at ? ` ${fmtAgo(tsOf(row.reviewed_at))}` : ''}
              </p>
              <ul className="mt-xxs flex flex-col gap-xxs">
                {row.reasons.map((r) => (
                  <li key={r.kind} className="line-clamp-2 break-words text-caption text-foreground" title={r.text}>
                    {r.text}
                  </li>
                ))}
              </ul>
              {row.review_summary && (
                <p className="line-clamp-2 break-words text-caption text-muted-foreground" title={row.review_summary}>
                  “{row.review_summary}”
                </p>
              )}
            </div>
            <div className="flex shrink-0 flex-col items-stretch gap-xxs">
              <Button
                size="sm"
                variant={row.mine ? 'default' : 'outline'}
                className="h-7"
                disabled={busyId === row.host_id}
                onClick={() => void reopen(row)}
                title={row.mine
                  ? 'Put this host back In Review under you. It returns to your queue and the old conclusion is cleared.'
                  : `Take this host into review yourself. ${row.reviewer ?? 'The reviewer'}'s conclusion stays on record.`}
              >
                {busyId === row.host_id ? 'Re-opening…' : row.mine ? 'Re-open review' : 'Review'}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {data.items.length > FOLLOWUPS_PREVIEW && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-xs rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {expanded ? 'Show fewer' : `Show ${data.items.length - FOLLOWUPS_PREVIEW} more`}
        </button>
      )}
      {data.total > data.items.length && (
        <p className="mt-xxs text-caption text-muted-foreground">
          Showing {data.items.length} of {data.total.toLocaleString()}.
        </p>
      )}
    </div>
  );
};

/**
 * "Worth a look" — the engagement-wide queue beneath the personal one
 * (design review item 2).  Hosts nobody has touched yet that carry an
 * observed weakness or a relevant change, each row saying why, what the
 * evidence is, and the next step.  Ordered by a stated tier (the legend
 * lists them); there is deliberately no composite priority number.
 */
const InvestigateSection: React.FC<{
  data: InvestigationQueueResponse;
  navigate: ReturnType<typeof useNavigate>;
  onTaken: () => void;
}> = ({ data, navigate, onTaken }) => {
  const toast = useToast();
  const [expanded, setExpanded] = React.useState(false);
  const [takingId, setTakingId] = React.useState<number | null>(null);
  const rows = expanded ? data.items : data.items.slice(0, INVESTIGATE_PREVIEW);

  // Take the host: mark it In Review under the caller.  The queue only lists
  // hosts with no follow row at all, so nobody else is reviewing it; after
  // this it leaves the queue and appears under "In review" above.  From
  // there the assist agent can plan against it ("hosts assigned to me or in
  // review by me").
  const take = async (row: InvestigateRow) => {
    setTakingId(row.host_id);
    try {
      await followHost(row.host_id, 'in_review');
      toast.success(`${row.ip_address} is now in your review queue`, { autoHideMs: 2500 });
      onTaken();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not take the host into review.'));
    } finally {
      setTakingId(null);
    }
  };

  return (
    <div className="mt-md border-t border-border pt-sm">
      <div className="mb-xs flex flex-wrap items-center gap-xs">
        <p className="text-metadata font-semibold text-foreground">Worth a look</p>
        <Badge variant="warning">{data.queue_total}</Badge>
        <span className="text-caption text-muted-foreground">
          hosts nobody is reviewing, with a reason — no review, assignment, note, plan entry or finding yet
        </span>
      </div>
      {data.items.length === 0 ? (
        <p className="text-caption text-muted-foreground">
          {data.untouched_total > 0
            ? `${data.untouched_total.toLocaleString()} untouched hosts, none with a weakness or change on record.`
            : 'Every host has been touched by someone.'}
        </p>
      ) : (
        <>
          {/* Stacked rows, not a table: this card is half the page wide, so four
              columns left ~110px for the next step — the sentence wrapped to
              many lines and "Upload evidence" spilled out of the card.  Text
              takes the row's width; the actions are a fixed column on the right. */}
          <ul className="divide-y divide-border">
            {rows.map((row) => (
              <li key={row.host_id} data-tier={row.tier} className="flex items-start gap-sm py-xs">
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 items-baseline gap-xs">
                    <button
                      type="button"
                      onClick={() => navigate(
                        `/hosts/${row.host_id}`,
                        fromOperationsQueue(data.items.map((r) => r.host_id), 'Worth a look',
                          { partial: data.queue_total > data.items.length }),
                      )}
                      className="min-w-0 max-w-[60%] shrink-0 truncate rounded font-mono text-metadata text-foreground hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      title={row.hostname ? `${row.ip_address} · ${row.hostname}` : row.ip_address}
                    >
                      {row.ip_address}
                    </button>
                    {row.hostname && (
                      <span className="min-w-0 truncate text-caption text-muted-foreground" title={row.hostname}>
                        {row.hostname}
                      </span>
                    )}
                  </div>
                  <p className="truncate text-caption font-medium text-warning" title={row.tier_label}>
                    {row.tier_label}
                  </p>
                  <ul className="mt-xxs flex flex-col gap-xxs">
                    {row.reasons.map((r) => (
                      <li key={r.kind} className="line-clamp-2 break-words text-caption text-foreground" title={r.text}>
                        {r.text}
                      </li>
                    ))}
                  </ul>
                  <p
                    className="mt-xxs truncate text-caption text-muted-foreground"
                    title={row.evidence.sources.join(', ') || 'no scan recorded'}
                  >
                    <span>{row.evidence.sources.length ? row.evidence.sources.join(', ') : 'no scan recorded'}</span>
                    {' · '}
                    {row.evidence.last_seen ? `seen ${fmtAgo(tsOf(row.evidence.last_seen))}` : 'never seen'}
                    {' · '}
                    {row.evidence.confirmation === 'scanner'
                      ? 'scanner-reported, unconfirmed'
                      : row.evidence.confirmation === 'finding'
                        ? 'has a finding'
                        : 'tested'}
                  </p>
                  <p className="line-clamp-2 break-words text-caption text-muted-foreground" title={row.next_action.text}>
                    Next: {row.next_action.text}
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-stretch gap-xxs">
                  <Button
                    size="sm"
                    variant={row.next_action.kind === 'review' ? 'default' : 'outline'}
                    className="h-7"
                    disabled={takingId === row.host_id}
                    onClick={() => void take(row)}
                    title="Mark this host In Review under you. It leaves this queue and joins your personal one."
                  >
                    {takingId === row.host_id ? 'Taking…' : 'Review'}
                  </Button>
                  {row.next_action.kind === 'collect' && (
                    <Button size="sm" variant="outline" className="h-7" onClick={() => navigate('/scans')}>
                      Upload evidence
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-xs flex flex-wrap items-center gap-x-md gap-y-xxs">
            {data.items.length > INVESTIGATE_PREVIEW && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {expanded ? 'Show fewer' : `Show ${data.items.length - INVESTIGATE_PREVIEW} more`}
              </button>
            )}
            <span className="text-caption text-muted-foreground" title={`Tiers, in order: ${data.tiers.join(' › ')}`}>
              Showing {rows.length} of {data.queue_total.toLocaleString()} · ordered by tier: {data.tiers.join(' › ')}
            </span>
          </div>
        </>
      )}
    </div>
  );
};

// Lowered from 14: the card is now the focused action queue (recent-notes
// activity moved to its own card) and shares a row with it, so a tighter
// Rows shown per category before its own "Show more" (six categories).
const GROUP_PREVIEW = 3;

export const MyWorkCard: React.FC<MyWorkCardProps> = ({
  queue, tasks, notes, findings, investigate = null, investigateUnavailable = false,
  followups = null, followupsUnavailable = false,
  loading, error, onRetry, updated,
}) => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  // v5.237.0 — each category expands by itself.  One global "show more" over
  // a merged list meant reaching "In review" required paging through every
  // category ranked above it.
  const [expandedGroups, setExpandedGroups] = React.useState<Set<GroupKey>>(new Set());
  const [claimingId, setClaimingId] = React.useState<number | null>(null);

  const items = React.useMemo(
    () => buildItems(queue, tasks, notes, findings),
    [queue, tasks, notes, findings],
  );
  const groups = React.useMemo(
    () => GROUP_ORDER
      .map((key) => ({ key, rows: items.filter((it) => it.group === key) }))
      .filter((g) => g.rows.length > 0),
    [items],
  );

  const handleClaim = async (c: { planId: number; entryId: number; updatedAt: string | null }) => {
    if (user?.id == null) return;
    setClaimingId(c.entryId);
    try {
      await updateTestPlanEntry(c.planId, c.entryId, {
        assigned_to_id: user.id,
        expected_updated_at: c.updatedAt ?? undefined,
      });
      toast.success("Claimed — it's now in your assigned work", { autoHideMs: 2000 });
      onRetry(); // refetch so it moves from Available → Assigned
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to claim.'));
    } finally {
      setClaimingId(null);
    }
  };

  // Authoritative server totals (the merged list is capped at the per-source
  // fetch limits, so it must NOT stand in for the totals).  §27: the personal
  // total EXCLUDES unassigned triage (shared work isn't "mine") and INCLUDES
  // owned findings.
  const availableCount = tasks?.reason_counts?.triage ?? 0;
  const totalCount =
    (notes?.total_open ?? 0) +
    (queue?.in_review_count ?? 0) +
    Math.max(0, (tasks?.total_open ?? 0) - availableCount) +
    (findings?.total_open ?? 0);
  const overdue = notes?.overdue_count ?? 0;

  // The server's count for a category, where one source owns it outright.
  // "Handoffs" and "Assigned" mix notes and plan steps whose totals the API
  // reports under other groupings, so they state what is loaded and no more.
  const serverTotal: Partial<Record<GroupKey, number>> = {
    overdue: notes?.overdue_count,
    findings: findings?.total_open,
    in_review: queue?.in_review_count,
    triage: tasks?.reason_counts?.triage,
  };
  // Where the whole of a category can be listed, filtered to the caller.
  const viewAll: Partial<Record<GroupKey, { to: string; label: string }>> = {
    in_review: { to: buildHostsUrl({ q: 'follow:in_review' }), label: 'All hosts I have in review' },
    findings: { to: '/findings?owner=me', label: 'All findings I own' },
  };

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <div className="mb-sm flex flex-wrap items-center gap-xs">
          <p className="text-subheading font-semibold text-foreground">My work</p>
          {totalCount > 0 && <Badge variant="secondary">{totalCount}</Badge>}
          {overdue > 0 && <Badge variant="destructive">{overdue} overdue</Badge>}
          {availableCount > 0 && <Badge variant="outline">{availableCount} to claim</Badge>}
          {/* v5.243.0 — when the workbench last loaded. */}
          <div className="ml-auto">{updated}</div>
        </div>

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
          <Alert variant="info">
            <AlertDescription>
              Nothing in your queue. Work shows here when you're <strong>assigned a note</strong>,
              {' '}mark a host <strong>In Review</strong>, or a test-plan step is assigned to you.
            </AlertDescription>
          </Alert>
        ) : (
          <div className="flex flex-col gap-sm">
            {groups.map((g) => {
              const open = expandedGroups.has(g.key);
              const rows = open ? g.rows : g.rows.slice(0, GROUP_PREVIEW);
              const total = serverTotal[g.key];
              const beyond = total != null && total > g.rows.length ? total - g.rows.length : 0;
              const all = viewAll[g.key];
              return (
                <section key={g.key} aria-label={GROUP_META[g.key].label}>
                  <div className="mb-xxs flex flex-wrap items-center gap-xs">
                    <Badge variant={GROUP_META[g.key].tone}>{GROUP_META[g.key].label}</Badge>
                    {/* The full count where the server has one; never the
                        number of rows that happen to be on screen. */}
                    <span className="text-caption text-muted-foreground">
                      {(total ?? g.rows.length).toLocaleString()}
                    </span>
                    {all && (
                      <button
                        type="button"
                        onClick={() => navigate(all.to)}
                        className="ml-auto rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        title={all.label}
                      >
                        View all
                      </button>
                    )}
                  </div>
                  <ul className="flex flex-col">
                    {rows.map((it) => (
                <li key={it.key}>
                  <div className="flex items-center gap-xxs">
                    <button
                      type="button"
                      onClick={() => navigate(
                        it.to,
                        // A host row carries its category's hosts as the queue;
                        // a finding / plan-step row is not a host page.
                        hostIdOf(it.to) != null
                          ? fromOperationsQueue(g.rows.map((r) => hostIdOf(r.to)), GROUP_META[g.key].label,
                            { partial: beyond > 0 })
                          : undefined,
                      )}
                      className={cn(
                        'flex min-w-0 flex-1 items-center gap-xs px-xs py-xxs text-left',
                        'rounded-control hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      )}
                    >
                      <it.Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                      {it.chip && <Badge variant={it.chip.tone}>{it.chip.label}</Badge>}
                      <span
                        className={cn(
                          'shrink-0 text-metadata font-medium text-foreground',
                          it.primaryMono && 'font-mono',
                        )}
                      >
                        {it.primary}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-metadata text-muted-foreground">
                        — {it.meta}
                      </span>
                      {it.right.label && (
                        it.right.tone ? (
                          <Badge variant={it.right.tone}>{it.right.label}</Badge>
                        ) : (
                          <span className="shrink-0 text-caption text-muted-foreground">{it.right.label}</span>
                        )
                      )}
                    </button>
                    {it.claim && (
                      <Button
                        size="sm" variant="outline" className="h-7 shrink-0"
                        disabled={claimingId === it.claim.entryId}
                        onClick={() => handleClaim(it.claim!)}
                      >
                        {claimingId === it.claim.entryId ? 'Claiming…' : 'Claim'}
                      </Button>
                    )}
                  </div>
                </li>
                    ))}
                  </ul>
                  {(g.rows.length > GROUP_PREVIEW || beyond > 0) && (
                    <div className="mt-xxs flex flex-wrap items-center gap-x-md gap-y-xxs pl-xs">
                      {g.rows.length > GROUP_PREVIEW && (
                        <button
                          type="button"
                          aria-expanded={open}
                          onClick={() => setExpandedGroups((prev) => {
                            const next = new Set(prev);
                            if (next.has(g.key)) next.delete(g.key); else next.add(g.key);
                            return next;
                          })}
                          className="rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {open ? 'Show fewer' : `Show ${g.rows.length - GROUP_PREVIEW} more`}
                        </button>
                      )}
                      {/* This card loads a capped slice per source. What lies
                          beyond it is named, with the view that lists it —
                          not left "in their source views" for the operator
                          to go and find. */}
                      {beyond > 0 && (
                        <span className="text-caption text-muted-foreground">
                          {beyond.toLocaleString()} more not loaded here
                          {all ? ' — use View all' : ''}
                        </span>
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        )}

        {!loading && !error && followupsUnavailable && (
          <div className="mt-md border-t border-border pt-sm">
            <p className="text-metadata font-semibold text-foreground">Needs another look</p>
            <div role="alert" className="mt-xxs flex flex-wrap items-center gap-xs text-caption text-warning">
              <span className="min-w-0 flex-1">
                Unavailable — reviewed hosts could not be checked for open questions or later changes.
              </span>
              <Button size="sm" variant="outline" onClick={onRetry}>Retry</Button>
            </div>
          </div>
        )}
        {!loading && !error && !followupsUnavailable && followups && followups.items.length > 0 && (
          <FollowupsSection data={followups} navigate={navigate} onReopened={onRetry} />
        )}

        {!loading && !error && investigateUnavailable && (
          <div className="mt-md border-t border-border pt-sm">
            <p className="text-metadata font-semibold text-foreground">Worth a look</p>
            <div role="alert" className="mt-xxs flex flex-wrap items-center gap-xs text-caption text-warning">
              <span className="min-w-0 flex-1">
                Unavailable — this queue could not be computed, so it says nothing about whether
                hosts are waiting. Your own work above is unaffected.
              </span>
              <Button size="sm" variant="outline" onClick={onRetry}>Retry</Button>
            </div>
          </div>
        )}
        {!loading && !error && !investigateUnavailable && investigate && (
          <InvestigateSection data={investigate} navigate={navigate} onTaken={onRetry} />
        )}
      </CardContent>
    </Card>
  );
};

export default MyWorkCard;
