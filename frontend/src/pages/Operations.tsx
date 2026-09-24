import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Loader2, MessageCircleQuestion, RefreshCw, Rocket, Sparkles, SquareArrowOutUpRight } from 'lucide-react';
import StartAssistDialog from '../components/StartAssistDialog';
import {
  AgentSessionRow,
  DashboardStats,
  OperationsBlockers,
  ProjectCoverageResponse,
  ScopeCoverageRow,
  SinceLastVisit,
  TestPlanSummary,
  WorkbenchResponse,
  getDashboardStats,
  getProjectCoverage,
  getTestPlans,
  getWorkbench,
  listAgentSessions,
  markWorkbenchSeen,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useReconPlan } from '../hooks/useReconPlan';
import { formatApiError } from '../utils/apiErrors';
import StartReconDialog from '../components/StartReconDialog';
import MyWorkCard, { personalWorkCounts } from '../components/MyWorkCard';
import MyActivityCard from '../components/MyActivityCard';
import UpdatedAt from '../components/UpdatedAt';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import PostureSection from '../components/posture/PostureSection';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import SeverityBar from '../components/ui/SeverityBar';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { sinceChips } from '../utils/sinceLastVisit';
import { safeFallback } from '../utils/uiStyles';
import { cn } from '../utils/cn';
import { useMyAssistSessions } from '../hooks/useMyAssistSessions';
import { formatRelativeTime } from '../utils/relativeTime';

type ScopeView = 'all' | 'mine';
/** The older of two load times; null until both have loaded. */
const olderOf = (a?: Date, b?: Date): Date | null =>
  (a && b ? (a.getTime() <= b.getTime() ? a : b) : null);

/** The page's independently fetched sources, each with its own load time. */
type LoadedSource = 'workbench' | 'coverage' | 'pending' | 'stats';
const SCOPE_STORAGE_KEY = 'nm.operations.scopeView';

const loadStickyScope = (): ScopeView => {
  try {
    const raw = localStorage.getItem(SCOPE_STORAGE_KEY);
    return raw === 'mine' ? 'mine' : 'all';
  } catch {
    return 'all';
  }
};

const persistScope = (view: ScopeView): void => {
  try {
    localStorage.setItem(SCOPE_STORAGE_KEY, view);
  } catch {
    // localStorage may be disabled in private modes — silently ignore.
  }
};

/** Short relative age ("5m ago"). Shared with every other surface —
 *  this was one of four byte-identical copies before v5.179.0. */
const fmtRelative = (iso?: string | null): string =>
  formatRelativeTime(iso, { withSeconds: true });

type Tone = 'default' | 'success' | 'warning' | 'destructive' | 'info' | 'secondary' | 'muted' | 'outline';

const kindTone = (kind: string): Tone => {
  if (kind === 'recon') return 'secondary';
  if (kind === 'plan_generation') return 'default';
  if (kind === 'execution') return 'success';
  // v5.185.0 — assist is a fourth kind on this timeline. It used to be absent
  // entirely: an operator with a live assist key saw nothing on Agent Runs.
  if (kind === 'assist') return 'info';
  return 'muted';
};

const kindLabel = (kind: string): string => {
  if (kind === 'plan_generation') return 'Plan gen';
  return kind.charAt(0).toUpperCase() + kind.slice(1);
};

// ---------------------------------------------------------------------------
// Security snapshot — project-wide totals + vulnerability severity mix.
// Sourced from /dashboard/stats (vulnerability_stats already aggregates
// findings by severity + hosts-with-vulns).  The severity bar is a
// dependency-free CSS stacked bar: clearer than a chart for five fixed
// buckets and it can't introduce horizontal overflow.
// ---------------------------------------------------------------------------

// RV-UI — "Security snapshot" (exposure/findings) and "Project coverage"
// (pipeline progress) answered different questions but both described
// overall project state, so they are one "Project state" section with an
// Exposure line and a Coverage strip; Hosts appears once (in Exposure).
// v5.267.0 — a section over a thin rule, the coverage counts one strip of
// PostureMeasures (UI_STYLE_GUIDE §7), not a card of tile cards.
const ProjectStateSection: React.FC<{
  stats: DashboardStats | null;
  statsLoading: boolean;
  coverage: ProjectCoverageResponse | null;
  coverageLoading: boolean;
  /** "updated …" beside the heading (components/UpdatedAt). */
  updated?: React.ReactNode;
}> = ({ stats, statsLoading, coverage, coverageLoading, updated }) => {
  if ((statsLoading && !stats) || (coverageLoading && !coverage)) {
    return (
      <PostureSection title={<span>Project state</span>}>
        <p role="status" aria-live="polite" className="flex items-center gap-xs text-metadata text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading project state…
        </p>
      </PostureSection>
    );
  }
  if (!stats && !coverage) return null;

  const vuln = stats?.vulnerability_stats;
  const sevTotal = vuln
    ? vuln.critical + vuln.high + vuln.medium + vuln.low + vuln.info
    : 0;
  // Informational is excluded from the severity bar (it dwarfs real severities);
  // the bar's denominator must be the non-info total so its segments fill the rail.
  const actionableTotal = vuln
    ? vuln.critical + vuln.high + vuln.medium + vuln.low
    : 0;
  const busy = statsLoading || coverageLoading;

  return (
    <PostureSection
      title={<span>Project state</span>}
      description={<>
        Exposure (scanner observations, not yet judged) and assessment coverage. Findings — the
        curated record — are on the <Link to="/findings" className="text-info hover:underline">Findings</Link> page;
        the assessment itself on <Link to="/posture" className="text-info hover:underline">Posture</Link>.
      </>}
      actions={updated}
    >
      <div aria-busy={busy || undefined} className="flex flex-col gap-md">
        {stats && (
          <div>
            <h3 className="mb-xs text-metadata font-semibold text-foreground">Exposure</h3>
            {/* Compact inline counts (a passive total doesn't earn a big
                number — only the host count navigates). The severity bar
                carries the per-severity drill-downs. */}
            <div className="mb-sm flex flex-wrap items-center gap-x-sm gap-y-xxs text-metadata">
              <Link to={buildHostsUrl({})}
                className="font-semibold text-foreground hover:text-info hover:underline">
                {stats.total_hosts.toLocaleString()} hosts
              </Link>
              <InfoTip text={'Total distinct hosts in the project. "marked up" counts only hosts a scanner explicitly tagged host-status "up"; hosts from masscan/naabu/DNS/subnet seeds are often left "unknown" even when reachable — so it\'s usually far lower than the total and is NOT a liveness count.'} />
              <span className="text-muted-foreground" aria-hidden>·</span>
              <span className="text-muted-foreground">{stats.up_hosts.toLocaleString()} marked up</span>
              <span className="text-muted-foreground" aria-hidden>·</span>
              <span className="text-muted-foreground">
                {(vuln?.hosts_with_vulnerabilities ?? 0).toLocaleString()} with vulns
              </span>
            </div>

            {vuln && actionableTotal > 0 ? (
              <div className="max-w-3xl">
                <div className="mb-xs flex flex-wrap items-baseline justify-between gap-x-md gap-y-xxs">
                  <span className="text-caption text-muted-foreground">
                    Share of scanner-detected vulnerabilities
                  </span>
                  <span className="text-caption text-muted-foreground tabular-nums">
                    {actionableTotal.toLocaleString()} actionable ·{' '}
                    {(vuln.hosts_with_vulnerabilities ?? 0).toLocaleString()} hosts affected
                  </span>
                </div>
                <SeverityBar
                  variant="summary"
                  counts={vuln}
                  total={actionableTotal}
                  ariaLabel="Share of scanner-detected vulnerabilities by severity"
                  // SeverityBar never renders info, but the callback is typed
                  // over all severities — guard so the type narrows to HostSeverity.
                  segmentHref={(sev) => (sev === 'info' ? null : buildHostsUrl({ severity: sev }))}
                />
              </div>
            ) : vuln && sevTotal > 0 ? (
              <p className="text-metadata text-muted-foreground">
                Only informational findings detected — no critical/high/medium/low
                vulnerabilities to prioritise.
              </p>
            ) : (
              <p className="text-metadata text-muted-foreground">
                No vulnerabilities detected yet — upload a Nessus or OpenVAS scan to populate this.
              </p>
            )}
          </div>
        )}

        {coverage && (
          <div>
            <h3 className="mb-xs text-metadata font-semibold text-foreground">Coverage</h3>
            {/* The gap lines are the point of this strip: each opens the
                hosts it counts. */}
            <div className="grid gap-y-md divide-border sm:grid-cols-3 sm:divide-x">
              <PostureMeasure
                label="With plan entries"
                info="Hosts that appear in at least one test plan."
                value={coverage.hosts_with_plan_entry.toLocaleString()}
                to={coverage.hosts_with_plan_entry > 0 ? buildHostsUrl({ q: 'has:planned' }) : undefined}
                toLabel="With plan entries — view hosts"
              >
                <GapLine
                  text={coverage.hosts_no_plan > 0
                    ? `${coverage.hosts_no_plan.toLocaleString()} not yet in any plan`
                    : 'all hosts planned'}
                  to={coverage.hosts_no_plan > 0 ? buildHostsUrl({ q: 'NOT has:planned' }) : undefined}
                />
              </PostureMeasure>
              <PostureMeasure
                label="With execution results"
                info="Hosts with at least one recorded test-plan execution result."
                value={coverage.hosts_with_execution_result.toLocaleString()}
                to={buildHostsUrl({ hasTestExecution: true })}
                toLabel="With execution results — view hosts"
              >
                <GapLine
                  text={coverage.hosts_no_execution > 0
                    ? `${coverage.hosts_no_execution.toLocaleString()} not yet tested`
                    : 'all hosts tested'}
                  to={coverage.hosts_no_execution > 0 ? buildHostsUrl({ q: 'NOT has:tested' }) : undefined}
                />
              </PostureMeasure>
              <PostureMeasure
                label="Outside scope"
                info="Hosts discovered at addresses no declared scope covers."
                value={coverage.hosts_outside_scope.toLocaleString()}
                to={coverage.hosts_outside_scope > 0 ? buildHostsUrl({ outOfScopeOnly: true }) : undefined}
                toLabel="Outside scope — view hosts"
              >
                {coverage.total_scopes === 0 ? 'no scopes declared' : 'discovered but unscoped'}
              </PostureMeasure>
            </div>

            {coverage.scopes.length > 0 && (
              <div className="mt-md">
                <h3 className="mb-xs text-metadata font-semibold text-foreground">
                  Scope coverage ({coverage.total_scopes})
                </h3>
                <ul className="divide-y divide-border/60">
                  {coverage.scopes.map((row) => (
                    <ScopeCoverageRowDisplay key={row.scope_id} row={row} />
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </PostureSection>
  );
};

/** A coverage gap: a link when it has hosts to open, plain text otherwise. */
const GapLine: React.FC<{ text: string; to?: string }> = ({ text, to }) => (to ? (
  <Link to={to} className="rounded text-warning hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
    {text}
  </Link>
) : <>{text}</>);

// v4.18.0 — scope coverage row no longer pretends to show "% scope
// completion".  Pre-fix, the denominator was the raw /32 count for
// every CIDR in the scope (256 addresses for a /24, including
// network + broadcast + dark space).  A /24 with every live host
// found rendered as "22 / 256 — 8.59%", which reads as failure when
// it's actually success.  Address-space size and asset-discovery
// progress are different questions; this card answers "what
// inventory does this scope hold?", not "how complete is recon?".
//
// Sentinel ``__default__`` (DEFAULT_SCOPE_NAME from the backend's
// scope helper) is the auto-created scope every fresh project gets.
// Renamed in display to "Project default scope" so operators don't
// see the underscores leak through.
const SENTINEL_SCOPE_NAME = '__default__';

function displayScopeName(rawName: string | null | undefined): string {
  if (!rawName) return '—';
  if (rawName === SENTINEL_SCOPE_NAME) return 'Project default scope';
  return rawName;
}

const ScopeCoverageRowDisplay: React.FC<{ row: ScopeCoverageRow }> = ({ row }) => {
  return (
    <li className="flex flex-wrap items-baseline gap-x-sm gap-y-xxs py-xxs">
      <p className="min-w-0 flex-1 truncate text-metadata">
        <strong>{displayScopeName(row.scope_name)}</strong>{' '}
        <span className="text-caption text-muted-foreground">
          ({row.subnet_count} subnet{row.subnet_count === 1 ? '' : 's'},{' '}
          {row.total_scoped_ips.toLocaleString()} scoped IPs)
        </span>
      </p>
      <span className="text-caption text-muted-foreground">
        {row.discovered_in_scope.toLocaleString()} host
        {row.discovered_in_scope === 1 ? '' : 's'} discovered
      </span>
    </li>
  );
};

// ---------------------------------------------------------------------------
// Needs-attention section
// ---------------------------------------------------------------------------

const NeedsAttentionSection: React.FC<{
  pendingPlans: TestPlanSummary[] | null;
  loading: boolean;
  // Approving a plan needs analyst+; for viewers/auditors this is passive
  // project context, not personal work (§27 role-aware approvals).
  canApprove: boolean;
  updated?: React.ReactNode;
  /** The fetch failed and there is nothing cached: the queue is UNKNOWN. */
  unavailable?: boolean;
}> = ({ pendingPlans, loading, canApprove, updated, unavailable = false }) => {
  const navigate = useNavigate();

  if (loading && !pendingPlans) {
    return (
      <p role="status" aria-live="polite" className="flex items-center gap-xs text-metadata text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden /> Loading approvals…
      </p>
    );
  }

  const hasAny = (pendingPlans?.length ?? 0) > 0;

  // v5.244.0 (code review D6) — a failed load is "unknown", never "nothing":
  // this rendered "Nothing needs your approval right now" directly under the
  // error saying approvals could not be loaded. Same rule as every other
  // section on this page: unavailable is said, not shown as empty.
  if (unavailable && !hasAny) {
    return (
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-sm text-caption text-muted-foreground">
        <h2 className="text-metadata font-semibold text-foreground">
          {canApprove ? 'Needs your approval' : 'Pending approvals'}
        </h2>
        <span role="status">Could not be checked — this is not a confirmation that nothing is waiting.</span>
      </div>
    );
  }

  // Nothing waiting: one line, not a card explaining a queue that is empty.
  // The heading stays a heading so the section is still findable.
  if (!hasAny) {
    return (
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-sm text-caption text-muted-foreground">
        <h2 className="text-metadata font-semibold text-foreground">
          {canApprove ? 'Needs your approval' : 'Pending approvals'}
        </h2>
        <span>{canApprove ? 'Nothing needs your approval right now.' : 'No plans are awaiting approval.'}</span>
        {updated}
      </div>
    );
  }

  return (
    <PostureSection
      title={<span>{canApprove ? 'Needs your approval' : 'Pending approvals'}</span>}
      description={canApprove
        ? 'Agent-drafted test plans awaiting your approve/reject decision. Project-wide.'
        : 'Agent-drafted test plans awaiting an analyst’s approve/reject decision. Shown for visibility — approving needs the analyst role.'}
      actions={updated}
    >
      {pendingPlans && pendingPlans.length > 0 && (
        <div>
          <p className="mb-xs text-metadata font-semibold text-warning">
            {pendingPlans.length} pending review
          </p>
          <ul className="divide-y divide-border/60">
            {pendingPlans.slice(0, 5).map((plan) => (
              <li key={plan.id} className="flex flex-wrap items-center gap-xs py-xxs">
                <p className="min-w-0 flex-1 truncate text-metadata">
                  <strong>#{plan.id}</strong> v{plan.version} · {plan.title || '—'}{' '}
                  <span className="text-caption text-muted-foreground">
                    · {plan.entry_count} entr{plan.entry_count === 1 ? 'y' : 'ies'}
                    {plan.generated_by_model && ` · by ${plan.generated_by_model}`}
                  </span>
                </p>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-info"
                  onClick={() => navigate(`/test-plans/${plan.id}`)}
                >
                  {canApprove ? 'Review' : 'View'}
                </Button>
              </li>
            ))}
          </ul>
          {pendingPlans.length > 5 && (
            <p className="mt-xs text-caption text-muted-foreground">
              + {pendingPlans.length - 5} more — see <Link to="/test-plans" className="text-info hover:underline">Test Plans</Link>.
            </p>
          )}
        </div>
      )}
    </PostureSection>
  );
};

// ---------------------------------------------------------------------------
// Runs section
// ---------------------------------------------------------------------------

type RunsStatusFilter = 'all' | 'active' | 'completed' | 'failed';

const RUNS_STATUS_OPTIONS: Array<{ value: RunsStatusFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
];

const SessionRowDisplay: React.FC<{ session: AgentSessionRow }> = ({ session }) => {
  const navigate = useNavigate();
  // v5.187.0 — prefer the declared target in words. "Scope #3" cannot tell a
  // second analyst that a range is already being scanned, which is the whole
  // reason a session declares one. Falls back to the id when a label can't be
  // resolved (deleted scope, or a deployment mid-upgrade).
  const subject =
    session.target_label
    || (session.kind === 'recon'
      ? session.scope_id
        ? `Scope #${session.scope_id}`
        : '—'
      : session.kind === 'plan_generation' || session.kind === 'execution'
      ? session.test_plan_id
        ? `Plan #${session.test_plan_id}`
        : '—'
      // Assist is project-scoped — it has no plan or scope to name, which is
      // the point of the workflow rather than missing data.
      : session.kind === 'assist'
      ? 'Project-wide'
      : '—');

  const handleOpen = () => {
    if (session.kind === 'recon') {
      navigate(`/recon/runs/${session.id}`);
    } else if (session.kind === 'execution') {
      navigate(`/executions/${session.id}`);
    } else if (session.kind === 'plan_generation' && session.test_plan_id) {
      navigate(`/test-plans/${session.test_plan_id}`);
    } else if (session.kind === 'assist') {
      navigate(`/assist-sessions/${session.id}`);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-xs">
      <Badge variant={kindTone(session.kind) === 'default' ? 'outline' : kindTone(session.kind)}>
        {kindLabel(session.kind)}
      </Badge>
      <Badge variant={session.status === 'active' ? 'success' : 'muted'}>{session.status}</Badge>
      <p className="min-w-0 flex-1 truncate text-metadata">
        <strong>#{session.id}</strong> · {subject}{' '}
        <span className="text-caption text-muted-foreground">
          {session.user_username && `by ${session.user_username}`}
          {session.generated_by_model && ` · ${session.generated_by_model}`}
          {session.started_at && ` · ${fmtRelative(session.started_at)}`}
        </span>
      </p>
      <Button size="sm" variant="ghost" className="h-7 text-info" onClick={handleOpen}>
        Open
      </Button>
    </div>
  );
};

const RunsSection: React.FC<{ refreshKey?: number }> = ({ refreshKey = 0 }) => {
  const navigate = useNavigate();
  const { user } = useAuth();
  // The All/Mine scope toggle lives here, not in the page header: it only
  // ever scoped the Runs list (it never touched the personal queue cards or
  // the project-wide sections), so a page-top placement implied a broader
  // effect than it had. Persisted to localStorage + the URL (?scope=) so the
  // choice survives refresh and stays shareable.
  const [searchParams, setSearchParams] = useSearchParams();
  const [scopeView, setScopeView] = useState<ScopeView>(() => {
    const urlScope = searchParams.get('scope');
    if (urlScope === 'mine' || urlScope === 'all') return urlScope;
    return loadStickyScope();
  });
  const userIdFilter = scopeView === 'mine' && user ? user.id : undefined;

  const handleScopeChange = (next: ScopeView) => {
    setScopeView(next);
    persistScope(next);
    const params = new URLSearchParams(searchParams);
    params.set('scope', next);
    setSearchParams(params, { replace: true });
  };

  const [rows, setRows] = useState<AgentSessionRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  // Audit CRIT-7 + PRF·M1: separate `error` from "empty" so a backend
  // outage on the primary landing surface no longer masquerades as
  // "No agent activity recorded yet." Each fresh fetch aborts the
  // previous in-flight one — rapid status-filter toggles previously
  // raced and overwrote results.
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<RunsStatusFilter>('all');
  const [reloadNonce, setReloadNonce] = useState(0);
  // This panel fetches for itself, so it keeps its own load time (v5.243.0).
  const [runsLoadedAt, setRunsLoadedAt] = useState<Date | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    listAgentSessions(
      {
        limit: statusFilter === 'all' ? 10 : 50,
        user_id: userIdFilter,
        status: statusFilter === 'all' ? undefined : statusFilter,
      },
      { signal: controller.signal },
    )
      .then((resp) => {
        if (controller.signal.aborted) return;
        setRows(resp.sessions);
        setRunsLoadedAt(new Date());
      })
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(formatApiError(err, 'Could not load agent runs.'));
      })
      .finally(() => {
        if (controller.signal.aborted) return;
        setLoading(false);
      });
    return () => controller.abort();
  }, [statusFilter, userIdFilter, reloadNonce, refreshKey]);

  return (
    <PostureSection
      title={<span>Runs</span>}
      description="Agent sessions on this project — recon, plan generation, execution and assist."
      actions={<>
          {/* A failed refetch keeps the previous rows under its error. */}
          <UpdatedAt at={runsLoadedAt} stale={!!error} />
          <div
            className="inline-flex overflow-hidden rounded-control border border-border"
            role="group"
            aria-label="Scope of runs view"
          >
            <button
              type="button"
              aria-pressed={scopeView === 'all'}
              onClick={() => handleScopeChange('all')}
              className={cn(
                'px-sm py-xxs text-metadata transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                scopeView === 'all' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
              )}
            >
              All
            </button>
            <button
              type="button"
              aria-pressed={scopeView === 'mine'}
              onClick={() => handleScopeChange('mine')}
              disabled={!user}
              className={cn(
                'border-l border-border px-sm py-xxs text-metadata transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                scopeView === 'mine' ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
                !user && 'cursor-not-allowed opacity-50',
              )}
            >
              Mine
            </button>
          </div>
          <Button size="sm" variant="ghost" className="h-7 text-info" onClick={() => navigate('/agent-activity')}>
            Open Agent Runs
          </Button>
      </>}
    >
        <div className="mb-sm flex flex-wrap items-center gap-xs" role="group" aria-label="Runs status filter">
          {RUNS_STATUS_OPTIONS.map((opt) => {
            const active = statusFilter === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                aria-pressed={active}
                onClick={() => setStatusFilter(opt.value)}
                className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Badge variant={active ? 'default' : 'outline'}>
                  {opt.label}
                </Badge>
              </button>
            );
          })}
          <span className="text-caption text-muted-foreground">
            {statusFilter === 'all'
              ? 'Last 10 runs across all kinds.'
              : `Up to 50 ${statusFilter} runs.`}
          </span>
        </div>
        {loading && !rows ? (
          <div
            className="flex items-center gap-xs text-metadata text-muted-foreground"
            role="status"
            aria-live="polite"
          >
            <Loader2 className="size-4 animate-spin" aria-hidden /> Loading runs…
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertDescription>
              <p className="break-words">{error}</p>
              <Button
                size="sm"
                variant="outline"
                className="mt-xs"
                onClick={() => setReloadNonce((n) => n + 1)}
              >
                <RefreshCw className="size-3.5" aria-hidden /> Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : !rows || rows.length === 0 ? (
          <p className="text-metadata text-muted-foreground">
            {statusFilter === 'all'
              ? 'No agent activity recorded yet.'
              : `No ${statusFilter} runs.`}
          </p>
        ) : (
          <ul className="divide-y divide-border/60">
            {rows.map((row) => (
              <li key={`${row.kind}-${row.id}`} className="py-xxs">
                <SessionRowDisplay session={row} />
              </li>
            ))}
          </ul>
        )}
    </PostureSection>
  );
};

// ---------------------------------------------------------------------------
// Since your last visit — durable per-user/project diff (P2).
// ---------------------------------------------------------------------------

const SinceLastVisitBanner: React.FC<{
  since: SinceLastVisit;
  onDismiss: () => void;
  saving: boolean;
  error: string | null;
}> = ({ since, onDismiss, saving, error }) => {
  // v5.242.0 — a change inbox: each count opens exactly the records it counted
  // (utils/sinceLastVisit). First-ever visit would report "everything is new"
  // — noise, not signal; nothing to show either once the cursor caught up.
  const chips = sinceChips(since);
  if (since.is_first_visit || chips.length === 0) return null;

  return (
    // v5.267.0 — a left-rule callout, not a tinted card (UI_STYLE_GUIDE §7).
    <div className="flex flex-wrap items-center gap-sm border-l-4 border-l-info py-xs pl-md">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-xs">
          <Sparkles className="size-4 shrink-0 text-info" aria-hidden />
          <span className="text-metadata font-semibold text-foreground">Since your last visit</span>
          <div className="flex min-w-0 flex-wrap items-center gap-xxs">
            {chips.map((c) => (c.href ? (
              <Link key={c.key} to={c.href} title={c.hint} aria-label={`${c.label} — ${c.hint}`}
                className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <Badge variant={c.tone} className="gap-xxs hover:underline">
                  {c.label}
                  <SquareArrowOutUpRight className="size-3" aria-hidden />
                </Badge>
              </Link>
            ) : (
              <Badge key={c.key} variant={c.tone} title={c.hint}>{c.label}</Badge>
            )))}
          </div>
        </div>
        <div className="flex items-center gap-xs">
          {/* Acknowledging is what advances the cursor — until then these
              changes persist across visits (no silent loss on a glance).
              "Acknowledge", not "reviewed": dismissing a summary reviews no host. */}
          <Button size="sm" variant="ghost" className="h-7 text-info" onClick={onDismiss} disabled={saving}>
            {saving && <Loader2 className="size-3 animate-spin" aria-hidden />}
            Acknowledge updates
          </Button>
        </div>
        {error && (
          <p role="alert" className="w-full break-words text-caption text-destructive">
            {error}
          </p>
        )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Blockers (v5.242.0) — work that has stopped and will not resume by itself:
// imports that failed or finished partial, execution runs that are paused or
// whose agent session ended.  Each says what is blocked and carries the one
// action that unblocks it.  Renders nothing when nothing is blocked; says so
// when it could not be checked (never "nothing blocked" on a failure).
// ---------------------------------------------------------------------------

const BlockersStrip: React.FC<{
  blockers: OperationsBlockers | null;
  unavailable: boolean;
}> = ({ blockers, unavailable }) => {
  const navigate = useNavigate();

  if (unavailable) {
    return (
      <p role="status" className="text-caption text-muted-foreground">
        Blocked work (failed imports, interrupted runs) could not be checked — this is not a
        confirmation that nothing is blocked.
      </p>
    );
  }
  if (!blockers) return null;
  const importCount = blockers.failed_import_count + blockers.partial_import_count;
  if (importCount === 0 && blockers.interrupted_execution_count === 0) return null;

  const importSummary = [
    blockers.failed_import_count > 0
      ? `${blockers.failed_import_count} import${blockers.failed_import_count === 1 ? '' : 's'} failed`
      : null,
    blockers.partial_import_count > 0
      ? `${blockers.partial_import_count} finished partial`
      : null,
  ].filter(Boolean).join(' · ');
  const moreRuns = blockers.interrupted_execution_count - blockers.executions.length;

  return (
    <div className="space-y-xs border-l-4 border-l-warning py-xs pl-md">
        <div className="flex items-center gap-xs">
          <AlertTriangle className="size-4 shrink-0 text-warning" aria-hidden />
          <h2 className="text-metadata font-semibold text-foreground">Blocked</h2>
          <span className="text-caption text-muted-foreground">
            stopped, and will not resume by itself
          </span>
        </div>

        {importCount > 0 && (
          <div className="flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs">
            <span className="shrink-0 text-metadata text-foreground">{importSummary}</span>
            <span
              className="min-w-0 flex-1 truncate text-caption text-muted-foreground"
              title={blockers.imports.map((i) => `${i.filename}${i.message ? ` — ${i.message}` : ''}`).join('\n')}
            >
              {blockers.imports.map((i) => i.filename).join(', ')}
              {importCount > blockers.imports.length && ` +${importCount - blockers.imports.length} more`}
              {' — '}
              {blockers.failed_import_count > 0
                ? 'nothing from a failed file is in the inventory'
                : 'part of each file is missing from the inventory'}
            </span>
            {/* The filtered view, not every upload: `needs_attention` is the
                same condition these counts were taken with. */}
            <Button size="sm" variant="ghost" className="h-7 shrink-0 text-info"
              onClick={() => navigate('/parse-errors?status=needs_attention')}>
              Inspect import errors
            </Button>
          </div>
        )}

        {blockers.executions.map((run) => (
          <div key={run.session_id} className="flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs">
            <span className="shrink-0 text-metadata text-foreground">
              Run #{run.session_id} {run.reason === 'paused' ? 'is paused' : 'lost its agent session'}
            </span>
            <span
              className="min-w-0 flex-1 truncate text-caption text-muted-foreground"
              title={run.plan_title ?? undefined}
            >
              {safeFallback(run.plan_title, `plan #${run.test_plan_id}`)} — the plan is locked to this
              run until it is resumed or abandoned
            </span>
            <Button size="sm" variant="ghost" className="h-7 shrink-0 text-info"
              onClick={() => navigate(`/executions/${run.session_id}`)}>
              Resume execution
            </Button>
          </div>
        ))}
        {moreRuns > 0 && (
          <Button size="sm" variant="ghost" className="text-caption" onClick={() => navigate('/executions')}>
            +{moreRuns} more interrupted run{moreRuns === 1 ? '' : 's'}
          </Button>
        )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const Operations: React.FC = () => {
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canApprovePlans = hasPermission('analyst');
  // 5.204.3 — the "scope registered, no hosts" setup card's Start Agentic
  // Recon button used to navigate('/scopes') and leave the operator to find
  // the real button there; it now opens the shared recon dialog in place.
  // Recon needs analyst+, same gate as the scan-freshness rows below.
  const canStartRecon = hasPermission('analyst');
  const recon = useReconPlan();

  const [coverage, setCoverage] = useState<ProjectCoverageResponse | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(true);
  const [pendingPlans, setPendingPlans] = useState<TestPlanSummary[] | null>(null);
  const [pendingLoading, setPendingLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-section errors for the non-structural fetches.  Pre-fix a failed
  // stats/pending request silently degraded to an empty card,
  // which reads as "nothing needs attention" — falsely implying a clean
  // project.  Track each so we can show "unavailable" (with Retry) instead
  // of a deceptively-empty section.  (UX review #8.)
  const [statsError, setStatsError] = useState<string | null>(null);
  const [pendingError, setPendingError] = useState<string | null>(null);
  // P2 — Operations owns ONE /workbench fetch covering the personal cards
  // (My Queue / My Tasks) + the since-last-visit diff, and prop-drives them.
  // The page-level Refresh re-runs this in lockstep with the coverage/stats
  // fetches, so everything refreshes together.  (The Team Review card that
  // also consumed this payload's team_review field was removed; the field
  // is left on the response for now.)
  const [workbench, setWorkbench] = useState<WorkbenchResponse | null>(null);
  const [workbenchLoading, setWorkbenchLoading] = useState(true);
  const [workbenchError, setWorkbenchError] = useState<string | null>(null);
  const [sinceDismissed, setSinceDismissed] = useState(false);
  // §27: do NOT advance the "since last visit" cursor merely because the page
  // loaded — that silently discarded changes the user never actually reviewed.
  // We only bootstrap it once on the genuine first visit (nothing to review
  // yet); thereafter it advances only when the user acknowledges the banner.
  const seenBootstrappedRef = useRef(false);
  // Stale-write guard: `reload` is fired imperatively from Refresh, every
  // section's Retry, and the mount effect, so two can overlap. Each run claims
  // the next generation; a run only writes state if it's still the latest,
  // so a slower earlier payload can't land on top of a newer one.
  const reloadGenRef = useRef(0);
  const [sinceSaving, setSinceSaving] = useState(false);
  const [sinceError, setSinceError] = useState<string | null>(null);
  const sinceAsOf = workbench?.since_last_visit.as_of ?? null;
  const dismissSince = useCallback(() => {
    // Acknowledge the snapshot that was DISPLAYED (its `as_of`), not "now":
    // a scan that landed after the page loaded must resurface. The banner
    // only goes once the cursor is saved — a silent failure brought the same
    // changes back on the next visit with no explanation.
    setSinceSaving(true);
    setSinceError(null);
    markWorkbenchSeen(sinceAsOf)
      .then(() => setSinceDismissed(true))
      .catch((err) => setSinceError(formatApiError(err, 'Could not save the acknowledgement. Try again.')))
      .finally(() => setSinceSaving(false));
  }, [sinceAsOf]);

  // v5.243.0 — when each independently fetched source last SUCCEEDED. Several
  // sections keep their previous data on a failed refresh; this is how they
  // say how old it is (components/UpdatedAt).
  const [loadedAt, setLoadedAt] = useState<Partial<Record<LoadedSource, Date>>>({});
  const markLoaded = useCallback((source: LoadedSource) => {
    setLoadedAt((prev) => ({ ...prev, [source]: new Date() }));
  }, []);

  const reload = useCallback(async () => {
    const gen = ++reloadGenRef.current;
    const isStale = () => gen !== reloadGenRef.current;
    setError(null);
    setCoverageLoading(true);
    setPendingLoading(true);
    setStatsLoading(true);
    setWorkbenchLoading(true);
    setWorkbenchError(null);

    // Workbench is independent of the coverage/stats core load — fetch it
    // alongside but isolate its failure so a workbench outage shows the
    // cards' own error state (with Retry) instead of blanking the page.
    getWorkbench()
      .then((wb) => {
        if (isStale()) return;
        setWorkbench(wb);
        markLoaded('workbench');
        // A fresh snapshot is diffed against the saved cursor, so anything it
        // reports arrived after the last acknowledgement — show it again.
        setSinceDismissed(false);
        setSinceError(null);
        // Bootstrap the cursor on the very first visit only (no prior baseline,
        // so nothing to lose) — otherwise leave it until the user acknowledges
        // the banner, so a glance doesn't discard unreviewed changes.
        if (!seenBootstrappedRef.current && wb.since_last_visit.is_first_visit) {
          seenBootstrappedRef.current = true;
          markWorkbenchSeen().catch(() => undefined);
        }
      })
      .catch((err) => {
        if (isStale()) return;
        setWorkbench(null);
        setWorkbenchError(formatApiError(err, 'Could not load your workbench.'));
      })
      .finally(() => {
        if (isStale()) return;
        setWorkbenchLoading(false);
      });

    // RV-10b — settle the core fetches independently. Pre-fix a single
    // Promise.all rejection (e.g. /dashboard/stats) blanked coverage AND
    // pending-plans too. Only coverage is structural (it gates the whole
    // page), so only its failure raises the page-level error; the other
    // sections degrade to their own empty/absent state.
    // (No scan-freshness fetch since 5.255.2: a project is one assessment
    // window, so the age of a scan is not something Operations chases.)
    const [coverageR, pendingR, statsR] = await Promise.allSettled([
      getProjectCoverage(),
      getTestPlans({ status: 'proposed' }),
      getDashboardStats(),
    ]);

    // A newer reload superseded us while these were in flight — drop this
    // payload so it can't overwrite the fresher one.
    if (isStale()) return;

    if (coverageR.status === 'fulfilled') {
      setCoverage(coverageR.value);
      markLoaded('coverage');
    } else {
      setError(formatApiError(coverageR.reason, 'Failed to load Operations data.'));
    }
    // Distinguish "successfully empty" from "unavailable": set each
    // section's error on rejection (and clear it on success) so the render
    // can warn + offer Retry instead of showing a deceptively-empty card.
    if (pendingR.status === 'fulfilled') {
      setPendingPlans(pendingR.value);
      setPendingError(null);
      markLoaded('pending');
    } else {
      setPendingError(formatApiError(pendingR.reason, 'Could not load pending plans.'));
    }
    if (statsR.status === 'fulfilled') {
      setStats(statsR.value);
      setStatsError(null);
      markLoaded('stats');
    } else {
      setStatsError(formatApiError(statsR.reason, 'Could not load project statistics.'));
    }

    setCoverageLoading(false);
    setPendingLoading(false);
    setStatsLoading(false);
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  // The page Refresh: Runs and Recent activity fetch for themselves, so
  // `reload` alone left them showing what they loaded on mount. The key is
  // bumped only here (not inside `reload`, which also runs on mount — that
  // would fetch both panels twice).
  const [refreshKey, setRefreshKey] = useState(0);
  const refreshAll = useCallback(() => {
    setRefreshKey((k) => k + 1);
    reload();
  }, [reload]);

  // FRX·CRIT-2: brand-new projects (no scopes AND no hosts) should
  // see the welcome card alone — the scope toggle, Refresh chrome,
  // and "Project-wide coordination view" subhead just add noise
  // before the operator has done anything.
  const isBrandNewProject =
    !!coverage && coverage.total_hosts === 0 && coverage.total_scopes === 0;

  // Single-scope projects (the setup-card case, since a project has one
  // conceptual scope) open the dialog directly; anything else goes to the
  // recon runs list, which owns the scope picker.
  const handleStartRecon = useCallback(() => {
    const scopes = coverage?.scopes ?? [];
    if (scopes.length === 1) {
      recon.openFor(scopes[0].scope_id, displayScopeName(scopes[0].scope_name));
      return;
    }
    navigate('/recon/runs');
  }, [coverage, recon, navigate]);

  // v4.29.0 — assist-session entry.  Lives on Operations because
  // it's the project-level coordination hub; recon-start lives on
  // Scopes (it's scope-level) and on the setup card above, plan-generate
  // on Test Plans.
  const [assistDialogOpen, setAssistDialogOpen] = useState(false);
  // `?start=agent-session` opens the dialog on arrival — the Agent Sessions
  // page links here as "where a session is started". The param is dropped
  // once read so a refresh or Back does not reopen it.
  const [pageParams, setPageParams] = useSearchParams();
  useEffect(() => {
    if (pageParams.get('start') !== 'agent-session') return;
    setAssistDialogOpen(true);
    const next = new URLSearchParams(pageParams);
    next.delete('start');
    setPageParams(next, { replace: true });
  }, [pageParams, setPageParams]);
  // An active assist session is an outstanding agent key. Surface the count on
  // the entry point so an operator doesn't mint a second one without knowing
  // the first is still live — assist has no one-active-session constraint.
  const {
    sessions: myAssistSessions,
    refresh: refreshAssistSessions,
  } = useMyAssistSessions();

  // Approvals: one block, placed by whether anything is waiting (see the
  // ordering note in the layout below).
  const approvalsWaiting = (pendingPlans?.length ?? 0) > 0;
  const approvalsBlock = (
    <>
      {pendingError && (
        <Alert variant="warning" className="mb-md">
          <AlertDescription className="flex items-center justify-between gap-md">
            <span>{pendingError}</span>
            <Button variant="outline" size="sm" onClick={reload}>
              <RefreshCw className="size-4" aria-hidden /> Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <NeedsAttentionSection
        pendingPlans={pendingPlans}
        loading={pendingLoading}
        canApprove={canApprovePlans}
        updated={<UpdatedAt at={loadedAt.pending ?? null} stale={!!pendingError} />}
        unavailable={!!pendingError}
      />
    </>
  );

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Operations</h1>
          {!isBrandNewProject && (
            <p className="text-metadata text-muted-foreground">
              Project-wide coordination view — coverage, queue, runs.
            </p>
          )}
        </div>
        {!isBrandNewProject && (
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setAssistDialogOpen(true)}
              aria-label={
                myAssistSessions.length > 0
                  ? `Start Agent Session — you have ${myAssistSessions.length} active session${myAssistSessions.length === 1 ? '' : 's'}`
                  : 'Start Agent Session'
              }
            >
              <MessageCircleQuestion className="size-4" aria-hidden />
              Start Agent Session
              {myAssistSessions.length > 0 && (
                <Badge variant="warning" className="ml-xxs">
                  {myAssistSessions.length}
                </Badge>
              )}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={refreshAll}
              disabled={coverageLoading || pendingLoading}
            >
              <RefreshCw
                className={cn(
                  'size-4',
                  (coverageLoading || pendingLoading) && 'animate-spin',
                )}
                aria-hidden
              />{' '}
              Refresh
            </Button>
          </>
        )}
      </div>

      <StartAssistDialog
        open={assistDialogOpen}
        onOpenChange={setAssistDialogOpen}
        mySessions={myAssistSessions}
        onSessionsChanged={refreshAssistSessions}
      />

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {coverage && coverage.total_hosts === 0 && coverage.total_scopes === 0 && (
        <SetupBlock title="Welcome — let's set up this project">
          This project has no scopes or scans yet. Start by registering the network ranges
          you're authorized to assess — everything else (coverage, triage, plans, agentic
          recon) lights up once a scope exists.
          <div className="mt-sm flex flex-wrap gap-sm">
            <Button size="sm" onClick={() => navigate('/scopes')}>Register Your First Scope</Button>
            <Button size="sm" variant="outline" onClick={() => navigate('/scans')}>
              Upload an Existing Scan
            </Button>
          </div>
        </SetupBlock>
      )}

      {coverage && coverage.total_scopes > 0 && coverage.total_hosts === 0 && (
        <SetupBlock title="Scope is registered — time to discover hosts">
          No hosts have been discovered yet. The fastest way to get started is to run{' '}
          <strong className="text-foreground">Agentic Reconnaissance</strong> against your registered scope.
          <div className="mt-sm flex flex-wrap gap-sm">
            {canStartRecon && (
              <Button size="sm" onClick={handleStartRecon}>
                <Rocket className="size-4" aria-hidden /> Start Agentic Recon
              </Button>
            )}
            <Button size="sm" variant="outline" onClick={() => navigate('/scans')}>
              Upload an Existing Scan
            </Button>
          </div>
        </SetupBlock>
      )}
      {/* Opens when recon.scopeId becomes non-null via handleStartRecon. */}
      <StartReconDialog recon={recon} />

      {coverage && coverage.total_hosts > 0 && (
        // v5.267.0 — one column read top to bottom (UI_STYLE_GUIDE §7): a
        // lead sentence, the callouts, then sections over thin rules. Five
        // cards in a two-column grid made every queue the same weight.
        <div className="flex min-w-0 flex-col gap-lg">
          {workbench && !workbenchError && (
            <OperationsLead
              workbench={workbench}
              pendingApprovals={canApprovePlans && !pendingError ? (pendingPlans?.length ?? 0) : 0}
            />
          )}
          {/* Since your last visit — what changed in this project while
              the operator was away (durable per-user cursor, P2). Leads
              the personal section: "what's new?" before "what's mine?". */}
          {workbench && !sinceDismissed && (
            <SinceLastVisitBanner
              since={workbench.since_last_visit}
              onDismiss={dismissSince}
              saving={sinceSaving}
              error={sinceError}
            />
          )}
          {/* v5.241.0 — order follows what the page is FOR: what changed →
              what is blocked on me → my work → runs → the project's state.
              A waiting approval is a blocker and leads; with nothing waiting
              the same block keeps its one-line empty state further down
              instead of pushing My work off the top. */}
          <BlockersStrip
            blockers={workbench?.blockers ?? null}
            unavailable={workbench?.blockers_unavailable ?? false}
          />
          {approvalsWaiting && approvalsBlock}
          {/* Personal surface: the action queue (what needs doing) beside the
              recent-activity feed (what I was just doing). My work, Needs
              another look and Worth a look are personal/engagement queues
              from the single /workbench fetch (P2); the Mine/All toggle
              scopes only Runs. min-w-0: a grid item's default min-width is
              its content, so one unbreakable value would widen the column. */}
          <div className="grid gap-lg lg:grid-cols-3 [&>*]:min-w-0">
            <div className="lg:col-span-2">
              <MyWorkCard
                queue={workbench?.my_queue ?? null}
                tasks={workbench?.my_tasks ?? null}
                notes={workbench?.my_notes ?? null}
                findings={workbench?.my_findings ?? null}
                investigate={workbench?.investigate ?? null}
                investigateUnavailable={workbench?.investigate_unavailable ?? false}
                followups={workbench?.followups ?? null}
                followupsUnavailable={workbench?.followups_unavailable ?? false}
                loading={workbenchLoading}
                error={workbenchError}
                onRetry={reload}
                updated={<UpdatedAt at={loadedAt.workbench ?? null} />}
              />
            </div>
            <MyActivityCard refreshKey={refreshKey} />
          </div>
          {/* Exposure + neglect analytics live on the Posture pages —
              reachable from the nav, not duplicated here. */}
          {!approvalsWaiting && approvalsBlock}
          <RunsSection refreshKey={refreshKey} />
          {statsError && (
            <Alert variant="warning">
              <AlertDescription className="flex items-center justify-between gap-md">
                <span>{statsError}</span>
                <Button variant="outline" size="sm" onClick={reload}>
                  <RefreshCw className="size-4" aria-hidden /> Retry
                </Button>
              </AlertDescription>
            </Alert>
          )}
          <ProjectStateSection
            stats={stats}
            statsLoading={statsLoading}
            coverage={coverage}
            coverageLoading={coverageLoading}
            // Two sources feed this section: it is as old as the OLDER of
            // them, and stale when either refresh failed over data it kept.
            updated={(
              <UpdatedAt
                at={olderOf(loadedAt.stats, loadedAt.coverage)}
                stale={!!statsError || !!error}
              />
            )}
          />
        </div>
      )}
    </div>
  );
};

/** A setup step on a project with nothing in it yet: a left-rule block, not a
 *  centred card (the page keeps its structure, §13). */
const SetupBlock: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div className="max-w-3xl border-l-4 border-l-info py-xs pl-md">
    <h2 className="text-subheading font-semibold text-foreground">{title}</h2>
    <div className="mt-xxs text-metadata text-muted-foreground">{children}</div>
  </div>
);

/**
 * The page's lead (v5.267.0): one sentence saying what is waiting on the
 * operator, from the same /workbench payload the sections below render — so
 * the sentence and the sections cannot disagree.  Blocked work and overdue
 * notes colour it; a queue alone does not (work is the page's normal state).
 */
const OperationsLead: React.FC<{ workbench: WorkbenchResponse; pendingApprovals: number }> = ({
  workbench, pendingApprovals,
}) => {
  const { total, overdue } = personalWorkCounts(
    workbench.my_queue, workbench.my_tasks, workbench.my_notes, workbench.my_findings,
  );
  const b = workbench.blockers;
  const blocked = !workbench.blockers_unavailable && b
    ? b.failed_import_count + b.partial_import_count + b.interrupted_execution_count
    : 0;
  const followups = workbench.followups_unavailable ? 0 : (workbench.followups?.total ?? 0);
  const worth = workbench.investigate_unavailable ? 0 : (workbench.investigate?.queue_total ?? 0);
  const n = (v: number) => v.toLocaleString();

  const parts = [
    blocked > 0 ? `${n(blocked)} blocked item${blocked === 1 ? '' : 's'} to unblock` : null,
    pendingApprovals > 0 ? `${n(pendingApprovals)} plan${pendingApprovals === 1 ? '' : 's'} awaiting your approval` : null,
    total > 0 ? `${n(total)} item${total === 1 ? '' : 's'} in your queue${overdue > 0 ? ` (${n(overdue)} overdue)` : ''}` : null,
    followups > 0 ? `${n(followups)} reviewed host${followups === 1 ? '' : 's'} needing another look` : null,
    worth > 0 ? `${n(worth)} untouched host${worth === 1 ? '' : 's'} worth a look` : null,
  ].filter((p): p is string => !!p);

  const tone: LeadTone = blocked > 0 || overdue > 0 ? 'critical' : pendingApprovals > 0 ? 'warning' : parts.length ? 'neutral' : 'clear';
  const sentence = parts.length === 0
    ? 'Nothing is waiting on you.'
    : parts.length === 1
      ? `${parts[0].charAt(0).toUpperCase()}${parts[0].slice(1)}.`
      : `${parts.slice(0, -1).join(', ').replace(/^./, (c) => c.toUpperCase())} and ${parts[parts.length - 1]}.`;

  return (
    <PostureLead
      tone={tone}
      restsOn="Your queue is notes and plan steps assigned to you, hosts you have in review and findings you own. Needing another look and worth a look are engagement-wide."
    >
      {sentence}
    </PostureLead>
  );
};

export default Operations;
