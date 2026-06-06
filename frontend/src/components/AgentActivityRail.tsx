/**
 * Floating agent-activity rail — beta.2.
 *
 * A pill-shaped trigger in the topbar opens a Popover with the
 * project's recent agent sessions (recon / plan generation /
 * execution).  Designed to be ambient awareness, not a primary
 * surface — the trigger is a small badge with a count; clicking
 * shows the last ~8 sessions; a "View all" link takes you to the
 * full /agent-activity timeline.
 *
 * Polls every 60s while the trigger is rendered.  Renders nothing
 * (no trigger, no popover) when:
 *   - the user isn't authenticated, OR
 *   - no project is loaded, OR
 *   - the project has zero agent sessions on file
 * so the topbar stays uncluttered for unused projects.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
import {
  Bot,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleDot,
  ExternalLink,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import {
  AgentSessionFilters,
  AgentSessionRow,
  listAgentSessions,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useProject } from '../contexts/ProjectContext';
import { cn } from '../utils/cn';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

// Cadence when there's something live worth watching.
const ACTIVE_POLL_MS = 60_000;
// Slower cadence when the popover is closed and there are no active
// sessions — drops the rail's idle network/render cost ~5×.  Audit H19.
const IDLE_POLL_MS = 5 * 60_000;
const PEEK_LIMIT = 8;

const KIND_LABEL: Record<string, string> = {
  recon: 'Recon',
  plan_generation: 'Plan generation',
  execution: 'Execution',
};

const statusIcon = (status: string) => {
  const s = status.toLowerCase();
  if (s === 'active' || s === 'in_progress') {
    return <Loader2 className="size-3 animate-spin text-info" aria-hidden />;
  }
  if (s === 'completed' || s === 'success') {
    return <CircleCheck className="size-3 text-success" aria-hidden />;
  }
  if (s === 'failed' || s === 'error' || s === 'rejected') {
    return <CircleAlert className="size-3 text-destructive" aria-hidden />;
  }
  return <CircleDot className="size-3 text-muted-foreground" aria-hidden />;
};

const fmtAgo = (iso?: string | null): string => {
  if (!iso) return '';
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 0) return 'just now';
    const sec = Math.floor(ms / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return `${Math.floor(hr / 24)}d ago`;
  } catch {
    return '';
  }
};

const detailPath = (row: AgentSessionRow): string => {
  switch (row.kind) {
    case 'recon':
      return `/recon/runs/${row.id}`;
    case 'execution':
      return `/executions/${row.id}`;
    case 'plan_generation':
      return row.test_plan_id ? `/test-plans/${row.test_plan_id}` : '/test-plans';
    default:
      return '/agent-activity';
  }
};

const AgentActivityRail: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const { currentProject } = useProject();
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<AgentSessionRow[]>([]);
  const [activeCount, setActiveCount] = useState<number>(0);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    if (!isAuthenticated || !currentProject) return;
    setLoading(true);
    try {
      // Two cheap requests in parallel — the recent list + just the
      // active count for the badge.  Both hit the same endpoint with
      // different filters.
      const [recent, active] = await Promise.all([
        listAgentSessions({ limit: PEEK_LIMIT } satisfies AgentSessionFilters),
        listAgentSessions({ status: 'active', limit: 1 } satisfies AgentSessionFilters),
      ]);
      setSessions(recent.sessions);
      setActiveCount(active.total);
      setLoaded(true);
    } catch {
      // Silent — the rail is ambient; failing is the same as having
      // nothing to show.  Notifications surface API outages through
      // their own polling path.
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated, currentProject]);

  useEffect(() => {
    if (!isAuthenticated || !currentProject) {
      setSessions([]);
      setActiveCount(0);
      setLoaded(false);
      return;
    }
    fetchData();
  }, [isAuthenticated, currentProject, fetchData]);

  // Visibility-gated polling — background tabs no longer hammer the
  // API (audit CRIT-17). Cadence still tightens when the popover is
  // open or there's a live session to track; otherwise backs off to
  // 5min so the rail isn't waking the browser every minute on a
  // normal page.
  const cadence = open || activeCount > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS;
  useVisibilityPoll(
    fetchData,
    cadence,
    isAuthenticated && !!currentProject,
  );

  // When the popover opens, fetch fresh data immediately so the user
  // sees the latest state without waiting for the next poll tick.
  useEffect(() => {
    if (open) fetchData();
  }, [open, fetchData]);

  const totalShown = sessions.length;

  const dotTone = useMemo(() => {
    if (activeCount > 0) return 'bg-info';
    return null;
  }, [activeCount]);

  // Hide the trigger entirely until the first fetch returns AND we
  // know the project has at least one agent session on file.
  if (!isAuthenticated || !currentProject) return null;
  if (loaded && totalShown === 0 && activeCount === 0) return null;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label={
                activeCount > 0
                  ? `Agent activity — ${activeCount} active session${activeCount === 1 ? '' : 's'}`
                  : 'Agent activity'
              }
              className={cn(
                'relative inline-flex size-8 items-center justify-center rounded-control border border-border bg-card text-foreground',
                'hover:border-primary/30 hover:bg-accent',
                'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
              )}
            >
              <Bot className="size-4" aria-hidden />
              {dotTone && (
                <span
                  className={cn(
                    // Audit RSP·L1 — use design-token spacing instead
                    // of arbitrary 0.5 values.
                    'absolute right-xxs top-xxs inline-block size-2 rounded-full',
                    dotTone,
                  )}
                  aria-hidden
                />
              )}
            </button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent>
          {activeCount > 0
            ? `${activeCount} active agent session${activeCount === 1 ? '' : 's'}`
            : 'Agent activity'}
        </TooltipContent>
      </Tooltip>
      <PopoverContent className="w-[22rem] p-0" align="end" sideOffset={6}>
        <div className="flex items-center justify-between gap-xs border-b border-border px-sm py-xs">
          <div className="flex items-center gap-xs">
            <Bot className="size-4 text-primary" aria-hidden />
            <span className="text-metadata font-semibold">Agent activity</span>
            {activeCount > 0 && (
              <Badge variant="info">{activeCount} active</Badge>
            )}
          </div>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={fetchData}
                disabled={loading}
                aria-label="Refresh agent activity"
              >
                <RefreshCw
                  className={cn('size-3.5', loading && 'animate-spin')}
                  aria-hidden
                />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Refresh</TooltipContent>
          </Tooltip>
        </div>

        <div className="max-h-[24rem] overflow-y-auto">
          {sessions.length === 0 ? (
            <p className="px-sm py-md text-center text-caption text-muted-foreground">
              No recent agent sessions.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {sessions.map((row) => (
                <li key={`${row.kind}-${row.id}`}>
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      navigate(detailPath(row));
                    }}
                    className="flex w-full items-start gap-sm px-sm py-xs text-left transition-colors hover:bg-accent/50 focus:outline-none focus:bg-accent/50"
                  >
                    <span className="mt-1 shrink-0">{statusIcon(row.status)}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-xs">
                        <span className="text-metadata font-medium">
                          {KIND_LABEL[row.kind] ?? row.kind} #{row.id}
                        </span>
                        <span className="text-caption text-muted-foreground">
                          · {row.status}
                        </span>
                      </div>
                      <div className="line-clamp-1 text-caption text-muted-foreground">
                        {row.generated_by_model && (
                          <span className="font-mono">{row.generated_by_model}</span>
                        )}
                        {row.generated_by_model && row.user_username && ' · '}
                        {row.user_username && <span>by {row.user_username}</span>}
                        {(row.generated_by_model || row.user_username) && row.started_at && ' · '}
                        {row.started_at && <span>{fmtAgo(row.started_at)}</span>}
                      </div>
                    </div>
                    <ChevronRight
                      className="mt-1 size-3.5 shrink-0 text-muted-foreground"
                      aria-hidden
                    />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="border-t border-border px-sm py-xs">
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              navigate('/agent-activity');
            }}
            className="inline-flex w-full items-center justify-center gap-xs rounded-control px-sm py-xs text-metadata text-primary hover:bg-accent focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
          >
            View all agent runs
            <ExternalLink className="size-3.5" aria-hidden />
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default AgentActivityRail;
