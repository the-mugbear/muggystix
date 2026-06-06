/**
 * Personal task list card. Controlled (prop-driven, v5.7.0 / refactor P2):
 * Operations owns one /workbench fetch and passes this section's data in,
 * so the page-level Refresh coordinates every card from a single request.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, RefreshCw } from 'lucide-react';
import type { MyTaskItem, MyTaskReason, MyTasksResponse } from '../services/api';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { Separator } from './ui/separator';
import { cn } from '../utils/cn';

type BadgeTone = 'destructive' | 'warning' | 'info' | 'muted';
const priorityTone = (p: string): BadgeTone =>
  p === 'critical' ? 'destructive' : p === 'high' ? 'warning' : p === 'medium' ? 'info' : 'muted';

// Why an entry is in your queue. Order here = display order on a row.
const REASON_META: Record<MyTaskReason, { label: string; tone: BadgeTone }> = {
  assigned: { label: 'Assigned', tone: 'info' },
  in_review: { label: 'In review', tone: 'muted' },
  triage: { label: 'Triage', tone: 'warning' },
};
const REASON_ORDER: MyTaskReason[] = ['assigned', 'in_review', 'triage'];

export interface MyTasksCardProps {
  data: MyTasksResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

export const MyTasksCard: React.FC<MyTasksCardProps> = ({ data, loading, error, onRetry }) => {
  const navigate = useNavigate();

  const items: MyTaskItem[] = data?.items ?? [];
  const total = data?.total_open ?? 0;
  const counts = data?.reason_counts;
  // Non-zero bucket breakdown — buckets overlap, so this is context, not a
  // sum of total.
  const countChips = counts
    ? REASON_ORDER.filter((r) => counts[r] > 0).map((r) => ({ r, n: counts[r] }))
    : [];

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <div className="mb-sm flex items-start justify-between gap-sm">
          <div className="min-w-0">
            <p className="text-subheading font-semibold text-foreground">My Tasks</p>
            <p className="text-caption text-muted-foreground">
              Assigned to you, on hosts you're reviewing, and unassigned critical/high
              {total > 0 && <> · showing {items.length} of {total}</>}
            </p>
            {countChips.length > 0 && (
              <div className="mt-xxs flex flex-wrap items-center gap-xxs">
                {countChips.map(({ r, n }) => (
                  <Badge key={r} variant={REASON_META[r].tone}>
                    {n} {REASON_META[r].label.toLowerCase()}
                  </Badge>
                ))}
              </div>
            )}
          </div>
          <Button size="sm" variant="outline" className="shrink-0" onClick={() => navigate('/test-plans')}>
            All Plans
          </Button>
        </div>
        {loading ? (
          <div className="flex items-center gap-xs">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Loading tasks…</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTitle>Couldn't load your tasks</AlertTitle>
            <AlertDescription>
              <p className="break-words">{error}</p>
              <Button
                size="sm"
                variant="outline"
                className="mt-xs"
                onClick={onRetry}
              >
                <RefreshCw className="size-3.5" aria-hidden />
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : items.length === 0 ? (
          <Alert variant="info">
            <AlertDescription>
              No open tasks. This queue collects open test-plan entries that are{' '}
              <strong>assigned to you</strong>, on a host you've marked <strong>In Review</strong>,
              or <strong>unassigned critical/high</strong> work awaiting triage.
            </AlertDescription>
          </Alert>
        ) : (
          <ul className="flex flex-col">
            {items.slice(0, 8).map((task, idx) => (
              <li key={task.entry_id}>
                {idx > 0 && <Separator />}
                <button
                  type="button"
                  onClick={() => navigate(`/hosts/${task.host_id}`)}
                  className={cn(
                    'flex w-full flex-col gap-xxs px-xs py-sm text-left',
                    'hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-control',
                  )}
                >
                  <div className="flex flex-wrap items-center gap-xs">
                    <Badge variant={priorityTone(task.priority)}>{task.priority}</Badge>
                    <p className="truncate font-mono text-metadata font-medium text-foreground">
                      {task.host_ip}
                    </p>
                    <p className="text-caption text-muted-foreground">
                      {task.proposed_test_count} test{task.proposed_test_count === 1 ? '' : 's'}
                    </p>
                    {REASON_ORDER.filter((r) => task.reasons.includes(r)).map((r) => (
                      <Badge key={r} variant={REASON_META[r].tone}>
                        {REASON_META[r].label}
                      </Badge>
                    ))}
                  </div>
                  <p className="truncate text-metadata text-muted-foreground">
                    {task.plan_title} · {task.test_phase.replace('_', ' ')}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
};

export default MyTasksCard;
