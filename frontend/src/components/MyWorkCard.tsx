/**
 * Unified "My work" list (RV-DESIGN2) — merges the two personal surfaces
 * into ONE prioritised list so an analyst has a single queue to work, not
 * two cards to reconcile:
 *   - HOST rows  = hosts the caller marked In Review (from my_queue)
 *   - TASK rows  = test-plan steps the caller owns (from my_tasks):
 *                  assigned / on an in-review host / unassigned crit-high triage
 *
 * Both arrays already arrive in the single /workbench response, so the
 * merge is client-side; rows are sorted by reason (assigned → in_review →
 * triage) then priority (critical → info) then recency.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, RefreshCw, ServerIcon, SquareArrowOutUpRight } from 'lucide-react';
import type {
  MyAttentionResponse,
  MyTaskReason,
  MyTasksResponse,
} from '../services/api';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { Separator } from './ui/separator';
import { cn } from '../utils/cn';

type BadgeTone = 'destructive' | 'warning' | 'info' | 'muted' | 'secondary';

const REASON_META: Record<MyTaskReason, { label: string; tone: BadgeTone; rank: number }> = {
  assigned: { label: 'Assigned', tone: 'info', rank: 0 },
  in_review: { label: 'In review', tone: 'muted', rank: 1 },
  triage: { label: 'Triage', tone: 'warning', rank: 2 },
};
const PRIORITY_RANK: Record<string, number> = {
  critical: 0, high: 1, medium: 2, low: 3, info: 4,
};
const priorityTone = (p: string): BadgeTone =>
  p === 'critical' ? 'destructive' : p === 'high' ? 'warning' : p === 'medium' ? 'info' : 'muted';

interface WorkItem {
  key: string;
  kind: 'host' | 'task';
  hostId: number;
  ip: string;
  hostname: string | null;
  reason: MyTaskReason;       // primary reason (drives grouping/sort)
  allReasons: MyTaskReason[]; // tasks can carry several
  priority: string;          // critical/high/… (severity for hosts)
  subtitle: string;
  tsEpoch: number;           // recency tiebreaker (desc)
}

function fmtAgo(value?: string | null): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

const tsOf = (v?: string | null): number => {
  if (!v) return 0;
  const t = new Date(v).getTime();
  return Number.isNaN(t) ? 0 : t;
};

function buildItems(
  queue: MyAttentionResponse | null,
  tasks: MyTasksResponse | null,
): WorkItem[] {
  const items: WorkItem[] = [];

  for (const h of queue?.items ?? []) {
    // A host's "priority" is its worst finding severity (for ranking).
    const severity = h.critical_vulns > 0 ? 'critical' : h.high_vulns > 0 ? 'high' : 'low';
    const findings =
      h.critical_vulns > 0 || h.high_vulns > 0
        ? `${h.critical_vulns ? `${h.critical_vulns} crit` : ''}${
            h.critical_vulns && h.high_vulns ? ' · ' : ''
          }${h.high_vulns ? `${h.high_vulns} high` : ''}`
        : 'no critical/high findings';
    items.push({
      key: `host-${h.host_id}`,
      kind: 'host',
      hostId: h.host_id,
      ip: h.ip_address,
      hostname: h.hostname,
      reason: 'in_review',
      allReasons: ['in_review'],
      priority: severity,
      subtitle: `${findings} · ${h.open_port_count} port${h.open_port_count === 1 ? '' : 's'}`,
      tsEpoch: tsOf(h.follow_updated_at),
    });
  }

  for (const t of tasks?.items ?? []) {
    const reasons = (t.reasons && t.reasons.length ? t.reasons : ['triage']) as MyTaskReason[];
    // Primary reason = strongest (lowest rank) the task satisfies.
    const primary = [...reasons].sort((a, b) => REASON_META[a].rank - REASON_META[b].rank)[0];
    items.push({
      key: `task-${t.entry_id}`,
      kind: 'task',
      hostId: t.host_id,
      ip: t.host_ip,
      hostname: t.host_hostname,
      reason: primary,
      allReasons: reasons,
      priority: t.priority,
      subtitle: `${t.plan_title} · ${t.test_phase.replace('_', ' ')}`,
      tsEpoch: tsOf(t.updated_at),
    });
  }

  items.sort((a, b) => {
    const r = REASON_META[a.reason].rank - REASON_META[b.reason].rank;
    if (r !== 0) return r;
    const p = (PRIORITY_RANK[a.priority] ?? 5) - (PRIORITY_RANK[b.priority] ?? 5);
    if (p !== 0) return p;
    return b.tsEpoch - a.tsEpoch;
  });
  return items;
}

export interface MyWorkCardProps {
  queue: MyAttentionResponse | null;
  tasks: MyTasksResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

const PREVIEW = 12;

export const MyWorkCard: React.FC<MyWorkCardProps> = ({ queue, tasks, loading, error, onRetry }) => {
  const navigate = useNavigate();
  const [expanded, setExpanded] = React.useState(false);

  const items = React.useMemo(() => buildItems(queue, tasks), [queue, tasks]);
  const shown = expanded ? items : items.slice(0, PREVIEW);

  // Non-zero reason breakdown across the merged set.
  // Authoritative totals from the server (the merged item list is capped at
  // the per-source fetch limits, so it must NOT be treated as the total —
  // review CR3-#1).  reason_counts is the server's task breakdown.
  const totalHosts = queue?.in_review_count ?? 0;
  const totalTasks = tasks?.total_open ?? 0;
  const shownHosts = items.filter((i) => i.kind === 'host').length;
  const shownTasks = items.filter((i) => i.kind === 'task').length;
  const moreHosts = totalHosts > shownHosts;
  const moreTasks = totalTasks > shownTasks;
  const taskReasons = tasks?.reason_counts;

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <div className="mb-sm flex items-start justify-between gap-sm">
          <div className="min-w-0">
            <p className="text-subheading font-semibold text-foreground">My work</p>
            <p className="text-caption text-muted-foreground">
              Hosts you're investigating and the test-plan steps you own, in one
              prioritised queue.
            </p>
            {(totalHosts > 0 || totalTasks > 0) && (
              <div className="mt-xxs flex flex-wrap items-center gap-xxs">
                <Badge variant="secondary">{totalHosts} In Review</Badge>
                <Badge variant="outline">{totalTasks} task{totalTasks === 1 ? '' : 's'}</Badge>
                {taskReasons && taskReasons.assigned > 0 && (
                  <Badge variant={REASON_META.assigned.tone}>{taskReasons.assigned} assigned</Badge>
                )}
                {taskReasons && taskReasons.triage > 0 && (
                  <Badge variant={REASON_META.triage.tone}>{taskReasons.triage} triage</Badge>
                )}
              </div>
            )}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center gap-xs">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Loading your work…</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTitle>Couldn't load your work</AlertTitle>
            <AlertDescription>
              <p className="break-words">{error}</p>
              <Button size="sm" variant="outline" className="mt-xs" onClick={onRetry}>
                <RefreshCw className="size-3.5" aria-hidden />
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : items.length === 0 ? (
          <Alert variant="info">
            <AlertDescription>
              Nothing in your queue. Work shows here when you mark a host{' '}
              <strong>In Review</strong>, a test-plan step is <strong>assigned</strong> to
              you, or unassigned <strong>critical/high</strong> work needs triage.
            </AlertDescription>
          </Alert>
        ) : (
          <ul className="flex flex-col">
            {shown.map((it, idx) => (
              <li key={it.key}>
                {idx > 0 && <Separator />}
                <button
                  type="button"
                  onClick={() => navigate(`/hosts/${it.hostId}`)}
                  className={cn(
                    'flex w-full flex-col gap-xxs px-xs py-sm text-left',
                    'rounded-control hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  )}
                >
                  <div className="flex flex-wrap items-center gap-xs">
                    <Badge variant={it.kind === 'host' ? 'secondary' : 'outline'} className="gap-xxs">
                      {it.kind === 'host' && <ServerIcon className="size-3" aria-hidden />}
                      {it.kind === 'host' ? 'Host' : 'Task'}
                    </Badge>
                    {it.kind === 'task' && (
                      <Badge variant={priorityTone(it.priority)}>{it.priority}</Badge>
                    )}
                    <p className="truncate font-mono text-metadata font-medium text-foreground">
                      {it.ip}
                    </p>
                    {(['assigned', 'in_review', 'triage'] as MyTaskReason[])
                      .filter((r) => it.allReasons.includes(r))
                      .map((r) => (
                        <Badge key={r} variant={REASON_META[r].tone}>
                          {REASON_META[r].label}
                        </Badge>
                      ))}
                    {it.tsEpoch > 0 && (
                      <span className="ml-auto text-caption text-muted-foreground">
                        {fmtAgo(new Date(it.tsEpoch).toISOString())}
                      </span>
                    )}
                  </div>
                  <p className="truncate text-metadata text-muted-foreground">
                    {it.hostname ? `${it.hostname} · ` : ''}{it.subtitle}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Footer — expand the FETCHED rows, plus honest "X of Y" totals and
            links to the complete lists.  The merged list is capped at the
            fetch limits, so "Show more" never implies it's everything
            (review CR3-#1). */}
        {!loading && !error && items.length > 0 && (
          <div className="mt-sm flex flex-wrap items-center gap-x-md gap-y-xxs">
            {items.length > PREVIEW && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
              >
                {expanded ? 'Show fewer' : `Show ${items.length - PREVIEW} more here`}
              </button>
            )}
            <span className="text-caption text-muted-foreground">
              Showing {shownHosts} of {totalHosts} host{totalHosts === 1 ? '' : 's'} ·{' '}
              {shownTasks} of {totalTasks} task{totalTasks === 1 ? '' : 's'}
            </span>
            <div className="ml-auto flex items-center gap-md">
              {moreHosts && (
                <button
                  type="button"
                  onClick={() => navigate('/hosts?follow_status=in_review')}
                  className="inline-flex items-center gap-xxs text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                >
                  All In Review <SquareArrowOutUpRight className="size-3" aria-hidden />
                </button>
              )}
              {moreTasks && (
                <button
                  type="button"
                  onClick={() => navigate('/test-plans')}
                  className="inline-flex items-center gap-xxs text-caption text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                >
                  All tasks <SquareArrowOutUpRight className="size-3" aria-hidden />
                </button>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default MyWorkCard;
