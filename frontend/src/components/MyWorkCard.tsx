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
  AlertTriangle,
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
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

const INVESTIGATE_PREVIEW = 5;

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
                      onClick={() => navigate(`/hosts/${row.host_id}`)}
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
// default preview keeps it scannable; "Show more" reveals the rest.
const PREVIEW = 8;

export const MyWorkCard: React.FC<MyWorkCardProps> = ({
  queue, tasks, notes, findings, investigate = null, investigateUnavailable = false,
  loading, error, onRetry,
}) => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const toast = useToast();
  const [expanded, setExpanded] = React.useState(false);
  const [claimingId, setClaimingId] = React.useState<number | null>(null);

  const items = React.useMemo(
    () => buildItems(queue, tasks, notes, findings),
    [queue, tasks, notes, findings],
  );
  const shown = expanded ? items : items.slice(0, PREVIEW);

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

  // Group-count headers, computed over the SHOWN rows (the visible grouping).
  const shownGroupCounts = React.useMemo(() => {
    const m = new Map<GroupKey, number>();
    for (const it of shown) m.set(it.group, (m.get(it.group) ?? 0) + 1);
    return m;
  }, [shown]);

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <div className="mb-sm flex flex-wrap items-center gap-xs">
          <p className="text-subheading font-semibold text-foreground">My work</p>
          {totalCount > 0 && <Badge variant="secondary">{totalCount}</Badge>}
          {overdue > 0 && <Badge variant="destructive">{overdue} overdue</Badge>}
          {availableCount > 0 && <Badge variant="outline">{availableCount} to claim</Badge>}
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
          <ul className="flex flex-col">
            {shown.map((it, idx) => {
              const newGroup = idx === 0 || shown[idx - 1].group !== it.group;
              return (
                <li key={it.key}>
                  {newGroup && (
                    <div className="mb-xxs mt-sm flex items-center gap-xs first:mt-0">
                      <Badge variant={GROUP_META[it.group].tone}>{GROUP_META[it.group].label}</Badge>
                      <span className="text-caption text-muted-foreground">
                        {shownGroupCounts.get(it.group)}
                      </span>
                    </div>
                  )}
                  <div className="flex items-center gap-xxs">
                    <button
                      type="button"
                      onClick={() => navigate(it.to)}
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
              );
            })}
          </ul>
        )}

        {!loading && !error && items.length > 0 && (
          <div className="mt-sm flex flex-wrap items-center gap-x-md gap-y-xxs">
            {items.length > PREVIEW && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="rounded text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {expanded ? 'Show fewer' : `Show ${items.length - PREVIEW} more`}
              </button>
            )}
            {/* The Show-more toggle only reaches the LOADED items (the merged
                list is capped at per-source fetch limits), so the footer must
                not imply totalCount is reachable here.  When the server total
                exceeds what's loaded, point to the source surfaces that own
                the remainder rather than advertising an unreachable count. */}
            <span className="text-caption text-muted-foreground">
              Showing {shown.length} of {items.length}
              {totalCount > items.length &&
                ` · ${totalCount - items.length} more open in their source views`}
            </span>
          </div>
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
