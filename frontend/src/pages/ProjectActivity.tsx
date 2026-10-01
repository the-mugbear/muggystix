/**
 * Agent Sessions — where an operator manages the agents working on this
 * project (route `/agent-activity`, kept so links and the palette still work).
 *
 * v3.0.0 — the unified timeline of every agent session (plan generation +
 * execution) with model/tool/user attribution.
 * v5.267.0 — the Posture layout (UI_STYLE_GUIDE §7).
 * v5.294.0 — Agent Sessions (the list) became this page's "Sessions" view.
 *
 * v5.312.0 — session-first. Since v2.337.0 an operator hands an agent ONE
 * session that does every kind of work, but the page still led with analytics
 * and split the list into two views that disagreed: the Sessions view numbered
 * sessions by their detail row's id and looked up End / Resume by it (so the
 * buttons never showed), and read a resumable session as ended. Now: what is live (with its
 * work and its controls) first, then the history — one row per session, its work under it, each opening the session
 * page (`/agent-sessions/:id`) — and the analytics last.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Bot, ChevronRight, Loader2, Play, RefreshCw, Search } from 'lucide-react';
import {
  AgentSessionKind,
  AgentSessionRow,
  AgentActivitySummary,
  listAgentSessions,
  getAgentSessionSummary,
  getAgentActivitySummary,
  ModelToolSummaryRow,
} from '../services/api';
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
import { useAgentSessionControls } from '../hooks/useAgentSessionControls';
import { useCanStartAgentSession } from '../hooks/useCanStartAgentSession';
import { useLatestRequest } from '../hooks/useLatestRequest';
import { safeFallback } from '../utils/uiStyles';
import { formatApiError } from '../utils/apiErrors';
import PostureLead, { LeadTone } from '../components/posture/PostureLead';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import PostureEmpty from '../components/posture/PostureEmpty';
import {
  AuthorityBadge,
  SessionWork,
  START_SESSION_PATH,
  SessionActions,
  SessionStateBadge,
  StateLineText,
  rowOperatorName,
  sessionStateLine,
} from '../components/agent-sessions/SessionParts';
import LastUpdated from '../components/LastUpdated';
import ListFilterBar, { FILTER_TRIGGER_CLASS } from '../components/ListFilterBar';
import { NavigableTableCell, NavigableTableRow } from '../components/NavigableTableRow';
import RunKindBadge, { runKindLabel } from '../components/RunKindBadge';
import TimeAgo from '../components/TimeAgo';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '../components/ui/tooltip';
import { cn } from '../utils/cn';
import {
  agentSessionPath,
  isStalledRun,
  keyState,
  sessionRowPath,
} from '../utils/agentRuns';

const KIND_OPTIONS: Array<{ value: '' | AgentSessionKind; label: string }> = [
  { value: '', label: 'All sessions' },
  { value: 'project', label: 'Sessions' },
  // Rows from before the v2.337.0 consolidation, when each kind of work had
  // its own key. (Legacy plan-generation and execution rows went with plans
  // and execution runs in 5.320.0.)
  { value: 'assist', label: 'Legacy assist' },
];

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'success' | 'warning' | 'info' | 'outline' | 'muted';

function statusBadgeVariant(status: string): BadgeVariant {
  const s = status.toLowerCase();
  if (s === 'active' || s === 'in_progress') return 'success';
  if (s === 'completed') return 'info';
  if (s === 'failed') return 'destructive';
  if (s === 'paused' || s === 'draft') return 'warning';
  return 'muted';
}

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;


/** v5.267.0 — the per-(model, tool) breakdown, as a section and only when an
 *  agent actually reported its model or tool. */
const ModelRollupSection: React.FC<{ rows: ModelToolSummaryRow[] | null }> = ({ rows }) => {
  if (!rows || rows.length === 0) return null;
  const reported = rows.some((r) => r.generated_by_model || r.generated_by_tool);
  if (!reported) {
    return (
      <p className="text-caption text-muted-foreground">
        No agent has reported its model or client yet, so there is no breakdown by model — it appears
        here once one does.
      </p>
    );
  }
  return (
    <PostureSection
      title="Activity by agent / model"
      description="Sessions per agent identity — unified project sessions, plus the legacy plan-generation / execution / assist rows from before the consolidation. Compares models running against the same project."
    >
      <div className="overflow-x-auto">
        <Table className="min-w-[600px]">
          <TableHeader>
            <TableRow>
              <TableHead>Model</TableHead>
              <TableHead>Client</TableHead>
              <TableHead className="w-20 text-right">Sessions</TableHead>
              <TableHead className="w-16 text-right">Assist</TableHead>
              <TableHead className="w-16 text-right">Total</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r, idx) => (
              <TableRow key={`${r.generated_by_model ?? 'unknown'}-${r.generated_by_tool ?? 'unknown'}-${idx}`}>
                <TableCell className="truncate" title={r.generated_by_model ?? undefined}>
                  {r.generated_by_model ? (
                    <code className="font-mono text-caption">{r.generated_by_model}</code>
                  ) : (
                    <span className="text-caption text-muted-foreground">(not reported)</span>
                  )}
                </TableCell>
                <TableCell className="truncate" title={r.generated_by_tool ?? undefined}>
                  {r.generated_by_tool || <span className="text-caption text-muted-foreground">—</span>}
                </TableCell>
                <TableCell className="text-right tabular-nums">{r.project ?? 0}</TableCell>
                <TableCell className="text-right tabular-nums">{r.assist}</TableCell>
                <TableCell className="text-right font-semibold tabular-nums">{r.total}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </PostureSection>
  );
};

type Hygiene = NonNullable<AgentActivitySummary['session_hygiene']>;

/** The lead sentence: what is live, what waits on someone.  (There is no
 *  "left open" count since 5.313.1: ending a session abandons its runs, so an
 *  ended session never leaves one open.) */
const SessionsLead: React.FC<{
  live: AgentSessionRow[] | null;
  hygiene: Hygiene | null;
  windowDays: number | null;
}> = ({ live, hygiene, windowDays }) => {
  if (live == null) return null;
  const connected = live.filter((r) => keyState(r)?.tone === 'ok').length;
  const resumable = live.filter((r) => keyState(r)?.tone === 'warn').length;
  let tone: LeadTone = 'neutral';
  const parts: string[] = [];
  if (live.length === 0) {
    parts.push('No agent session is live on this project.');
  } else {
    parts.push(`${plural(connected, 'session')} live now`);
    if (resumable > 0) {
      parts[0] += `; ${plural(resumable, 'more')} waiting to be resumed (the key ran out, the session did not)`;
      tone = 'warning';
    }
    parts[0] += '.';
  }
  if (hygiene && hygiene.lapsed > 0) {
    parts.push(`${plural(hygiene.lapsed, 'session')} in the last ${windowDays ?? 14} days lapsed without ending.`);
  }
  return (
    <PostureLead
      tone={tone}
      restsOn="Live = an agent can use the session's key right now. A session whose key ran out inside its lifetime is resumable by the operator who started it: same session, a new key. Ending a session revokes its key; the tests it proposed and the evidence it recorded stay."
    >
      {parts.join(' ')}
    </PostureLead>
  );
};

/** v5.219.0 — session hygiene: are sessions exiting cleanly, and are they
 *  telling us anything on the way out? Counted over sessions STARTED in the
 *  window. */
const HygieneStrip: React.FC<{ hygiene: Hygiene | null }> = ({ hygiene }) => {
  if (!hygiene || hygiene.sessions_started === 0) return null;
  const h = hygiene;
  const warn = (on: boolean, text: string) => (
    <span className={on ? 'text-warning' : undefined}>{text}</span>
  );
  const MIN_SAMPLE_FOR_PERCENT = 5;
  const ratio = (n: number, d: number): string =>
    d >= MIN_SAMPLE_FOR_PERCENT
      ? `${n.toLocaleString()} · ${Math.round((n / d) * 100)}%`
      : `${n.toLocaleString()} of ${d.toLocaleString()}`;
  return (
    <PostureSection title="Session hygiene">
      <div className="grid gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
        <PostureMeasure
          label="Sessions started"
          info="Agent sessions started in the window, whatever they did afterwards — including ones whose agent never connected and made no calls. Legacy sessions from before one session did all the work count too."
          value={h.sessions_started.toLocaleString()}
        >
          {h.sessions_active.toLocaleString()} active · {h.sessions_ended.toLocaleString()} ended
          {h.ended_by_operator > 0 && ` · ${h.ended_by_operator.toLocaleString()} by an operator`}
        </PostureMeasure>
        <PostureMeasure
          label="Ended by the agent"
          info="Sessions the agent closed itself (end_reason: agent), out of sessions ENDED — a percentage once there are at least five. An agent that ends its own session files feedback on the way; one that lapsed or was ended from here usually did not."
          value={warn(
            h.sessions_ended > 0 && h.ended_by_agent < h.sessions_ended,
            h.sessions_ended > 0 ? ratio(h.ended_by_agent, h.sessions_ended) : '0',
          )}
        >
          the clean exit, of {plural(h.sessions_ended, 'ended session')}
        </PostureMeasure>
        <PostureMeasure
          label="Lapsed (never ended)"
          info="Sessions nobody ended: the agent never called end and no operator did, so the key ran out. These are the sessions with no wrap-up."
          value={warn(h.lapsed > 0, h.lapsed.toLocaleString())}
        >
          key ran out with no end call
        </PostureMeasure>
        <PostureMeasure
          label="Filed feedback"
          info="Sessions that filed at least one feedback item, out of sessions STARTED — a percentage once there are at least five."
          value={warn(
            h.sessions_with_feedback < h.sessions_started,
            ratio(h.sessions_with_feedback, h.sessions_started),
          )}
        >
          of {plural(h.sessions_started, 'session')} started
        </PostureMeasure>
      </div>
    </PostureSection>
  );
};

/** Every day of the window, oldest first, with the days that had no calls as
 *  zeros. v5.312.0 — the chart drew only the days that had calls, each a
 *  flex-1 bar, so a single busy day filled the whole width as one block. */
export const fillCallDays = (
  daily: AgentActivitySummary['daily'],
  windowDays: number,
  today: Date = new Date(),
): AgentActivitySummary['daily'] => {
  const byDay = new Map(daily.map((d) => [d.day, d]));
  // Ends at today (UTC, the backend's day) or the newest day with calls, if a
  // skewed clock puts that later — a day of data is never dropped.
  const newest = daily.reduce((m, d) => (d.day > m ? d.day : m), today.toISOString().slice(0, 10));
  const end = new Date(`${newest}T00:00:00Z`);
  const out: AgentActivitySummary['daily'] = [];
  for (let i = Math.max(1, windowDays) - 1; i >= 0; i -= 1) {
    const d = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate() - i));
    const day = d.toISOString().slice(0, 10);
    out.push(byDay.get(day) ?? { day, calls: 0, errors: 0 });
  }
  return out;
};

/** API-call analytics from the per-call audit log. */
const ApiCallSection: React.FC<{
  summary: AgentActivitySummary | null;
  error?: boolean;
  onRetry?: () => void;
}> = ({ summary, error, onRetry }) => {
  const navigate = useNavigate();
  if (error) {
    return (
      <div className="flex flex-wrap items-center gap-xs">
        <p className="text-caption text-muted-foreground">API-call analytics are currently unavailable.</p>
        {onRetry && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw className="size-3.5" aria-hidden /> Retry
          </Button>
        )}
      </div>
    );
  }
  if (!summary) {
    return (
      <p className="flex items-center gap-xs text-caption text-muted-foreground" role="status" aria-live="polite">
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        Loading API-call analytics…
      </p>
    );
  }
  if (summary.total_calls === 0) {
    return (
      <p className="text-caption text-muted-foreground">
        No agent API calls recorded in the last {summary.window_days} days.
      </p>
    );
  }

  const days = fillCallDays(summary.daily, summary.window_days);
  const maxDay = Math.max(1, ...days.map((d) => d.calls));
  const sb = summary.status_breakdown;
  const openSession = (s: { workflow: string; session_id: number }) => {
    if (s.workflow === 'session') navigate(agentSessionPath(s.session_id));
  };

  return (
    <PostureSection
      title="API calls"
      description={`Every agent → BlueStick request over the last ${summary.window_days} days, from the per-call audit log.`}
    >
      <p className="text-metadata" data-testid="api-call-line">
        <strong>{plural(summary.total_calls, 'call')}</strong>
        {' '}from {plural(summary.distinct_agents, 'agent')}
        <span className="text-muted-foreground"> · </span>
        <span className="text-success">{sb.success.toLocaleString()} 2xx</span>
        <span className="text-muted-foreground"> · </span>
        <span className={sb.client_error > 0 ? 'text-warning' : 'text-muted-foreground'}>
          {sb.client_error.toLocaleString()} 4xx
        </span>
        <span className="text-muted-foreground"> · </span>
        <span className={sb.server_error > 0 ? 'text-destructive' : 'text-muted-foreground'}>
          {sb.server_error.toLocaleString()} 5xx
        </span>
      </p>

      <div className="mt-sm">
        <p className="mb-xxs text-caption text-muted-foreground">Calls per day</p>
        <div className="flex h-16 items-end gap-[2px]" data-testid="calls-per-day">
          {days.map((d) => (
            <Tooltip key={d.day}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={`${d.day}: ${d.calls.toLocaleString()} call${d.calls === 1 ? '' : 's'}${d.errors > 0 ? `, ${d.errors.toLocaleString()} error${d.errors === 1 ? '' : 's'}` : ''}`}
                  className={cn(
                    'min-w-[3px] flex-1 rounded-sm border-0 p-0',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    d.calls === 0 ? 'bg-border' : d.errors > 0 ? 'bg-warning' : 'bg-info',
                  )}
                  style={{ height: d.calls === 0 ? '2px' : `${Math.max(4, (d.calls / maxDay) * 100)}%` }}
                />
              </TooltipTrigger>
              <TooltipContent>
                {d.day}: {d.calls.toLocaleString()} call{d.calls === 1 ? '' : 's'}
                {d.errors > 0 ? `, ${d.errors.toLocaleString()} error${d.errors === 1 ? '' : 's'}` : ''}
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      </div>

      {summary.busiest_sessions.length > 0 && (
        <div className="mt-sm">
          <p className="mb-xxs text-caption text-muted-foreground">Busiest sessions</p>
          <ul className="flex flex-col">
            {summary.busiest_sessions.slice(0, 5).map((s) => (
              <li key={`${s.workflow}-${s.session_id}`} className="flex min-w-0 items-center gap-xs text-metadata">
                <span className="min-w-0 truncate">
                  <span className="text-muted-foreground">{s.workflow}</span> #{s.session_id} ·{' '}
                  <strong>{s.calls.toLocaleString()}</strong> call{s.calls === 1 ? '' : 's'}
                </span>
                {['execution', 'plan', 'session'].includes(s.workflow) && (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-6 px-xs"
                    onClick={() => openSession(s)}
                    aria-label={`Open ${s.workflow} #${s.session_id}`}
                  >
                    Open
                    <ChevronRight className="ml-xxs size-3" aria-hidden />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </PostureSection>
  );
};

const StartSessionButton: React.FC = () => (
  <Button size="sm" variant="outline" asChild>
    <Link to={START_SESSION_PATH}>
      <Play className="size-3.5" aria-hidden /> Start agent session
    </Link>
  </Button>
);

const ProjectActivity: React.FC = () => {
  const [lastFetched, setLastFetched] = useState<Date | null>(null);
  const [live, setLive] = useState<AgentSessionRow[] | null>(null);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [rows, setRows] = useState<AgentSessionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<ModelToolSummaryRow[] | null>(null);
  const [apiSummary, setApiSummary] = useState<AgentActivitySummary | null>(null);
  const [apiSummaryError, setApiSummaryError] = useState(false);
  // Grows on "Load older sessions" so the history isn't silently capped.
  const [limit, setLimit] = useState(200);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const refresh = useCallback(() => setRefreshNonce((n) => n + 1), []);
  const controls = useAgentSessionControls(refresh);
  const canStartAgent = useCanStartAgentSession();

  // B15 — the history's filters live in the URL (`?kind=&model=&tool=`,
  // replace not push), so a filtered history can be shared and survives a
  // reload.
  const [searchParams, setSearchParams] = useSearchParams();
  const kindParam = searchParams.get('kind');
  const kindFilter: '' | AgentSessionKind = kindParam === 'project' || kindParam === 'assist' ? kindParam : '';
  const modelFilter = searchParams.get('model') ?? '';
  const toolFilter = searchParams.get('tool') ?? '';
  const setFilter = useCallback((key: 'kind' | 'model' | 'tool', value: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value); else next.delete(key);
      return next;
    }, { replace: true });
  }, [setSearchParams]);
  const setKindFilter = (value: '' | AgentSessionKind) => setFilter('kind', value);
  const setModelFilter = (value: string) => setFilter('model', value);
  const setToolFilter = (value: string) => setFilter('tool', value);
  // One navigation: successive setSearchParams calls in a tick each start
  // from the same URL, so three of them would clear only the last.
  const clearFilters = () => setSearchParams((prev) => {
    const next = new URLSearchParams(prev);
    ['kind', 'model', 'tool'].forEach((k) => next.delete(k));
    return next;
  }, { replace: true });

  const knownModels = useMemo(() => {
    if (!summary) return [];
    const set = new Set<string>();
    summary.forEach((r) => r.generated_by_model && set.add(r.generated_by_model));
    return Array.from(set).sort();
  }, [summary]);
  const knownTools = useMemo(() => {
    if (!summary) return [];
    const set = new Set<string>();
    summary.forEach((r) => r.generated_by_tool && set.add(r.generated_by_tool));
    return Array.from(set).sort();
  }, [summary]);

  // The live sessions: every active project session, whatever the history's
  // filters say — this section answers "what is running right now".
  const loadLive = useCallback(async () => {
    try {
      const list = await listAgentSessions({ kind: 'project', status: 'active', limit: 100 });
      setLive(list.sessions);
      setLiveError(null);
    } catch (e: unknown) {
      setLiveError(formatApiError(e, 'Could not load the live sessions.'));
    }
  }, []);

  // Only the latest fetch may set state: a slow response for an older filter
  // would otherwise land after the newer one and show the wrong history.
  const runLatest = useLatestRequest();
  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    setApiSummaryError(false);
    const filters: Record<string, string | number> = { limit };
    if (kindFilter) filters.kind = kindFilter;
    if (modelFilter) filters.model = modelFilter;
    if (toolFilter) filters.tool = toolFilter;
    // API-call analytics is best-effort — its failure must not blank the
    // history; the section shows "unavailable + Retry" instead.
    let apiSumFailed = false;
    const r = await runLatest(() => Promise.all([
      listAgentSessions(filters),
      getAgentSessionSummary(),
      getAgentActivitySummary().catch(() => { apiSumFailed = true; return null; }),
      loadLive(),
    ]));
    if (r.stale) return;
    if (r.ok) {
      const [list, sum, apiSum] = r.value;
      setRows(list.sessions);
      setTotal(list.total);
      setSummary(sum.summary);
      setApiSummary(apiSum);
      setApiSummaryError(apiSumFailed);
      setLastFetched(new Date());
    } else {
      setError(formatApiError(r.error, 'Failed to load agent sessions.'));
    }
    setLoading(false);
  }, [kindFilter, modelFilter, toolFilter, limit, loadLive, runLatest]);

  useEffect(() => {
    void fetchAll();
  }, [fetchAll, refreshNonce]);

  // Key state and last calls move while the page is open; the live list is
  // re-read each minute (not in a hidden tab).
  useVisibilityPoll(loadLive, 60_000);

  const showModelFilter = knownModels.length > 0 || modelFilter !== '';
  const showToolFilter = knownTools.length > 0 || toolFilter !== '';
  const hygiene = apiSummary?.session_hygiene ?? null;
  const filtered = Boolean(kindFilter || modelFilter || toolFilter);

  return (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      {controls.dialogs}
      <div className="flex items-start justify-between gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title">Agent Sessions</h1>
          <p className="mt-xxs max-w-4xl text-metadata text-muted-foreground">
            Every session an operator has handed an agent on this project: what is live, the
            tests each one proposed and the evidence it recorded, and the controls to resume or end it. Open a
            session for its notes and every call it made.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-xs">
          {canStartAgent && <StartSessionButton />}
          <LastUpdated
            compact
            lastFetched={lastFetched}
            onRefresh={refresh}
            isLoading={loading}
            label="agent sessions"
          />
        </div>
      </div>

      <SessionsLead
        live={live}
        hygiene={hygiene}
        windowDays={apiSummary?.window_days ?? null}
      />

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <PostureSection
        title={<>Live now {live && live.length > 0 && <SectionCount>{live.length}</SectionCount>}</>}
        description="Active sessions — ones an agent can use now, and ones whose key ran out that their operator can resume."
      >
        {liveError && (
          <div className="flex flex-wrap items-center gap-xs">
            <p className="text-caption text-destructive">{liveError}</p>
            <Button size="sm" variant="outline" onClick={() => void loadLive()}>
              <RefreshCw className="size-3.5" aria-hidden /> Retry
            </Button>
          </div>
        )}
        {live == null && !liveError && (
          <p className="flex items-center gap-xs text-caption text-muted-foreground" role="status">
            <Loader2 className="size-3.5 animate-spin" aria-hidden /> Loading live sessions…
          </p>
        )}
        {live != null && live.length === 0 && (
          <PostureEmpty
            Icon={Bot}
            title="No agent session is live"
            action={canStartAgent ? { to: START_SESSION_PATH, label: 'Start agent session' } : undefined}
          >
            A session lets an agent query this project, upload scans, and open plan or execution
            work, with your permissions. It shows here while it runs, with its work and the controls
            to resume or end it.
          </PostureEmpty>
        )}
        {live != null && live.length > 0 && (
          <ul className="flex flex-col divide-y divide-border" data-testid="live-sessions">
            {live.map((row) => {
              const operator = rowOperatorName(row);
              return (
                <li key={row.id} className="flex min-w-0 flex-col gap-xs py-sm md:flex-row md:items-start" data-testid="live-session">
                  <div className="flex min-w-0 flex-1 flex-col gap-xxs">
                    <div className="flex min-w-0 flex-wrap items-center gap-xs">
                      <SessionStateBadge row={row} />
                      <Link
                        to={agentSessionPath(row.id)}
                        className="min-w-0 truncate font-medium text-foreground underline-offset-4 hover:underline"
                        title={row.purpose ?? undefined}
                      >
                        #{row.id} · {safeFallback(row.purpose, 'No stated purpose')}
                      </Link>
                      <AuthorityBadge role={row.operator_role} operator={operator} />
                    </div>
                    <p className="flex min-w-0 flex-wrap items-center gap-x-xs text-caption text-muted-foreground">
                      <span className="truncate">{safeFallback(operator, 'unknown operator')}</span>
                      <span aria-hidden>·</span>
                      <span>started <TimeAgo value={row.started_at} /></span>
                      <span aria-hidden>·</span>
                      {row.last_activity_at ? (
                        <span>last call <TimeAgo value={row.last_activity_at} /></span>
                      ) : (
                        <span className="text-warning">no call yet — the agent has not connected</span>
                      )}
                      <span aria-hidden>·</span>
                      <StateLineText line={keyState(row)} />
                    </p>
                    <SessionWork row={row} />
                  </div>
                  <SessionActions row={row} controls={controls} labelled showOpen />
                </li>
              );
            })}
          </ul>
        )}
      </PostureSection>

      <PostureSection
        title="History"
        description="One row per session, newest first, with the work it opened. Older rows, from before one session did all the work, open their own pages."
        actions={(
          <>
            {loading && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}
            {rows.length < total && !loading && (
              <Button size="sm" variant="outline" onClick={() => setLimit((l) => l + 200)}>
                Load older sessions
              </Button>
            )}
          </>
        )}
      >
        <ListFilterBar summary={`${rows.length} of ${total} shown`} className="mb-0">
          <div data-testid="runs-filters" className="contents">
            <Select
              value={kindFilter || 'all'}
              onValueChange={(v) => setKindFilter(v === 'all' ? '' : (v as AgentSessionKind))}
            >
              <SelectTrigger className={`${FILTER_TRIGGER_CLASS} w-52`} aria-label="Filter sessions by kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KIND_OPTIONS.map((o) => (
                  <SelectItem key={o.value || 'all'} value={o.value || 'all'}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {showModelFilter && (
              <Select value={modelFilter || 'all'} onValueChange={(v) => setModelFilter(v === 'all' ? '' : v)}>
                <SelectTrigger className={`${FILTER_TRIGGER_CLASS} w-52`} aria-label="Filter sessions by model">
                  <SelectValue placeholder="All models" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All models</SelectItem>
                  {knownModels.map((m) => (
                    <SelectItem key={m} value={m}>{m}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {showToolFilter && (
              <Select value={toolFilter || 'all'} onValueChange={(v) => setToolFilter(v === 'all' ? '' : v)}>
                <SelectTrigger className={`${FILTER_TRIGGER_CLASS} w-44`} aria-label="Filter sessions by client">
                  <SelectValue placeholder="All clients" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All clients</SelectItem>
                  {knownTools.map((t) => (
                    <SelectItem key={t} value={t}>{t}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
        </ListFilterBar>

        <Table data-testid="runs-table" style={{ tableLayout: 'fixed' }}>
          <TableHeader>
            <TableRow>
              <TableHead className="w-16">#</TableHead>
              <TableHead>Session</TableHead>
              <TableHead className="w-48">Status</TableHead>
              <TableHead className="w-24">Started</TableHead>
              <TableHead className="w-40">Operator · agent</TableHead>
              <TableHead className="w-[30%]">Work</TableHead>
              <TableHead className="w-20"><span className="sr-only">Actions</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => {
              const path = sessionRowPath(r);
              const stalled = isStalledRun(r);
              const operator = rowOperatorName(r);
              const label = r.kind === 'project'
                ? `Open agent session ${r.id}`
                : `Open ${runKindLabel(r.kind)} ${r.id}`;
              return (
                <NavigableTableRow key={`${r.kind}-${r.id}`} data-testid="run-row">
                  <NavigableTableCell to={path} ariaLabel={label} className="tabular-nums text-muted-foreground">
                    {r.id}
                  </NavigableTableCell>
                  <NavigableTableCell to={path} ariaLabel={label}>
                    <div className="flex min-w-0 items-center gap-xs">
                      {r.kind !== 'project' && <RunKindBadge kind={r.kind} className="shrink-0" />}
                      <span className="min-w-0 truncate" title={r.purpose ?? undefined}>
                        {safeFallback(r.purpose, r.kind === 'project' ? 'No stated purpose' : 'Project-wide')}
                      </span>
                    </div>
                    {(r.generated_by_model || r.generated_by_tool) && (
                      <p
                        className="truncate text-caption text-muted-foreground"
                        title={[r.generated_by_model, r.generated_by_tool].filter(Boolean).join(' · ')}
                      >
                        {[r.generated_by_model, r.generated_by_tool].filter(Boolean).join(' · ')}
                      </p>
                    )}
                  </NavigableTableCell>
                  <TableCell className="overflow-hidden">
                    {r.kind === 'project' ? (
                      <div className="flex min-w-0 flex-col items-start gap-xxs">
                        <SessionStateBadge row={r} />
                        <StateLineText line={sessionStateLine(r)} className="block max-w-full" />
                      </div>
                    ) : (
                      <div className="flex min-w-0 flex-col items-start gap-xxs">
                        <Badge variant={stalled ? 'warning' : statusBadgeVariant(r.status)} className="whitespace-nowrap">
                          {stalled ? 'Stalled' : r.status.replace(/_/g, ' ')}
                        </Badge>
                        {stalled && (
                          <span className="block max-w-full truncate text-caption text-warning">
                            its session can no longer act
                          </span>
                        )}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="truncate">
                    <TimeAgo value={r.started_at} className="text-caption text-muted-foreground" />
                  </TableCell>
                  <TableCell>
                    <div
                      className="min-w-0 text-caption"
                      title={[operator, r.agent_name].filter(Boolean).join(' · ') || undefined}
                    >
                      <p className="truncate text-foreground">{safeFallback(operator, '—')}</p>
                      {r.agent_name && <p className="truncate text-muted-foreground">{r.agent_name}</p>}
                    </div>
                  </TableCell>
                  <TableCell>
                    {r.kind === 'project' ? (
                      <SessionWork row={r} />
                    ) : (
                      <span className="text-caption text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {r.kind === 'project' && <SessionActions row={r} controls={controls} />}
                  </TableCell>
                </NavigableTableRow>
              );
            })}
            {!loading && rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-xl text-center">
                  <Search className="mx-auto mb-xs size-9 text-muted-foreground/50" aria-hidden />
                  <p className="text-metadata text-muted-foreground">
                    {filtered ? 'No agent sessions match the current filters.' : 'No agent has run against this project yet.'}
                  </p>
                  {filtered && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={clearFilters}
                      className="mt-xs"
                    >
                      Clear filters
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </PostureSection>

      <HygieneStrip hygiene={hygiene} />

      <ApiCallSection
        summary={apiSummary}
        error={apiSummaryError}
        onRetry={refresh}
      />

      <ModelRollupSection rows={summary} />
    </div>
  );
};

export default ProjectActivity;
