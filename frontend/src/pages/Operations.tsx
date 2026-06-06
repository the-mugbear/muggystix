import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Info, Loader2, MessageCircleQuestion, RefreshCw, SquareArrowOutUpRight } from 'lucide-react';
import StartAssistDialog from '../components/StartAssistDialog';
import {
  AgentSessionRow,
  DashboardStats,
  ProjectCoverageResponse,
  ScopeCoverageRow,
  StalenessResponse,
  TestPlanSummary,
  getDashboardStats,
  getProjectCoverage,
  getStaleness,
  getTestPlans,
  listAgentSessions,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { formatApiError } from '../utils/apiErrors';
import MyQueueCard from '../components/MyQueueCard';
import MyTasksCard from '../components/MyTasksCard';
import TeamReviewCard from '../components/TeamReviewCard';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { cn } from '../utils/cn';

type ScopeView = 'all' | 'mine';
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

const fmtRelative = (iso?: string | null): string => {
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
    const day = Math.floor(hr / 24);
    return `${day}d ago`;
  } catch {
    return '';
  }
};

type Tone = 'default' | 'success' | 'warning' | 'destructive' | 'info' | 'secondary' | 'muted' | 'outline';

const kindTone = (kind: string): Tone => {
  if (kind === 'recon') return 'secondary';
  if (kind === 'plan_generation') return 'default';
  if (kind === 'execution') return 'success';
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

const SEVERITY_SEGMENTS: Array<{
  key: 'critical' | 'high' | 'medium' | 'low' | 'info';
  label: string;
  color: string;
}> = [
  { key: 'critical', label: 'Critical', color: 'bg-destructive' },
  { key: 'high', label: 'High', color: 'bg-warning' },
  { key: 'medium', label: 'Medium', color: 'bg-info' },
  { key: 'low', label: 'Low', color: 'bg-success' },
  { key: 'info', label: 'Info', color: 'bg-muted-foreground/40' },
];

const SecuritySnapshot: React.FC<{
  stats: DashboardStats | null;
  loading: boolean;
}> = ({ stats, loading }) => {
  if (loading && !stats) {
    return (
      <Card className="mb-md" aria-busy="true">
        <CardContent className="p-md" role="status" aria-live="polite">
          <span className="sr-only">Loading security snapshot…</span>
          <div className="grid grid-cols-2 gap-sm sm:grid-cols-3 lg:grid-cols-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-20 rounded-panel bg-muted/40 animate-pulse" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }
  if (!stats) return null;

  const vuln = stats.vulnerability_stats;
  const sevTotal = vuln
    ? vuln.critical + vuln.high + vuln.medium + vuln.low + vuln.info
    : 0;

  return (
    <Card className="mb-md" aria-busy={loading || undefined}>
      <CardContent className="p-md">
        <h2 className="text-subheading font-semibold">Security snapshot</h2>
        <p className="mb-sm text-caption text-muted-foreground">
          Project-wide totals at a glance. Findings come from vulnerability scanners (Nessus,
          OpenVAS, …).
        </p>

        <div className="mb-sm grid grid-cols-2 gap-sm sm:grid-cols-3 lg:grid-cols-6">
          <CoverageStatTile
            label="Hosts"
            value={stats.total_hosts.toLocaleString()}
            subtle={`${stats.up_hosts.toLocaleString()} marked up`}
            hint={
              'Total distinct hosts in the project. "marked up" counts only hosts a scanner ' +
              'explicitly tagged with host-status "up" (e.g. an nmap host that reported Up). ' +
              'Hosts ingested from masscan lists, naabu, DNS records, or subnet seeds are often ' +
              'left "unknown" even when they have open ports and are clearly reachable — so this ' +
              'number is usually far lower than the host total and is NOT a liveness count. ' +
              'For reachability, look at open ports / per-host detail.'
            }
          />
          <CoverageStatTile label="Open ports" value={stats.open_ports.toLocaleString()} />
          <CoverageStatTile
            label="Hosts with vulns"
            value={(vuln?.hosts_with_vulnerabilities ?? 0).toLocaleString()}
          />
          <CoverageStatTile label="Critical" value={(vuln?.critical ?? 0).toLocaleString()} />
          <CoverageStatTile label="High" value={(vuln?.high ?? 0).toLocaleString()} />
          <CoverageStatTile
            label="Findings"
            value={(vuln?.total_vulnerabilities ?? 0).toLocaleString()}
          />
        </div>

        {vuln && sevTotal > 0 ? (
          <div>
            <div
              className="mb-xs flex h-3 w-full overflow-hidden rounded-full bg-muted/40"
              role="img"
              aria-label="Vulnerability severity distribution"
            >
              {SEVERITY_SEGMENTS.map((seg) => {
                const count = vuln[seg.key];
                if (count <= 0) return null;
                return (
                  <div
                    key={seg.key}
                    className={seg.color}
                    style={{ width: `${(count / sevTotal) * 100}%` }}
                    title={`${seg.label}: ${count.toLocaleString()}`}
                  />
                );
              })}
            </div>
            <div className="flex flex-wrap gap-x-md gap-y-xxs">
              {SEVERITY_SEGMENTS.map((seg) => (
                <span
                  key={seg.key}
                  className="inline-flex items-center gap-xxs text-caption text-muted-foreground"
                >
                  <span
                    className={cn('inline-block size-2 rounded-full', seg.color)}
                    aria-hidden
                  />
                  {seg.label}{' '}
                  <span className="font-medium text-foreground">
                    {vuln[seg.key].toLocaleString()}
                  </span>
                </span>
              ))}
            </div>
          </div>
        ) : (
          <p className="text-metadata text-muted-foreground">
            No vulnerabilities detected yet — upload a Nessus or OpenVAS scan to populate findings.
          </p>
        )}
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Scan freshness — flags scopes/project that need a re-scan (v2.73.0).
// ---------------------------------------------------------------------------

const ScanFreshness: React.FC<{ data: StalenessResponse | null }> = ({ data }) => {
  const navigate = useNavigate();
  if (!data || data.scopes.length === 0) return null;
  const stale = data.scopes.filter((s) => s.is_stale);
  return (
    <Card className="mb-md">
      <CardContent className="p-md">
        <h2 className="text-subheading font-semibold">Scan freshness</h2>
        <p className="mb-sm text-caption text-muted-foreground">
          Scopes whose newest host observation is older than {data.stale_days} days — candidates for
          a re-scan.
        </p>
        <div className="mb-sm flex flex-wrap items-center gap-xs">
          <Badge variant={data.project_is_stale ? 'warning' : 'success'}>
            {data.latest_scan_at ? `Last scan ${data.days_since_last_scan}d ago` : 'No scans yet'}
          </Badge>
          <Badge variant={data.stale_scope_count > 0 ? 'warning' : 'outline'}>
            {data.stale_scope_count} stale scope{data.stale_scope_count === 1 ? '' : 's'}
          </Badge>
        </div>
        {stale.length > 0 && (
          <ul className="flex flex-col gap-xxs">
            {stale.slice(0, 5).map((s) => (
              <li key={s.scope_id} className="flex flex-wrap items-center gap-xs">
                <p className="min-w-0 flex-1 truncate text-metadata">
                  <strong>{displayScopeName(s.scope_name)}</strong>{' '}
                  <span className="text-caption text-muted-foreground">
                    {s.last_activity_at ? `last activity ${s.days_since}d ago` : 'no hosts discovered'}
                  </span>
                </p>
                <Button size="sm" variant="ghost" onClick={() => navigate(`/scopes/${s.scope_id}`)}>
                  Open
                  <SquareArrowOutUpRight className="size-3" aria-hidden />
                </Button>
              </li>
            ))}
            {stale.length > 5 && (
              <p className="text-caption text-muted-foreground">+{stale.length - 5} more</p>
            )}
          </ul>
        )}
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Coverage section
// ---------------------------------------------------------------------------

const CoverageStatTile: React.FC<{
  label: string;
  value: number | string;
  subtle?: string;
  /** Optional explainer rendered behind an info icon next to the label. */
  hint?: string;
}> = ({ label, value, subtle, hint }) => (
  <Card>
    <CardContent className="p-md text-center">
      <p className="text-page-title font-semibold">{value}</p>
      <p className="flex items-center justify-center gap-xxs text-metadata text-muted-foreground">
        {label}
        {hint && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="rounded-full text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={`What does "${label}" count?`}
              >
                <Info className="size-3.5" aria-hidden />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs text-left">{hint}</TooltipContent>
          </Tooltip>
        )}
      </p>
      {subtle && <p className="mt-xxs text-caption text-muted-foreground">{subtle}</p>}
    </CardContent>
  </Card>
);

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
    <div className="mb-xs flex flex-wrap items-baseline gap-x-sm gap-y-xxs">
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
    </div>
  );
};

const CoverageSection: React.FC<{
  coverage: ProjectCoverageResponse | null;
  loading: boolean;
}> = ({ coverage, loading }) => {
  if (loading && !coverage) {
    return (
      <Card className="mb-md" aria-busy="true">
        <CardContent className="p-md" role="status" aria-live="polite">
          <span className="sr-only">Loading project coverage…</span>
          <div className="grid grid-cols-2 gap-sm md:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-20 rounded-panel bg-muted/40 animate-pulse" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }
  if (!coverage) return null;

  return (
    <Card className="mb-md" aria-busy={loading || undefined}>
      <CardContent className="p-md">
        <h2 className="text-subheading font-semibold">Project coverage</h2>
        <p className="mb-sm text-caption text-muted-foreground">
          Hosts discovered in this project, broken down by pipeline stage. The gap counts surface
          what hasn't been planned or executed yet.
        </p>

        <div className="mb-sm grid grid-cols-2 gap-sm sm:grid-cols-4">
          <CoverageStatTile label="Hosts discovered" value={coverage.total_hosts.toLocaleString()} />
          <CoverageStatTile
            label="With plan entries"
            value={coverage.hosts_with_plan_entry.toLocaleString()}
            subtle={
              coverage.hosts_no_plan > 0
                ? `${coverage.hosts_no_plan.toLocaleString()} not yet in any plan`
                : 'all hosts planned'
            }
          />
          <CoverageStatTile
            label="With execution results"
            value={coverage.hosts_with_execution_result.toLocaleString()}
            subtle={
              coverage.hosts_no_execution > 0
                ? `${coverage.hosts_no_execution.toLocaleString()} not yet tested`
                : 'all hosts tested'
            }
          />
          <CoverageStatTile
            label="Outside scope"
            value={coverage.hosts_outside_scope.toLocaleString()}
            subtle={
              coverage.total_scopes === 0
                ? 'no scopes declared'
                : 'discovered but unscoped'
            }
          />
        </div>

        {coverage.scopes.length > 0 && (
          <div>
            <h3 className="mb-xs text-metadata font-semibold">
              Scope coverage ({coverage.total_scopes})
            </h3>
            {coverage.scopes.map((row) => (
              <ScopeCoverageRowDisplay key={row.scope_id} row={row} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Needs-attention section
// ---------------------------------------------------------------------------

const NeedsAttentionSection: React.FC<{
  pendingPlans: TestPlanSummary[] | null;
  loading: boolean;
}> = ({ pendingPlans, loading }) => {
  const navigate = useNavigate();

  if (loading && !pendingPlans) {
    return (
      <Card className="mb-md">
        <CardContent className="p-md" role="status" aria-live="polite">
          <span className="sr-only">Loading attention queue…</span>
          <div className="flex flex-col gap-xs">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-12 rounded-control bg-muted/40 animate-pulse" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const hasAny = (pendingPlans?.length ?? 0) > 0;

  return (
    <Card className="mb-md">
      <CardContent className="p-md">
        <h2 className="text-subheading font-semibold">Needs attention</h2>
        <p className="mb-sm text-caption text-muted-foreground">
          Project-wide queue of items that need a human decision. Independent of the Mine / All
          toggle — exceptions affect everyone.
        </p>

        {!hasAny && (
          <p className="text-metadata text-muted-foreground">
            Nothing currently needs your attention.
          </p>
        )}

        {pendingPlans && pendingPlans.length > 0 && (
          <div>
            <div className="mb-xs flex items-center gap-xs">
              <Badge variant="warning">{pendingPlans.length} pending review</Badge>
              <span className="text-metadata text-muted-foreground">
                Plans the agent drafted; awaiting approval or rejection.
              </span>
            </div>
            <ul className="flex flex-col gap-xxs">
              {pendingPlans.slice(0, 5).map((plan) => (
                <li
                  key={plan.id}
                  className="flex flex-wrap items-center gap-xs"
                >
                  <p className="min-w-0 flex-1 truncate text-metadata">
                    <strong>#{plan.id}</strong> v{plan.version} · {plan.title || '—'}{' '}
                    <span className="text-caption text-muted-foreground">
                      · {plan.entry_count} entr{plan.entry_count === 1 ? 'y' : 'ies'}
                      {plan.generated_by_model && ` · by ${plan.generated_by_model}`}
                    </span>
                  </p>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => navigate(`/test-plans/${plan.id}`)}
                  >
                    Review
                    <SquareArrowOutUpRight className="size-3" aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
            {pendingPlans.length > 5 && (
              <p className="mt-xs text-caption text-muted-foreground">
                + {pendingPlans.length - 5} more — see Test Plans page.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
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
  const subject =
    session.kind === 'recon'
      ? session.scope_id
        ? `Scope #${session.scope_id}`
        : '—'
      : session.kind === 'plan_generation' || session.kind === 'execution'
      ? session.test_plan_id
        ? `Plan #${session.test_plan_id}`
        : '—'
      : '—';

  const handleOpen = () => {
    if (session.kind === 'recon') {
      navigate(`/recon/runs/${session.id}`);
    } else if (session.kind === 'execution') {
      navigate(`/executions/${session.id}`);
    } else if (session.kind === 'plan_generation' && session.test_plan_id) {
      navigate(`/test-plans/${session.test_plan_id}`);
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
      <Button size="sm" variant="ghost" onClick={handleOpen}>
        Open
        <SquareArrowOutUpRight className="size-3" aria-hidden />
      </Button>
    </div>
  );
};

const RunsSection: React.FC<{
  userIdFilter: number | undefined;
}> = ({ userIdFilter }) => {
  const navigate = useNavigate();
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
  }, [statusFilter, userIdFilter, reloadNonce]);

  return (
    <Card className="mb-md">
      <CardContent className="p-md">
        <div className="mb-xs flex flex-wrap items-center gap-xs">
          <h2 className="flex-1 text-subheading font-semibold">Runs</h2>
          <Button size="sm" variant="ghost" onClick={() => navigate('/agent-activity')}>
            Open Agent Runs
            <SquareArrowOutUpRight className="size-3" aria-hidden />
          </Button>
        </div>
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
          <div className="flex flex-col gap-xs">
            {rows.map((row) => (
              <SessionRowDisplay key={`${row.kind}-${row.id}`} session={row} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const Operations: React.FC = () => {
  const { user } = useAuth();
  const navigate = useNavigate();
  // FRX·M2: scopeView is persisted to both the URL (?scope=mine|all)
  // and localStorage so a deep-link survives a hard refresh AND so
  // the toggle stays sticky across navigations.  URL wins on mount;
  // localStorage is the fallback.
  const [searchParams, setSearchParams] = useSearchParams();
  const [scopeView, setScopeView] = useState<ScopeView>(() => {
    const urlScope = searchParams.get('scope');
    if (urlScope === 'mine' || urlScope === 'all') return urlScope;
    return loadStickyScope();
  });

  const [coverage, setCoverage] = useState<ProjectCoverageResponse | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(true);
  const [pendingPlans, setPendingPlans] = useState<TestPlanSummary[] | null>(null);
  const [pendingLoading, setPendingLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [staleness, setStaleness] = useState<StalenessResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const userIdFilter = useMemo(
    () => (scopeView === 'mine' && user ? user.id : undefined),
    [scopeView, user],
  );

  const reload = useCallback(async () => {
    setError(null);
    setCoverageLoading(true);
    setPendingLoading(true);
    setStatsLoading(true);

    try {
      const [coverageResp, pendingResp, statsResp, stalenessResp] = await Promise.all([
        getProjectCoverage(),
        getTestPlans({ status: 'proposed' }),
        getDashboardStats(),
        // Best-effort — staleness must not block the core Operations load.
        getStaleness().catch(() => null),
      ]);
      setCoverage(coverageResp);
      setPendingPlans(pendingResp);
      setStats(statsResp);
      setStaleness(stalenessResp);
    } catch (err) {
      setError(formatApiError(err, 'Failed to load Operations data.'));
    } finally {
      setCoverageLoading(false);
      setPendingLoading(false);
      setStatsLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const handleScopeChange = (next: ScopeView) => {
    setScopeView(next);
    persistScope(next);
    // FRX·M2: mirror to the URL so the operator can share or bookmark
    // a "mine"-filtered view.  Preserve any other params already set.
    const params = new URLSearchParams(searchParams);
    params.set('scope', next);
    setSearchParams(params, { replace: true });
  };

  // FRX·CRIT-2: brand-new projects (no scopes AND no hosts) should
  // see the welcome card alone — the scope toggle, Refresh chrome,
  // and "Project-wide coordination view" subhead just add noise
  // before the operator has done anything.
  const isBrandNewProject =
    !!coverage && coverage.total_hosts === 0 && coverage.total_scopes === 0;

  // v4.29.0 — assist-session entry.  Lives on Operations because
  // it's the project-level coordination hub; recon-start lives on
  // Scopes (it's scope-level), plan-generate on Test Plans.
  const [assistDialogOpen, setAssistDialogOpen] = useState(false);

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
                  scopeView === 'all'
                    ? 'bg-primary text-primary-foreground'
                    : 'hover:bg-accent',
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
                  scopeView === 'mine'
                    ? 'bg-primary text-primary-foreground'
                    : 'hover:bg-accent',
                  !user && 'cursor-not-allowed opacity-50',
                )}
              >
                Mine
              </button>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setAssistDialogOpen(true)}
            >
              <MessageCircleQuestion className="size-4" aria-hidden />
              AI Assist
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={reload}
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
      />

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {coverage && coverage.total_hosts === 0 && coverage.total_scopes === 0 && (
        <Card className="mx-auto max-w-3xl">
          <CardContent className="p-xl text-center">
            <h2 className="mb-sm text-page-title font-semibold">
              Welcome — let's set up this project
            </h2>
            <p className="mb-md text-metadata text-muted-foreground">
              This project has no scopes or scans yet. Start by registering the network ranges
              you're authorized to assess — everything else (coverage, triage, plans, agentic
              recon) lights up once a scope exists.
            </p>
            <div className="flex flex-wrap justify-center gap-sm">
              <Button onClick={() => navigate('/scopes')}>Register Your First Scope</Button>
              <Button variant="outline" onClick={() => navigate('/scans')}>
                Upload an Existing Scan
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {coverage && coverage.total_scopes > 0 && coverage.total_hosts === 0 && (
        <Card className="mx-auto mb-md max-w-3xl">
          <CardContent className="p-xl text-center">
            <h2 className="mb-sm text-page-title font-semibold">
              Scope is registered — time to discover hosts
            </h2>
            <p className="mb-md text-metadata text-muted-foreground">
              No hosts have been discovered yet. The fastest way to get started is to run{' '}
              <strong>Agentic Reconnaissance</strong> against your registered scope.
            </p>
            <div className="flex flex-wrap justify-center gap-sm">
              <Button onClick={() => navigate('/scopes')}>Start Agentic Recon</Button>
              <Button variant="outline" onClick={() => navigate('/scans')}>
                Upload an Existing Scan
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {coverage && coverage.total_hosts > 0 && (
        <>
          {/* Security snapshot leads the page — the project-level "what
              do we have and how exposed is it?" headline numbers, above
              the personal queue widgets. */}
          <SecuritySnapshot stats={stats} loading={statsLoading} />
          <ScanFreshness data={staleness} />
          {/* My Queue + My Tasks are personal by definition — the hosts
              YOU marked In Review, the tasks assigned to YOU.  They
              render unconditionally; the Mine/All toggle scopes only
              the Runs section (a runs-view control), not your personal
              widgets.  Pre-fix they were gated behind `scope === mine`,
              so an operator viewing All runs lost sight of their own
              review queue entirely. */}
          <div className="mb-md grid grid-cols-1 gap-sm lg:grid-cols-12">
            <div className="lg:col-span-7">
              <MyQueueCard />
            </div>
            <div className="lg:col-span-5">
              <MyTasksCard />
            </div>
          </div>
          {/* Team Review — the project-wide review roster (who has
              which hosts In Review), so operators can plan coverage
              and avoid two people working the same host. */}
          <div className="mb-md">
            <TeamReviewCard />
          </div>
          <CoverageSection coverage={coverage} loading={coverageLoading} />
          <NeedsAttentionSection pendingPlans={pendingPlans} loading={pendingLoading} />
          <RunsSection userIdFilter={userIdFilter} />
        </>
      )}
    </div>
  );
};

export default Operations;
