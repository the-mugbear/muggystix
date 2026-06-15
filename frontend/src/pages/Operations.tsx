import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Info, Loader2, MessageCircleQuestion, RefreshCw, Sparkles, SquareArrowOutUpRight, X } from 'lucide-react';
import StartAssistDialog from '../components/StartAssistDialog';
import {
  AgentSessionRow,
  DashboardStats,
  ProjectCoverageResponse,
  ScopeCoverageRow,
  MyRecentNotesResponse,
  SinceLastVisit,
  StalenessResponse,
  TestPlanSummary,
  WorkbenchResponse,
  getDashboardStats,
  getProjectCoverage,
  getStaleness,
  getTestPlans,
  getWorkbench,
  listAgentSessions,
  markWorkbenchSeen,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { formatApiError } from '../utils/apiErrors';
import MyWorkCard from '../components/MyWorkCard';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import SeverityBar from '../components/ui/SeverityBar';
import { buildHostsUrl } from '../utils/drilldownLinks';
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

// RV-UI — "Security snapshot" (exposure/findings) and "Project coverage"
// (pipeline progress) answered different questions but both described
// overall project state and both led with a redundant Hosts tile.  Merged
// into one "Project state" card with an Exposure row and a Coverage row;
// Hosts now appears once (in Exposure).
const ProjectStateCard: React.FC<{
  stats: DashboardStats | null;
  statsLoading: boolean;
  coverage: ProjectCoverageResponse | null;
  coverageLoading: boolean;
}> = ({ stats, statsLoading, coverage, coverageLoading }) => {
  if ((statsLoading && !stats) || (coverageLoading && !coverage)) {
    return (
      <Card className="mb-md" aria-busy="true">
        <CardContent className="p-md" role="status" aria-live="polite">
          <span className="sr-only">Loading project state…</span>
          <div className="grid grid-cols-2 gap-sm sm:grid-cols-3 lg:grid-cols-6">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-20 rounded-panel bg-muted/40 animate-pulse" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }
  if (!stats && !coverage) return null;

  const vuln = stats?.vulnerability_stats;
  const sevTotal = vuln
    ? vuln.critical + vuln.high + vuln.medium + vuln.low + vuln.info
    : 0;
  const busy = statsLoading || coverageLoading;

  return (
    <Card className="mb-md" aria-busy={busy || undefined}>
      <CardContent className="p-md">
        <h2 className="text-subheading font-semibold">Project state</h2>
        <p className="mb-sm text-caption text-muted-foreground">
          Exposure (vulnerabilities from scanners) and assessment coverage
          (pipeline progress) at a glance. "Findings" (promoted, curated) live on
          the Findings page — these are the raw scanner counts.
        </p>

        {stats && (
          <div className="mb-md">
            <h3 className="mb-xs text-metadata font-semibold text-muted-foreground">Exposure</h3>
            {/* Compact inline counts (a passive total doesn't earn a big tile —
                only the host count navigates). The severity bar below carries
                the actionable per-severity drill-downs. Raw open-ports totals
                were dropped as a vanity metric; the useful scoped form lives on
                Hosts + Scan detail. */}
            <div className="mb-md flex flex-wrap items-center gap-x-sm gap-y-xxs text-metadata">
              <Link to={buildHostsUrl({})}
                className="font-semibold text-foreground hover:text-info hover:underline">
                {stats.total_hosts.toLocaleString()} hosts
              </Link>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button type="button" aria-label="What does the host count include?"
                    className="rounded-full text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                    <Info className="size-3.5" aria-hidden />
                  </button>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs text-left">
                  Total distinct hosts in the project. "marked up" counts only hosts a scanner
                  explicitly tagged host-status "up"; hosts from masscan/naabu/DNS/subnet seeds are
                  often left "unknown" even when reachable — so it's usually far lower than the total
                  and is NOT a liveness count.
                </TooltipContent>
              </Tooltip>
              <span className="text-muted-foreground" aria-hidden>·</span>
              <span className="text-muted-foreground">{stats.up_hosts.toLocaleString()} marked up</span>
              <span className="text-muted-foreground" aria-hidden>·</span>
              <span className="text-muted-foreground">
                {(vuln?.hosts_with_vulnerabilities ?? 0).toLocaleString()} with vulns
              </span>
            </div>

            {vuln && sevTotal > 0 ? (
              <div>
                <div className="mb-xs flex flex-wrap items-baseline justify-between gap-x-md gap-y-xxs">
                  <span className="text-metadata font-medium text-foreground">
                    Share of scanner-detected vulnerabilities
                  </span>
                  <span className="text-caption text-muted-foreground tabular-nums">
                    {(vuln.total_vulnerabilities ?? sevTotal).toLocaleString()} total ·{' '}
                    {(vuln.hosts_with_vulnerabilities ?? 0).toLocaleString()} hosts affected
                  </span>
                </div>
                <SeverityBar
                  variant="summary"
                  counts={vuln}
                  total={sevTotal}
                  ariaLabel="Share of scanner-detected vulnerabilities by severity"
                  // info has no has_info_vulns host param — leave it passive.
                  segmentHref={(sev) => (sev === 'info' ? null : buildHostsUrl({ severity: sev }))}
                />
              </div>
            ) : (
              <p className="text-metadata text-muted-foreground">
                No vulnerabilities detected yet — upload a Nessus or OpenVAS scan to populate this.
              </p>
            )}
          </div>
        )}

        {coverage && (
          <div>
            <h3 className="mb-xs text-metadata font-semibold text-muted-foreground">Coverage</h3>
            <p className="mb-sm text-caption text-muted-foreground">
              Hosts by pipeline stage — the gap counts surface what isn't planned or executed yet.
            </p>
            <div className="mb-sm grid grid-cols-2 gap-sm sm:grid-cols-3">
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
                href={buildHostsUrl({ hasTestExecution: true })}
                subtle={
                  coverage.hosts_no_execution > 0
                    ? `${coverage.hosts_no_execution.toLocaleString()} not yet tested`
                    : 'all hosts tested'
                }
              />
              <CoverageStatTile
                label="Outside scope"
                value={coverage.hosts_outside_scope.toLocaleString()}
                href={coverage.hosts_outside_scope > 0 ? buildHostsUrl({ outOfScopeOnly: true }) : undefined}
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
          </div>
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
          How recent each scope's scan evidence is — scopes whose hosts haven't been seen by a scan
          in over {data.stale_days} days are due for a re-scan. This tracks the age of the data, not
          the scope itself (scope definitions don't change).
        </p>
        <div className="mb-sm flex flex-wrap items-center gap-xs">
          <Badge variant={data.project_is_stale ? 'warning' : 'success'}>
            {data.latest_scan_at ? `Last scan ${data.days_since_last_scan}d ago` : 'No scans yet'}
          </Badge>
          <Badge variant={data.stale_scope_count > 0 ? 'warning' : 'outline'}>
            {data.stale_scope_count} due for re-scan
          </Badge>
        </div>
        {stale.length > 0 && (
          <ul className="flex flex-col gap-xxs">
            {stale.slice(0, 5).map((s) => (
              <li key={s.scope_id} className="flex flex-wrap items-center gap-xs">
                <p className="min-w-0 flex-1 truncate text-metadata">
                  <strong>{displayScopeName(s.scope_name)}</strong>{' '}
                  <span className="text-caption text-muted-foreground">
                    {s.last_activity_at ? `last seen by a scan ${s.days_since}d ago` : 'no hosts discovered'}
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
  /** Drill-down to the records this tile counts (§26) — makes the value a link. */
  href?: string;
}> = ({ label, value, subtle, hint, href }) => (
  <Card>
    <CardContent className="p-md text-center">
      {href ? (
        <Link to={href} aria-label={`${label} — view hosts`}
          className="inline-block text-page-title font-semibold text-foreground hover:text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded">
          {value}
        </Link>
      ) : (
        <p className="text-page-title font-semibold">{value}</p>
      )}
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

// ---------------------------------------------------------------------------
// Needs-attention section
// ---------------------------------------------------------------------------

// Relative "time ago" for the Recent notes strip.
function fmtNoteAgo(iso: string | null): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const mins = Math.floor((Date.now() - t) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}

// "What was I just doing?" — the caller's latest authored notes, separate from
// the My-work action queue. Answers the "latest work" question directly.
const RecentNotesCard: React.FC<{
  notes: MyRecentNotesResponse | null;
  loading: boolean;
}> = ({ notes, loading }) => {
  const navigate = useNavigate();
  const items = notes?.items ?? [];
  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <p className="text-subheading font-semibold text-foreground">Recent notes</p>
        <p className="mb-sm text-caption text-muted-foreground">
          Your latest notes — pick up where you left off.
        </p>
        {loading && !notes ? (
          <div className="flex items-center gap-xs" role="status" aria-live="polite">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Loading…</p>
          </div>
        ) : items.length === 0 ? (
          <p className="text-metadata text-muted-foreground">
            No notes yet — add one from a host to track what you've looked at.
          </p>
        ) : (
          <ul className="flex flex-col">
            {items.map((n) => (
              <li key={n.note_id}>
                <button
                  type="button"
                  onClick={() =>
                    navigate(n.host_id ? `/hosts/${n.host_id}#note-${n.note_id}` : '/operations')
                  }
                  className="flex w-full items-center gap-xs rounded-control px-xs py-xxs text-left hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {n.host_ip && (
                    <span className="shrink-0 font-mono text-metadata font-medium text-foreground">
                      {n.host_ip}
                    </span>
                  )}
                  {n.note_type && n.note_type !== 'observation' && (
                    <Badge variant="secondary">{n.note_type}</Badge>
                  )}
                  <span className="min-w-0 flex-1 truncate text-metadata text-muted-foreground">
                    {n.body_preview || '(no text)'}
                  </span>
                  <span className="shrink-0 text-caption text-muted-foreground">
                    {fmtNoteAgo(n.created_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
};

const NeedsAttentionSection: React.FC<{
  pendingPlans: TestPlanSummary[] | null;
  loading: boolean;
  // Approving a plan needs analyst+; for viewers/auditors this is passive
  // project context, not personal work (§27 role-aware approvals).
  canApprove: boolean;
}> = ({ pendingPlans, loading, canApprove }) => {
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
        <h2 className="text-subheading font-semibold">
          {canApprove ? 'Needs your approval' : 'Pending approvals'}
        </h2>
        <p className="mb-sm text-caption text-muted-foreground">
          {canApprove
            ? 'Agent-drafted test plans awaiting your approve/reject decision. Project-wide — independent of the Mine / All toggle.'
            : 'Agent-drafted test plans awaiting an analyst’s approve/reject decision. Shown for visibility — approving needs the analyst role.'}
        </p>

        {!hasAny && (
          <p className="text-metadata text-muted-foreground">
            {canApprove ? 'Nothing needs your approval right now.' : 'No plans are awaiting approval.'}
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
                    {canApprove ? 'Review' : 'View'}
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

const RunsSection: React.FC = () => {
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
// Since your last visit — durable per-user/project diff (P2).
// ---------------------------------------------------------------------------

const SinceLastVisitBanner: React.FC<{
  since: SinceLastVisit;
  onDismiss: () => void;
}> = ({ since, onDismiss }) => {
  const navigate = useNavigate();

  // First-ever visit would report "everything is new" — noise, not signal.
  // Also nothing to show when the cursor caught up.
  const hasUpdates =
    since.new_scan_count > 0 ||
    since.new_host_count > 0 ||
    since.new_critical_findings > 0 ||
    since.new_high_findings > 0;
  if (since.is_first_visit || !hasUpdates) return null;

  const chips: Array<{ key: string; label: string; tone: 'info' | 'secondary' | 'destructive' | 'warning' }> = [];
  if (since.new_scan_count > 0)
    chips.push({ key: 'scans', tone: 'info', label: `${since.new_scan_count} new scan${since.new_scan_count === 1 ? '' : 's'}` });
  if (since.new_host_count > 0)
    chips.push({ key: 'hosts', tone: 'secondary', label: `${since.new_host_count} new host${since.new_host_count === 1 ? '' : 's'}` });
  if (since.new_critical_findings > 0)
    chips.push({ key: 'crit', tone: 'destructive', label: `${since.new_critical_findings} new critical` });
  if (since.new_high_findings > 0)
    chips.push({ key: 'high', tone: 'warning', label: `${since.new_high_findings} new high` });

  return (
    <Card className="mb-md border-info/40 bg-info/5">
      <CardContent className="flex flex-wrap items-center gap-sm p-md">
        <div className="flex min-w-0 flex-1 items-center gap-xs">
          <Sparkles className="size-4 shrink-0 text-info" aria-hidden />
          <span className="text-metadata font-semibold text-foreground">Since your last visit</span>
          <div className="flex flex-wrap items-center gap-xxs">
            {chips.map((c) => (
              <Badge key={c.key} variant={c.tone}>{c.label}</Badge>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-xs">
          {since.new_scan_count > 0 && (
            <Button size="sm" variant="outline" onClick={() => navigate('/scans')}>
              View scans
              <SquareArrowOutUpRight className="size-3" aria-hidden />
            </Button>
          )}
          <Button size="sm" variant="ghost" aria-label="Dismiss" onClick={onDismiss}>
            <X className="size-4" aria-hidden />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const Operations: React.FC = () => {
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canApprovePlans = hasPermission('analyst');

  const [coverage, setCoverage] = useState<ProjectCoverageResponse | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(true);
  const [pendingPlans, setPendingPlans] = useState<TestPlanSummary[] | null>(null);
  const [pendingLoading, setPendingLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [staleness, setStaleness] = useState<StalenessResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Per-section errors for the non-structural fetches.  Pre-fix a failed
  // stats/pending/staleness request silently degraded to an empty card,
  // which reads as "nothing needs attention" — falsely implying a clean
  // project.  Track each so we can show "unavailable" (with Retry) instead
  // of a deceptively-empty section.  (UX review #8.)
  const [statsError, setStatsError] = useState<string | null>(null);
  const [pendingError, setPendingError] = useState<string | null>(null);
  const [stalenessError, setStalenessError] = useState<string | null>(null);
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
  // Mark the cursor seen exactly once per mount (after the first successful
  // workbench load), so the "since last visit" diff shown this visit is
  // snapshotted before we advance the cursor for next time.
  const seenMarkedRef = useRef(false);

  const reload = useCallback(async () => {
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
        setWorkbench(wb);
        if (!seenMarkedRef.current) {
          seenMarkedRef.current = true;
          // Fire-and-forget: advancing the cursor must not block render,
          // and the banner already has its snapshot from `wb`.
          markWorkbenchSeen().catch(() => undefined);
        }
      })
      .catch((err) => {
        setWorkbench(null);
        setWorkbenchError(formatApiError(err, 'Could not load your workbench.'));
      })
      .finally(() => setWorkbenchLoading(false));

    // RV-10b — settle the core fetches independently. Pre-fix a single
    // Promise.all rejection (e.g. /dashboard/stats) blanked coverage AND
    // pending-plans too. Only coverage is structural (it gates the whole
    // page), so only its failure raises the page-level error; the other
    // sections degrade to their own empty/absent state.
    const [coverageR, pendingR, statsR, stalenessR] = await Promise.allSettled([
      getProjectCoverage(),
      getTestPlans({ status: 'proposed' }),
      getDashboardStats(),
      getStaleness(),
    ]);

    if (coverageR.status === 'fulfilled') {
      setCoverage(coverageR.value);
    } else {
      setError(formatApiError(coverageR.reason, 'Failed to load Operations data.'));
    }
    // Distinguish "successfully empty" from "unavailable": set each
    // section's error on rejection (and clear it on success) so the render
    // can warn + offer Retry instead of showing a deceptively-empty card.
    if (pendingR.status === 'fulfilled') {
      setPendingPlans(pendingR.value);
      setPendingError(null);
    } else {
      setPendingError(formatApiError(pendingR.reason, 'Could not load pending plans.'));
    }
    if (statsR.status === 'fulfilled') {
      setStats(statsR.value);
      setStatsError(null);
    } else {
      setStatsError(formatApiError(statsR.reason, 'Could not load project statistics.'));
    }
    if (stalenessR.status === 'fulfilled') {
      setStaleness(stalenessR.value);
      setStalenessError(null);
    } else {
      setStaleness(null);
      setStalenessError(formatApiError(stalenessR.reason, 'Could not load scan freshness.'));
    }

    setCoverageLoading(false);
    setPendingLoading(false);
    setStatsLoading(false);
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

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
          {/* Since your last visit — what changed in this project while
              the operator was away (durable per-user cursor, P2). Leads
              the personal section: "what's new?" before "what's mine?". */}
          {workbench && !sinceDismissed && (
            <SinceLastVisitBanner
              since={workbench.since_last_visit}
              onDismiss={() => setSinceDismissed(true)}
            />
          )}
          {statsError && (
            <Alert variant="warning" className="mb-md">
              <AlertDescription className="flex items-center justify-between gap-md">
                <span>{statsError}</span>
                <Button variant="outline" size="sm" onClick={reload}>
                  <RefreshCw className="size-4" aria-hidden /> Retry
                </Button>
              </AlertDescription>
            </Alert>
          )}
          <ProjectStateCard
            stats={stats}
            statsLoading={statsLoading}
            coverage={coverage}
            coverageLoading={coverageLoading}
          />
          {stalenessError ? (
            <Alert variant="warning" className="mb-md">
              <AlertDescription className="flex items-center justify-between gap-md">
                <span>{stalenessError}</span>
                <Button variant="outline" size="sm" onClick={reload}>
                  <RefreshCw className="size-4" aria-hidden /> Retry
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            <ScanFreshness data={staleness} />
          )}
          {/* My Queue + My Tasks are personal by definition — the hosts
              YOU marked In Review, the tasks assigned to YOU.  They
              render unconditionally; the Mine/All toggle scopes only
              the Runs section (a runs-view control), not your personal
              widgets.  Pre-fix they were gated behind `scope === mine`,
              so an operator viewing All runs lost sight of their own
              review queue entirely.  Prop-driven from the single
              /workbench fetch (P2). */}
          {/* RV-DESIGN2 — ONE prioritised "My work" list merging host
              investigations (In Review) and the test-plan steps the caller
              owns, so there's a single queue to work rather than two cards
              to reconcile.  Both arrays come from the single /workbench
              fetch; the merge + ranking is in MyWorkCard. */}
          {/* Personal surface: the action queue (what needs doing) beside the
              recent-notes strip (what I was just doing). Two distinct questions,
              two cards — the prior single merged card tried to be both. */}
          <div className="mb-md grid gap-md lg:grid-cols-2">
            <MyWorkCard
              queue={workbench?.my_queue ?? null}
              tasks={workbench?.my_tasks ?? null}
              notes={workbench?.my_notes ?? null}
              findings={workbench?.my_findings ?? null}
              loading={workbenchLoading}
              error={workbenchError}
              onRetry={reload}
            />
            <RecentNotesCard
              notes={workbench?.recent_notes ?? null}
              loading={workbenchLoading}
            />
          </div>
          {/* Exposure + neglect analytics live on the Insights pages (per-subnet
              hygiene + by-site rollup + cross-sectional hotspots) — reachable
              from the nav, not duplicated here. */}
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
          <NeedsAttentionSection pendingPlans={pendingPlans} loading={pendingLoading} canApprove={canApprovePlans} />
          <RunsSection />
        </>
      )}
    </div>
  );
};

export default Operations;
