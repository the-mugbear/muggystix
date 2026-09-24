/**
 * Project Activity — unified timeline of every agent session
 * (recon + plan generation + execution) for the active project.
 *
 * v3.0.0 — the one surface that aggregates the three workflows in time
 * order with model/tool/user attribution.
 *
 * v5.267.0 — the Posture layout (UI_STYLE_GUIDE §7): a lead sentence, one
 * strip of session-hygiene measures, then sections over thin rules. The five
 * hygiene boxes, the call tiles and the card around every block are gone; the
 * call chart renders only when there were calls, and the model breakdown only
 * when an agent reported its model or tool.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { RefreshCw, Search, ChevronRight, Loader2, RotateCcw, Square } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import ResumeAgentSessionDialog from '../components/ResumeAgentSessionDialog';
import {
  AgentSessionKind,
  AgentSessionRow,
  AgentActivitySummary,
  listAgentSessions,
  endAgentSession,
  getAgentSessionSummary,
  getAgentActivitySummary,
  ModelToolSummaryRow,
} from '../services/api';
import { safeFallback } from '../utils/uiStyles';
import { formatApiError } from '../utils/apiErrors';
import PostureLead, { LeadTone } from '../components/posture/PostureLead';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection from '../components/posture/PostureSection';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Alert, AlertDescription } from '../components/ui/alert';
import { CodeBlock } from '../components/ui/code-block';
import { Label } from '../components/ui/label';
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
import { formatRelativeTime } from '../utils/relativeTime';

const KIND_OPTIONS: Array<{ value: '' | AgentSessionKind; label: string }> = [
  { value: '', label: 'All sessions' },
  // v2.337.0 — one project session does every kind of work; the four below are
  // legacy per-workflow rows from before the consolidation.
  { value: 'project', label: 'Session' },
  { value: 'recon', label: 'Recon' },
  { value: 'plan_generation', label: 'Plan generation' },
  { value: 'execution', label: 'Execution' },
  // v5.185.0 — the fourth workflow. Its absence here meant "All workflows"
  // was not all of them.
  { value: 'assist', label: 'Assist' },
];

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'success' | 'warning' | 'info' | 'outline' | 'muted';

function kindBadgeVariant(kind: AgentSessionKind): BadgeVariant {
  switch (kind) {
    case 'project':
      return 'default';
    case 'recon':
      return 'secondary';
    case 'plan_generation':
      return 'info';
    case 'execution':
      return 'success';
    case 'assist':
      return 'warning';
  }
}

function statusBadgeVariant(status: string): BadgeVariant {
  const s = status.toLowerCase();
  if (s === 'active' || s === 'in_progress') return 'success';
  if (s === 'completed' || s === 'approved') return 'info';
  if (s === 'failed' || s === 'rejected') return 'destructive';
  if (s === 'paused' || s === 'pending_review' || s === 'draft') return 'warning';
  return 'muted';
}

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

/** Short relative age ("5m ago"). Shared with every other surface —
 *  this was one of four byte-identical copies before v5.179.0. */
const fmtRelative = (iso?: string | null): string =>
  formatRelativeTime(iso, { withSeconds: true });

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** v5.267.0 — the per-(model, tool) breakdown, as a section and only when an
 *  agent actually reported its model or tool: a table whose every row read
 *  "(not reported)" compared nothing. */
const ModelRollupSection: React.FC<{ rows: ModelToolSummaryRow[] | null }> = ({ rows }) => {
  if (!rows || rows.length === 0) return null;
  const reported = rows.some((r) => r.generated_by_model || r.generated_by_tool);
  if (!reported) {
    return (
      <p className="text-caption text-muted-foreground">
        No agent has reported its model or tool yet, so there is no breakdown by model — it appears
        here once one does.
      </p>
    );
  }
  return (
    <PostureSection
      title="Activity by agent / model"
      description="Sessions per agent identity — unified project sessions, plus the legacy recon / plan-generation / execution / assist rows from before the consolidation. Compares models running against the same project."
    >
      <div className="overflow-x-auto">
        <Table className="min-w-[600px]">
          <TableHeader>
            <TableRow>
              <TableHead>Model</TableHead>
              <TableHead>Tool / harness</TableHead>
              <TableHead className="w-20 text-right">Sessions</TableHead>
              <TableHead className="w-16 text-right">Recon</TableHead>
              <TableHead className="w-20 text-right">Plan-gen</TableHead>
              <TableHead className="w-20 text-right">Execution</TableHead>
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
                <TableCell className="text-right tabular-nums">{r.recon}</TableCell>
                <TableCell className="text-right tabular-nums">{r.plan_generation}</TableCell>
                <TableCell className="text-right tabular-nums">{r.execution}</TableCell>
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

/** v5.219.0 — what the operator pastes to a still-connected agent before
 *  ending from the UI. Mirrors the contract's ending steps so the agent does
 *  the clean exit itself: feedback (if none filed), phases closed, session end. */
const WRAP_UP_PROMPT =
  'We are done with this BlueStick session. Wrap up now: (1) if you have filed no '
  + 'feedback in this session yet, call submit_feedback (POST /agent/feedback) with the '
  + 'friction you hit — one line per endpoint or tool where you retried, guessed, or worked '
  + 'around something; (2) close any open phase — recon_complete / '
  + 'execution_complete_session; (3) call end_session (POST /agent/session/end) with a '
  + 'one-line note of what this session did. Confirm each step’s response to me.';

/** v5.219.0 — one line under the status badge on an ended project row: how
 *  it ended and whether it said anything on the way out. 'agent' is the clean
 *  exit; the other two mean the agent never called end. */
const endedState = (row: AgentSessionRow): { text: string; tone: 'ok' | 'warn' | 'muted' } | null => {
  if (row.kind !== 'project' || row.status === 'active') return null;
  const fb = row.feedback_count ?? 0;
  const fbText = fb > 0 ? `${fb} feedback` : 'no feedback';
  switch (row.end_reason) {
    case 'agent':
      return { text: `ended by agent · ${fbText}`, tone: fb > 0 ? 'ok' : 'warn' };
    case 'operator':
      return { text: `ended by operator · ${fbText}`, tone: 'warn' };
    case 'lapsed':
      return { text: `lapsed (never ended) · ${fbText}`, tone: 'warn' };
    default:
      return fb > 0 ? { text: fbText, tone: 'muted' } : null;
  }
};

type Hygiene = NonNullable<AgentActivitySummary['session_hygiene']>;

/** v5.267.0 — the lead sentence: the facts about sessions, from the hygiene
 *  counts when the backend sent them (sessions STARTED in the window), else
 *  from the model rollup's total — unfiltered, unlike the timeline's. */
const RunsLead: React.FC<{
  hygiene: Hygiene | null;
  windowDays: number | null;
  total: number | null;
  /** In-progress runs on the loaded page whose session can no longer act. */
  stalledRuns: number;
}> = ({ hygiene, windowDays, total: recorded, stalledRuns }) => {
  if (recorded == null && !hygiene) return null;
  const total = recorded ?? 0;
  let tone: LeadTone = 'neutral';
  let sentence: string;
  if (hygiene && hygiene.sessions_started > 0) {
    sentence = `${plural(hygiene.sessions_started, 'session')} started in the last ${windowDays ?? 14} days; `
      + `${hygiene.sessions_active.toLocaleString()} still active; `
      + `${hygiene.lapsed.toLocaleString()} lapsed without ending.`;
    if (hygiene.lapsed > 0) tone = 'warning';
  } else if (total > 0) {
    sentence = hygiene
      ? `No agent sessions started in the last ${windowDays ?? 14} days; ${plural(total, 'session')} on record.`
      : `${plural(total, 'agent session')} on record for this project.`;
  } else {
    sentence = 'No agent has run against this project yet.';
  }
  // v5.288.0 — the lead counts SESSIONS; the table below also lists RUNS,
  // which keep their own status after the session that drove them ends. Said
  // here so "0 still active" above an "active" run is not a contradiction.
  if (stalledRuns > 0) {
    sentence += ` ${plural(stalledRuns, 'run')} below ${stalledRuns === 1 ? 'is' : 'are'} still open after ${stalledRuns === 1 ? 'its' : 'their'} session ended — nothing is driving ${stalledRuns === 1 ? 'it' : 'them'}.`;
    tone = 'warning';
  }
  return (
    <PostureLead
      tone={tone}
      restsOn="Counts are of sessions started in the window. Runs (recon, plan, execution) are listed in the table and can outlive the session that opened them: a run keeps its own status until an agent or operator closes it. A session that lapsed never called end, so it filed no wrap-up and its key simply ran out."
    >
      {sentence}
    </PostureLead>
  );
};

/** v5.288.0 — an in-progress run whose session can no longer act: it will not
 *  move on its own. Workflow state, not evidence age. */
const isStalledRun = (row: AgentSessionRow): boolean =>
  row.kind !== 'project'
  && row.session_live === false
  && ['active', 'in_progress'].includes(row.status.toLowerCase());

/** Below this many sessions a percentage says more than the sample does. */
const MIN_SAMPLE_FOR_PERCENT = 5;

/** "n · p%" over a real sample, "n of d" over a small one. */
const ratio = (n: number, d: number): string =>
  d >= MIN_SAMPLE_FOR_PERCENT
    ? `${n.toLocaleString()} · ${Math.round((n / d) * 100)}%`
    : `${n.toLocaleString()} of ${d.toLocaleString()}`;

/** v5.219.0 — session hygiene: are sessions exiting cleanly, and are they
 *  telling us anything on the way out? Counted over sessions STARTED in the
 *  window, independent of call volume — a session whose agent never connected
 *  made no calls and is exactly what this shows. v5.267.0 — one strip of four
 *  measures (ended-by-operator folds into the first); the percentage
 *  explanations live on each (i). Renders nothing without the field or with
 *  no sessions in the window. */
const HygieneStrip: React.FC<{ hygiene: Hygiene | null }> = ({ hygiene }) => {
  if (!hygiene || hygiene.sessions_started === 0) return null;
  const h = hygiene;
  const warn = (on: boolean, text: string) => (
    <span className={on ? 'text-warning' : undefined}>{text}</span>
  );
  return (
    <PostureSection title="Session hygiene">
      <div className="grid gap-y-md divide-border sm:grid-cols-2 lg:grid-cols-4 lg:divide-x">
        <PostureMeasure
          label="Sessions started"
          info="Agent sessions started in the window, whatever they did afterwards — including ones whose agent never connected and made no calls."
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
          info="Sessions that filed at least one feedback item, out of sessions STARTED — a percentage once there are at least five. The feedback loop depends on agents saying where they retried, guessed or worked around something."
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

/** API-call analytics from the per-call audit log. v5.267.0 — a section only
 *  when there were calls; an empty 14-day chart is one caption line. Loading
 *  and failure are plain lines (the failure keeps its Retry). */
const ApiCallSection: React.FC<{
  summary: AgentActivitySummary | null;
  error?: boolean;
  onRetry?: () => void;
}> = ({ summary, error, onRetry }) => {
  const navigate = useNavigate();
  if (error) {
    // Distinct from loading: a failed fetch used to render the spinner
    // forever (the error was swallowed to null), indistinguishable from a
    // slow load and with no way to retry.
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

  const maxDay = Math.max(1, ...summary.daily.map((d) => d.calls));
  const sb = summary.status_breakdown;
  const openSession = (s: { workflow: string; session_id: number }) => {
    if (s.workflow === 'recon') navigate(`/recon/runs/${s.session_id}`);
    else if (s.workflow === 'execution') navigate(`/executions/${s.session_id}`);
    else if (s.workflow === 'plan') navigate(`/test-plans/${s.session_id}`);
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
        {summary.by_workflow.length > 0 && (
          <span className="text-caption text-muted-foreground">
            {' '}— by workflow: {summary.by_workflow.map((w) => `${w.workflow} ${w.calls.toLocaleString()}`).join(' · ')}
          </span>
        )}
      </p>

      {summary.daily.length > 0 && (
        <div className="mt-sm">
          <p className="mb-xxs text-caption text-muted-foreground">Calls per day</p>
          <div className="flex h-16 items-end gap-[2px]">
            {summary.daily.map((d) => (
              <Tooltip key={d.day}>
                <TooltipTrigger asChild>
                  {/* Focusable button (not a bare div) so keyboard + screen
                      readers can reach the daily value via aria-label; the
                      tooltip also opens on focus. */}
                  <button
                    type="button"
                    aria-label={`${d.day}: ${d.calls.toLocaleString()} call${d.calls === 1 ? '' : 's'}${d.errors > 0 ? `, ${d.errors.toLocaleString()} error${d.errors === 1 ? '' : 's'}` : ''}`}
                    className={cn(
                      'min-w-[3px] flex-1 rounded-sm border-0 p-0',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      d.errors > 0 ? 'bg-destructive' : 'bg-info',
                    )}
                    style={{ height: `${Math.max(4, (d.calls / maxDay) * 100)}%` }}
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
      )}

      {summary.busiest_sessions.length > 0 && (
        <div className="mt-sm">
          <p className="mb-xxs text-caption text-muted-foreground">Busiest sessions</p>
          <ul className="flex flex-col">
            {summary.busiest_sessions.slice(0, 5).map((s) => {
              const linkable = s.workflow === 'recon' || s.workflow === 'execution' || s.workflow === 'plan';
              return (
                <li key={`${s.workflow}-${s.session_id}`} className="flex min-w-0 items-center gap-xs text-metadata">
                  <span className="min-w-0 truncate">
                    <span className="text-muted-foreground">{s.workflow}</span> #{s.session_id} ·{' '}
                    <strong>{s.calls.toLocaleString()}</strong> call{s.calls === 1 ? '' : 's'}
                  </span>
                  {linkable && (
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
              );
            })}
          </ul>
        </div>
      )}
    </PostureSection>
  );
};

const ProjectActivity: React.FC = () => {
  const navigate = useNavigate();
  const [rows, setRows] = useState<AgentSessionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<ModelToolSummaryRow[] | null>(null);
  const [apiSummary, setApiSummary] = useState<AgentActivitySummary | null>(null);
  const [apiSummaryError, setApiSummaryError] = useState(false);
  // Grows on "Load older runs" so the unified timeline isn't silently capped.
  const [limit, setLimit] = useState(200);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  const { user } = useAuth();
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [endingId, setEndingId] = useState<number | null>(null);
  // v5.214.0 — the row a Resume click is about; null keeps the dialog closed.
  const [resumeRow, setResumeRow] = useState<AgentSessionRow | null>(null);
  const [kindFilter, setKindFilter] = useState<'' | AgentSessionKind>('');
  const [modelFilter, setModelFilter] = useState('');
  const [toolFilter, setToolFilter] = useState('');

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

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    setApiSummaryError(false);
    try {
      const filters: Record<string, string | number> = { limit };
      if (kindFilter) filters.kind = kindFilter;
      if (modelFilter) filters.model = modelFilter;
      if (toolFilter) filters.tool = toolFilter;
      // API-call analytics is best-effort — its failure must not blank
      // the session timeline; record the error so the section shows an
      // "unavailable + Retry" line instead of an endless spinner.
      const [list, sum, apiSum] = await Promise.all([
        listAgentSessions(filters),
        getAgentSessionSummary(),
        getAgentActivitySummary().catch(() => { setApiSummaryError(true); return null; }),
      ]);
      setRows(list.sessions);
      setTotal(list.total);
      setSummary(sum.summary);
      setApiSummary(apiSum);
    } catch (e: unknown) {
      setError(formatApiError(e, 'Failed to load project activity.'));
    } finally {
      setLoading(false);
    }
  }, [kindFilter, modelFilter, toolFilter, limit]);

  useEffect(() => {
    fetchAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchAll, refreshNonce]);

  // v5.212.0 — the operator's kill switch for a unified project session. The
  // per-workflow rows have their own detail pages; a project session had none,
  // so its row on the hub page went nowhere and — worse — a session started from
  // Scopes / Test Plans / Execute could not be stopped short of its key's TTL.
  // Owner or project admin only; the backend enforces that and answers 403.
  const canEnd = (row: AgentSessionRow) =>
    row.kind === 'project'
    && row.status === 'active'
    && (row.user_id === user?.id || user?.role === 'admin');

  // v5.214.0 — the other button. An active project session whose agent died
  // mid-tool is resumable by the operator who started it (the key acts under
  // their name, so no admin override — the backend answers 403 otherwise).
  const canResume = (row: AgentSessionRow) =>
    row.kind === 'project'
    && row.status === 'active'
    && row.user_id === user?.id;

  /** One line under the status badge on a project row: whether the key is
   *  live, lapsed-but-renewable, or gone. Reads the two dates the row carries;
   *  says nothing when it has neither (a legacy row). */
  const keyState = (row: AgentSessionRow): { text: string; tone: 'ok' | 'warn' | 'muted' } | null => {
    if (row.kind !== 'project' || row.status !== 'active') return null;
    const now = Date.now();
    const exp = row.key_expires_at ? new Date(row.key_expires_at).getTime() : null;
    const cap = row.renewable_until ? new Date(row.renewable_until).getTime() : null;
    if (exp != null && exp > now) {
      return { text: `key valid until ${fmtTime(row.key_expires_at)}`, tone: 'ok' };
    }
    if (cap != null && cap > now) {
      return {
        text: exp == null
          ? `key revoked · resumable until ${fmtTime(row.renewable_until)}`
          : `key expired · renewable until ${fmtTime(row.renewable_until)}`,
        tone: 'warn',
      };
    }
    if (exp != null || cap != null) return { text: 'key expired · past lifetime', tone: 'muted' };
    return null;
  };

  const handleEnd = async (row: AgentSessionRow) => {
    // v5.219.0 — the wrap-up handoff. Sessions end because the human stops
    // typing, and nobody tells the agent it is done, so the feedback and the
    // clean exit never happen. If the agent is still reachable, the operator
    // can paste this first; End underneath remains the fallback.
    const agentAlive = keyState(row)?.tone === 'ok';
    const ok = await confirm({
      title: `End agent session #${row.id}?`,
      severity: 'warning',
      confirmLabel: 'End session',
      body: (
        <div className="flex flex-col gap-sm">
          <p>
            The agent’s API key is revoked immediately; any agent still running against it
            gets 401s from its next call. Open reconnaissance runs are marked abandoned, open
            execution runs are paused (resumable), and draft plans are kept. The session
            record stays for the audit trail.
          </p>
          {agentAlive && (
            <div>
              <p className="mb-xxs text-metadata font-semibold">
                Agent still connected? Paste this to it first
              </p>
              <p className="mb-xxs text-caption text-muted-foreground">
                It files the feedback we ask every session for and ends the session cleanly
                (
                <span className="font-mono">end_reason: agent</span>
                ). Ending from here is the fallback for an agent that is gone.
              </p>
              <CodeBlock
                text={WRAP_UP_PROMPT}
                label="wrap-up prompt"
                className="max-h-40 whitespace-pre-wrap break-words"
              />
            </div>
          )}
        </div>
      ),
    });
    if (!ok) return;
    setEndingId(row.id);
    try {
      await endAgentSession(row.id);
      toast.success(`Agent session #${row.id} ended — its key is revoked.`);
      setRefreshNonce((n) => n + 1);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not end the agent session.'));
    } finally {
      setEndingId(null);
    }
  };

  const drillInto = (row: AgentSessionRow) => {
    if (row.kind === 'execution') {
      navigate(`/executions/${row.id}`);
    } else if (row.kind === 'plan_generation' && row.test_plan_id != null) {
      navigate(`/test-plans/${row.test_plan_id}`);
    } else if (row.kind === 'recon') {
      navigate(`/recon/runs/${row.id}`);
    } else if (row.kind === 'assist') {
      navigate(`/assist-sessions/${row.id}`);
    }
  };

  const hygiene = apiSummary?.session_hygiene ?? null;
  const stalledRuns = rows.filter(isStalledRun).length;
  // v5.288.0 — the Model · Tool column only when some row on screen carries
  // one; the rollup section already says when no agent has reported.
  const showModel = rows.some((r) => r.generated_by_model || r.generated_by_tool);

  return (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      {confirmEl}
      <ResumeAgentSessionDialog
        session={resumeRow}
        onOpenChange={(next) => { if (!next) setResumeRow(null); }}
        onResumed={() => setRefreshNonce((n) => n + 1)}
      />
      <div className="flex items-start justify-between gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title">Agent Runs</h1>
          {/* v5.288.0 — this page and Agent Sessions overlap (a session is a
              row on both), so each says what it is for and points at the
              other. Wraps rather than truncating: it is the page's job line. */}
          <p className="mt-xxs max-w-4xl text-metadata text-muted-foreground">
            Everything agents have done on this project, in time order: each agent session
            (with its key state, and Resume / End) and every recon, plan or execution run
            across workflows, with its own status. For one session&rsquo;s authority, notes
            and API calls, see{' '}
            <Link to="/assist-sessions" className="text-primary underline-offset-4 hover:underline">
              Agent Sessions
            </Link>
            .
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setRefreshNonce((n) => n + 1)}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="size-4" aria-hidden />
          )}
          Refresh
        </Button>
      </div>

      <RunsLead
        hygiene={hygiene}
        windowDays={apiSummary?.window_days ?? null}
        total={summary ? summary.reduce((n, r) => n + r.total, 0) : null}
        stalledRuns={stalledRuns}
      />

      <HygieneStrip hygiene={hygiene} />

      <ApiCallSection
        summary={apiSummary}
        error={apiSummaryError}
        onRetry={() => setRefreshNonce((n) => n + 1)}
      />

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <PostureSection
        title="Runs"
        actions={(
          <>
            {loading && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}
            <span className="text-muted-foreground">
              {rows.length} of {total} shown
            </span>
            {rows.length < total && !loading && (
              <Button size="sm" variant="outline" onClick={() => setLimit((l) => l + 200)}>
                Load older runs
              </Button>
            )}
          </>
        )}
      >
        <div className="flex flex-wrap items-end gap-sm border-b border-border pb-sm" data-testid="runs-filters">
          <div className="w-48">
            <Label htmlFor="pa-kind">Workflow</Label>
            <Select
              value={kindFilter || 'all'}
              onValueChange={(v) =>
                setKindFilter(v === 'all' ? '' : (v as AgentSessionKind))
              }
            >
              <SelectTrigger id="pa-kind"><SelectValue /></SelectTrigger>
              <SelectContent>
                {KIND_OPTIONS.map((o) => (
                  <SelectItem key={o.value || 'all'} value={o.value || 'all'}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="w-52">
            <Label htmlFor="pa-model">Model</Label>
            <Select
              value={modelFilter || 'all'}
              onValueChange={(v) => setModelFilter(v === 'all' ? '' : v)}
            >
              <SelectTrigger id="pa-model"><SelectValue placeholder="All models" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All models</SelectItem>
                {knownModels.map((m) => (
                  <SelectItem key={m} value={m}>{m}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="w-48">
            <Label htmlFor="pa-tool">Tool</Label>
            <Select
              value={toolFilter || 'all'}
              onValueChange={(v) => setToolFilter(v === 'all' ? '' : v)}
            >
              <SelectTrigger id="pa-tool"><SelectValue placeholder="All tools" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All tools</SelectItem>
                {knownTools.map((t) => (
                  <SelectItem key={t} value={t}>{t}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <Table className="min-w-[1000px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-28">Workflow</TableHead>
                {/* Status carries the key / ended line under its chip. */}
                <TableHead className="w-52">Status</TableHead>
                <TableHead className="w-28">Started</TableHead>
                {showModel && <TableHead className="w-52">Model · Tool</TableHead>}
                <TableHead className="w-56">User · Agent</TableHead>
                <TableHead>Subject</TableHead>
                {/* v5.214.0 — two icon buttons (Resume + End) on a project row. */}
                <TableHead className="w-24" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => {
                const stalled = isStalledRun(r);
                const ks = stalled
                  ? {
                      text: 'stalled — its session ended; resume or close the run',
                      tone: 'warn' as const,
                    }
                  : keyState(r) ?? endedState(r);
                const userName = r.user_full_name?.trim() || r.user_username || null;
                return (
                  <TableRow key={`${r.kind}-${r.id}`} data-testid="run-row">
                    <TableCell>
                      <Badge variant={kindBadgeVariant(r.kind)} className="whitespace-nowrap">
                        {r.kind === 'plan_generation' ? 'plan-gen' : r.kind === 'project' ? 'session' : r.kind}
                      </Badge>
                    </TableCell>
                    <TableCell className="overflow-hidden">
                      {/* whitespace-nowrap prevents the badge from
                          wrapping mid-status (e.g. "in" + "_progress") */}
                      <Badge
                        variant={stalled ? 'warning' : statusBadgeVariant(r.status)}
                        className="whitespace-nowrap"
                      >
                        {stalled ? `${r.status} · session ended` : r.status}
                      </Badge>
                      {/* v5.214.0 — "active" alone cannot tell a live agent
                          from one that died a day ago; the key's state can. */}
                      {ks && (
                        <p
                          className={cn(
                            'mt-xxs max-w-full truncate text-caption',
                            ks.tone === 'warn' ? 'text-warning' : 'text-muted-foreground',
                          )}
                          title={ks.text}
                        >
                          {ks.text}
                        </p>
                      )}
                    </TableCell>
                    <TableCell>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="text-caption text-muted-foreground">
                            {fmtRelative(r.started_at) || '—'}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>{fmtTime(r.started_at)}</TooltipContent>
                      </Tooltip>
                    </TableCell>
                    {showModel && (
                      <TableCell>
                        {r.generated_by_model || r.generated_by_tool ? (
                          <p
                            className="truncate text-caption"
                            title={[r.generated_by_model, r.generated_by_tool].filter(Boolean).join(' · ')}
                          >
                            {r.generated_by_model && (
                              <code className="font-mono">{r.generated_by_model}</code>
                            )}
                            {r.generated_by_tool && (
                              <span className="text-muted-foreground">
                                {r.generated_by_model ? ' · ' : ''}
                                {r.generated_by_tool}
                              </span>
                            )}
                          </p>
                        ) : (
                          <span className="text-caption text-muted-foreground">(not reported)</span>
                        )}
                      </TableCell>
                    )}
                    <TableCell>
                      {/* v5.288.0 — the person's full name (username as the
                          fallback and in the tooltip) on its own line, the
                          agent's name under it; both wrap instead of cutting
                          off mid-word. */}
                      <div
                        className="min-w-0 text-caption"
                        title={[
                          userName && r.user_username && userName !== r.user_username
                            ? `${userName} (${r.user_username})`
                            : userName,
                          r.agent_name,
                        ].filter(Boolean).join(' · ') || undefined}
                      >
                        <p className="line-clamp-2 break-words text-foreground">
                          {safeFallback(userName, '—')}
                        </p>
                        {r.agent_name && (
                          <p className="line-clamp-2 break-words text-muted-foreground">
                            {r.agent_name}
                          </p>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      {/* v5.187.0 — the declared target in words where we have
                          it; ids are the fallback. A colleague scanning this
                          list needs the ranges, not "Scope #3". v5.267.0 — the
                          purpose follows on the same truncated line. */}
                      <p
                        className="truncate"
                        title={[r.target_label, r.kind === 'project' ? r.purpose : null].filter(Boolean).join(' · ') || undefined}
                      >
                        {r.target_label ? (
                          <span>{r.target_label}</span>
                        ) : (
                          <>
                            {r.kind === 'recon' && r.scope_id != null && <span>Scope #{r.scope_id}</span>}
                            {(r.kind === 'plan_generation' || r.kind === 'execution') &&
                              r.test_plan_id != null && <span>Plan #{r.test_plan_id}</span>}
                            {(r.kind === 'assist' || r.kind === 'project') && (
                              <span className="text-caption text-muted-foreground">
                                Project session
                              </span>
                            )}
                          </>
                        )}
                        {r.kind === 'project' && r.purpose && (
                          <span className="text-caption text-muted-foreground"> · {r.purpose}</span>
                        )}
                      </p>
                    </TableCell>
                    <TableCell>
                      {r.kind === 'project' ? (
                        <div className="flex items-center gap-xxs">
                          {canResume(r) && (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => setResumeRow(r)}
                                  aria-label={`Resume agent session ${r.id}`}
                                >
                                  <RotateCcw className="size-4 text-primary" aria-hidden />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>Resume (reconnect an agent to this session)</TooltipContent>
                            </Tooltip>
                          )}
                          {canEnd(r) && (
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => handleEnd(r)}
                                  disabled={endingId === r.id}
                                  aria-label={`End agent session ${r.id}`}
                                >
                                  {endingId === r.id ? (
                                    <Loader2 className="size-4 animate-spin" aria-hidden />
                                  ) : (
                                    <Square className="size-4 text-warning" aria-hidden />
                                  )}
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>End session (revokes its key)</TooltipContent>
                            </Tooltip>
                          )}
                        </div>
                      ) : (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => drillInto(r)}
                              aria-label={`Open ${r.kind === 'plan_generation' ? 'plan' : r.kind} run ${r.id}`}
                              disabled={
                                // Assist has its own detail page and no target
                                // id — keyed off the session id alone. Without
                                // this branch the row's Open button was disabled
                                // because test_plan_id is (correctly) null.
                                r.kind === 'assist'
                                  ? false
                                  : r.kind === 'recon'
                                  ? r.scope_id == null
                                  : r.test_plan_id == null
                              }
                            >
                              {/* Navigates in place — a chevron, not the
                                  new-tab icon it used to carry. */}
                              <ChevronRight className="size-4" aria-hidden />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Open</TooltipContent>
                        </Tooltip>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
              {!loading && rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={showModel ? 7 : 6} className="py-xl text-center">
                    <Search className="mx-auto mb-xs size-9 text-muted-foreground/50" aria-hidden />
                    <p className="text-metadata text-muted-foreground">
                      No agent sessions match the current filters.
                    </p>
                    {(kindFilter || modelFilter || toolFilter) && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setKindFilter('');
                          setModelFilter('');
                          setToolFilter('');
                        }}
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
        </div>
      </PostureSection>

      <ModelRollupSection rows={summary} />
    </div>
  );
};

export default ProjectActivity;
