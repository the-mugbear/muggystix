/**
 * Floating agent-activity rail — beta.2.
 *
 * A pill-shaped trigger in the topbar opens a Popover with the
 * project's recent agent sessions and the runs they opened.  The badge
 * counts live sessions only.  Designed to be ambient awareness, not a primary
 * surface — the trigger is a small badge with a count; clicking
 * shows the last ~8 sessions; a "View all" link takes you to the
 * full /agent-activity timeline.
 *
 * Polls while the trigger is rendered (`pollEvery`).  Renders nothing
 * (no trigger, no popover) when:
 *   - the user isn't authenticated, OR
 *   - no project is loaded, OR
 *   - the project has zero agent sessions on file
 * so the topbar stays uncluttered for unused projects.
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
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
  AgentSessionListResponse,
  AgentSessionRow,
  listAgentSessions,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { NO_PROJECT, useProjectId } from '../hooks/useProjectId';
import { pollEvery } from '../lib/query';
import { cn } from '../utils/cn';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { formatRelativeTime } from '../utils/relativeTime';
import { SESSIONS_LIST_PATH, agentConnected, sessionRowPath } from '../utils/agentRuns';

// Cadence when there's something live worth watching.
const ACTIVE_POLL_MS = 60_000;
// Slower cadence when the popover is closed and there are no active
// sessions — drops the rail's idle network/render cost ~5×.  Audit H19.
const IDLE_POLL_MS = 5 * 60_000;
const PEEK_LIMIT = 8;

const RECENT_FILTERS: AgentSessionFilters = { limit: PEEK_LIMIT };
const ACTIVE_FILTERS: AgentSessionFilters = { status: 'active', kind: 'project', limit: 1 };
const NO_SESSIONS: AgentSessionRow[] = [];

const KIND_LABEL: Record<string, string> = {
  // 5.312.0 — the unified session, the row every new session is; it read as
  // the raw "project #72" and opened the list, not the session.
  project: 'Session',
  assist: 'Assist',
};

const statusIcon = (row: AgentSessionRow) => {
  // An active session whose key ran out is waiting on its operator, not
  // working: no spinner for it.
  if (row.kind === 'project' && row.status === 'active' && !agentConnected(row)) {
    return <CircleDot className="size-3 text-warning" aria-hidden />;
  }
  const s = row.status.toLowerCase();
  if (s === 'active' || s === 'in_progress') {
    return <Loader2 className="size-3 animate-spin text-info" aria-hidden />;
  }
  if (s === 'completed' || s === 'success') {
    return <CircleCheck className="size-3 text-success" aria-hidden />;
  }
  if (s === 'failed' || s === 'error') {
    return <CircleAlert className="size-3 text-destructive" aria-hidden />;
  }
  return <CircleDot className="size-3 text-muted-foreground" aria-hidden />;
};

/** Short relative age ("5m ago"). Shared with every other surface —
 *  this was one of four byte-identical copies before v5.179.0. */
const fmtAgo = (iso?: string | null): string =>
  formatRelativeTime(iso, { withSeconds: true });

/** Where a row opens; the list, should a row ever have no page. */
const detailPath = (row: AgentSessionRow): string =>
  sessionRowPath(row) ?? SESSIONS_LIST_PATH;

const AgentActivityRail: React.FC = () => {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const projectId = useProjectId();
  const hasProject = projectId !== NO_PROJECT;
  const [open, setOpen] = useState(false);
  const enabled = isAuthenticated && hasProject;

  // Two cheap reads of the same endpoint — the recent list, and just the
  // active count for the badge.  5.313.0 — the badge counts agent SESSIONS
  // (kind 'project'): a run is part of its session, so counting runs too
  // told the operator one live agent was two or three.
  // A failure is silent — the rail is ambient; failing is the same as having
  // nothing new to show.  Notifications surface API outages through their own
  // polling path.
  // Polled only in a visible tab.  The cadence tightens when the popover is
  // open or there's a live session to track; otherwise it backs off to 5min
  // so the rail isn't waking the browser every minute on a normal page.
  const cadence = (liveCount: number) => (open || liveCount > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS);
  const active = useQuery({
    queryKey: ['listAgentSessions', projectId, ACTIVE_FILTERS],
    queryFn: ({ signal }) => listAgentSessions(projectId, ACTIVE_FILTERS, { signal }),
    enabled,
    // Its own answer is the live count that sets the cadence.
    ...pollEvery((query) => cadence((query.state.data as AgentSessionListResponse | undefined)?.total ?? 0)),
  });
  const activeCount = active.data?.total ?? 0;
  const recent = useQuery({
    queryKey: ['listAgentSessions', projectId, RECENT_FILTERS],
    queryFn: ({ signal }) => listAgentSessions(projectId, RECENT_FILTERS, { signal }),
    enabled,
    ...pollEvery(cadence(activeCount)),
  });
  const sessions = recent.data?.sessions ?? NO_SESSIONS;
  const loaded = recent.data !== undefined && active.data !== undefined;
  const loading = recent.isFetching || active.isFetching;
  const fetchData = () => {
    void recent.refetch();
    void active.refetch();
  };

  const totalShown = sessions.length;
  const dotTone = activeCount > 0 ? 'bg-info' : null;

  // Hide the trigger entirely until the first fetch returns AND we
  // know the project has at least one agent session on file.
  if (!isAuthenticated || !hasProject) return null;
  if (loaded && totalShown === 0 && activeCount === 0) return null;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        // Opening reads at once, so the reader sees the latest state without
        // waiting for the next poll.
        if (next) fetchData();
      }}
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-label={
                activeCount > 0
                  // "in this project": Operations' own line counts the READER's
                  // sessions, and the two numbers can differ (owner, 2026-10-10:
                  // keep both, say which this one is).
                  ? `Agent activity — ${activeCount} active session${activeCount === 1 ? '' : 's'} in this project`
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
            ? `${activeCount} active agent session${activeCount === 1 ? '' : 's'} in this project`
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
            // "None" is said only of a list that was read: the rail stays
            // quiet about a failed poll, but its open popover must not claim
            // there are no sessions when it could not find out.
            <p className="px-sm py-md text-center text-caption text-muted-foreground">
              {recent.data !== undefined
                ? 'No recent agent sessions.'
                : recent.isError
                  ? 'The agent sessions could not be loaded. Use Refresh to try again.'
                  : 'Loading…'}
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
                    <span className="mt-1 shrink-0">{statusIcon(row)}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-xs">
                        <span className="text-metadata font-medium">
                          {KIND_LABEL[row.kind] ?? row.kind} #{row.id}
                        </span>
                        <span className="text-caption text-muted-foreground">
                          · {row.status}
                        </span>
                      </div>
                      {row.kind === 'project' && row.purpose && (
                        <div className="line-clamp-1 break-words text-caption text-foreground" title={row.purpose}>
                          {row.purpose}
                        </div>
                      )}
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
            Manage agent sessions
            <ExternalLink className="size-3.5" aria-hidden />
          </button>
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default AgentActivityRail;
