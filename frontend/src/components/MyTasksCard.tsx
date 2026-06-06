/**
 * Personal task list card — extracted from Dashboard.tsx.
 * Per-user; rendered in Operations under the Mine toggle.
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { getMyTasks } from '../services/api';
import type { MyTaskItem, MyTasksResponse } from '../services/api';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription } from './ui/alert';
import { Separator } from './ui/separator';
import { cn } from '../utils/cn';

type BadgeTone = 'destructive' | 'warning' | 'info' | 'muted';
const priorityTone = (p: string): BadgeTone =>
  p === 'critical' ? 'destructive' : p === 'high' ? 'warning' : p === 'medium' ? 'info' : 'muted';

export const MyTasksCard: React.FC = () => {
  const navigate = useNavigate();
  const [data, setData] = useState<MyTasksResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getMyTasks(15)
      .then((resp) => { if (!cancelled) setData(resp); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const items: MyTaskItem[] = data?.items ?? [];
  const total = data?.total_open ?? 0;

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <div className="mb-sm flex items-start justify-between gap-sm">
          <div>
            <p className="text-subheading font-semibold text-foreground">My Tasks</p>
            <p className="text-caption text-muted-foreground">
              Test plan entries on hosts you're reviewing
              {total > 0 && <> · showing {items.length} of {total}</>}
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={() => navigate('/test-plans')}>
            All Plans
          </Button>
        </div>
        {loading ? (
          <div className="flex items-center gap-xs">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Loading tasks…</p>
          </div>
        ) : items.length === 0 ? (
          <Alert variant="info">
            <AlertDescription>
              No open tasks. Tasks here are non-terminal test plan entries on the hosts in your
              queue — mark a host <strong>In Review</strong>, then any open entries from approved
              plans on that host appear here.
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
