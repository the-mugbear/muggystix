import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Loader2, MessageCircleQuestion, RefreshCw, Sparkles } from 'lucide-react';
import StartAssistDialog from '../components/StartAssistDialog';
import {
  AgentSessionRow,
  DashboardStats,
  OperationsBlockers,
  ProjectCoverageResponse,
  SinceLastVisit,
  WorkbenchResponse,
  InvestigationQueueResponse,
  getDashboardStats,
  getProjectCoverage,
  getWorkbench,
  getInvestigationQueue,
  listAgentSessions,
  markWorkbenchSeen,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useProject } from '../contexts/ProjectContext';
import { projectRoleAtLeast } from '../utils/projectRole';
import { useCanStartAgentSession } from '../hooks/useCanStartAgentSession';
import { formatApiError } from '../utils/apiErrors';
import AgentTaskButton from '../components/agent-sessions/AgentTaskButton';
import MyWorkCard, { personalWorkCounts } from '../components/MyWorkCard';
import MyActivityCard from '../components/MyActivityCard';
import AddressTerrainSection from '../components/operations/AddressTerrainSection';
import UpdatedAt from '../components/UpdatedAt';
import RunKindBadge from '../components/RunKindBadge';
import LastUpdated from '../components/LastUpdated';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import PostureSection from '../components/posture/PostureSection';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import SeverityBar from '../components/ui/SeverityBar';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { sinceChips, type SinceChip } from '../utils/sinceLastVisit';
import { filenameSummary } from '../utils/filenameSummary';
import { agentInstruction, isStalledRun, sessionRowPath } from '../utils/agentRuns';
import { cn } from '../utils/cn';
import { useMyAssistSessions } from '../hooks/useMyAssistSessions';
import { formatRelativeTime } from '../utils/relativeTime';

type ScopeView = 'all' | 'mine';
/** The older of two load times; null until both have loaded. */
const olderOf = (a?: Date, b?: Date): Date | null =>
  (a && b ? (a.getTime() <= b.getTime() ? a : b) : null);

/** The page's independently fetched sources, each with its own load time. */
type LoadedSource = 'workbench' | 'coverage' | 'stats';
/** The oldest load on the page; null until something has loaded. */
const oldestLoad = (loaded: Partial<Record<LoadedSource, Date>>): Date | null => {
  const times = Object.values(loaded).filter((d): d is Date => d instanceof Date);
  return times.length ? times.reduce((a, b) => (a.getTime() <= b.getTime() ? a : b)) : null;
};
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
              <span className="text-muted-foreground" aria-hidden>·</span>
              {/* 5.304.0 — "marked up" was jargon; each count opens its list. */}
              <Link to={buildHostsUrl({ q: 'state:up' })}
                className="text-muted-foreground hover:text-info hover:underline">
                {stats.up_hosts.toLocaleString()} reported up
              </Link>
              <InfoTip text={'Hosts a scanner reported as answering (host state "up"). masscan, naabu, DNS and scope seeds do not report host state, so their hosts stay "unknown" even when reachable — this is not a liveness count.'} />
              <span className="text-muted-foreground" aria-hidden>·</span>
              {/* Every kind, informational included — the count is every host
                  with any scanner row. */}
              <Link to={buildHostsUrl({ q: 'kind:vulnerability,misconfiguration,informational' })}
                className="text-muted-foreground hover:text-info hover:underline">
                {(vuln?.hosts_with_vulnerabilities ?? 0).toLocaleString()} with scanner observations
              </Link>
            </div>

            {vuln && actionableTotal > 0 ? (
              <div className="max-w-3xl">
                <div className="mb-xs flex flex-wrap items-baseline justify-between gap-x-md gap-y-xxs">
                  <span className="inline-flex items-center gap-xxs text-caption text-muted-foreground">
                    Scanner observations by severity
                    <InfoTip text="One per scanner check per port, as imported — not yet judged, and a host usually carries several. Each severity opens the hosts carrying at least one observation of it; that host count is under the number." />
                  </span>
                  <span className="text-caption text-muted-foreground tabular-nums">
                    {actionableTotal.toLocaleString()} observations, informational excluded
                  </span>
                </div>
                <SeverityBar
                  variant="summary"
                  counts={vuln}
                  total={actionableTotal}
                  ariaLabel="Scanner observations by severity"
                  // SeverityBar never renders info, but the callback is typed
                  // over all severities — guard so the type narrows to HostSeverity.
                  segmentHref={(sev) => (sev === 'info' ? null : buildHostsUrl({ severity: sev }))}
                  // 5.304.0 — the counts are observations, the links hosts:
                  // "154 High" opened 122 hosts unannounced.  The link says so.
                  linkLabel={(sev) => {
                    const n = vuln.hosts_by_severity?.[sev];
                    return n == null ? null : `${n.toLocaleString()} host${n === 1 ? '' : 's'}`;
                  }}
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
                label="With tests to do"
                info="Hosts with at least one test that is proposed or in progress. A host whose tests are all done or dismissed is not counted. The line below opens the hosts with none."
                value={coverage.hosts_with_plan_entry.toLocaleString()}
                to={coverage.hosts_with_plan_entry > 0 ? buildHostsUrl({ q: 'has:planned' }) : undefined}
                toLabel="With tests to do — view hosts"
              >
                <GapLine
                  text={coverage.hosts_no_plan > 0
                    ? `${coverage.hosts_no_plan.toLocaleString()} with no test to do`
                    : 'every host has a test to do'}
                  to={coverage.hosts_no_plan > 0 ? buildHostsUrl({ q: 'NOT has:planned' }) : undefined}
                />
              </PostureMeasure>
              <PostureMeasure
                label="Tested"
                info="Hosts with recorded evidence of a test that ran — a finding, no finding, or an inconclusive result. A proposed test alone does not count, nor does an attempt that could not run. The line below opens the hosts with none."
                value={coverage.hosts_with_execution_result.toLocaleString()}
                to={buildHostsUrl({ hasTestExecution: true })}
                toLabel="Tested — view hosts"
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
                info="Hosts in no scope subnet and not reached through an in-scope name. Discovered, but nobody approved testing them — confirm they are in scope before acting on them."
                value={coverage.hosts_outside_scope.toLocaleString()}
                to={coverage.hosts_outside_scope > 0 ? buildHostsUrl({ outOfScopeOnly: true }) : undefined}
                toLabel="Outside scope — view hosts"
              >
                {coverage.total_scopes === 0 ? 'no scopes declared' : 'discovered but unscoped'}
              </PostureMeasure>
            </div>

            {/* 5.304.0 — the three coverage states, adding up to every host.
                It was a list by scope NAME ("Demo scope … 400 hosts
                discovered") beside "18 outside", and nothing said where the
                419th host was: reached only through an in-scope name. */}
            {coverage.total_scopes > 0 && (
              <p className="mt-md flex flex-wrap items-center gap-x-xs gap-y-xxs text-metadata">
                <span className="font-semibold text-foreground">Scope</span>
                <InfoTip text="Every host is in exactly one of these: inside a scope subnet; in no subnet but reached through an in-scope name (the name was approved, not the address); or outside scope." />
                <ScopeStateLink n={coverage.hosts_in_subnet_scope} q="scope:subnet" label="in scope subnets" />
                <span className="text-muted-foreground" aria-hidden>·</span>
                <ScopeStateLink n={coverage.hosts_name_scope_only} q="scope:name" label="reached only through an in-scope name" />
                <span className="text-muted-foreground" aria-hidden>·</span>
                <ScopeStateLink n={coverage.hosts_outside_scope} q="scope:none" label="outside scope" />
                <span className="text-caption text-muted-foreground">
                  — {scopedSubnets(coverage).toLocaleString()} subnet{scopedSubnets(coverage) === 1 ? '' : 's'},{' '}
                  {scopedAddresses(coverage).toLocaleString()} addresses
                </span>
              </p>
            )}
          </div>
        )}
      </div>
    </PostureSection>
  );
};

/** A coverage gap: a link when it has hosts to open, plain text otherwise.
 *  5.304.0 — a plain link, not warning colour: "417 not yet in any plan" in
 *  amber on a project that has barely started planning flagged nearly every
 *  host, and a warning that is always on stops meaning anything. */
const GapLine: React.FC<{ text: string; to?: string }> = ({ text, to }) => (to ? (
  <Link to={to} className="rounded text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring">
    {text}
  </Link>
) : <>{text}</>);

// The scope's size, summed over its subnets — never a "% discovered" (v4.18.0:
// a /24 with every live host found read "22 / 256 — 8.59%", a success shown
// as failure).  Scope names are not shown (5.304.0): they are a relic.
const scopedSubnets = (c: ProjectCoverageResponse) => c.scopes.reduce((n, s) => n + s.subnet_count, 0);
const scopedAddresses = (c: ProjectCoverageResponse) => c.scopes.reduce((n, s) => n + s.total_scoped_ips, 0);

/** One coverage state: its count opens exactly its hosts (`scope:` DSL). */
const ScopeStateLink: React.FC<{ n: number | undefined; q: string; label: string }> = ({ n, q, label }) => {
  const count = n ?? 0;
  return count > 0 ? (
    <Link to={buildHostsUrl({ q })} className="text-info hover:underline">
      <strong className="tabular-nums">{count.toLocaleString()}</strong> {label}
    </Link>
  ) : (
    <span className="text-muted-foreground"><span className="tabular-nums">0</span> {label}</span>
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

/** A small switch of mutually exclusive options (the Scans "Grouped / All
 *  files" shape) — both Runs controls use it, so they read as one row. */
function Segmented<T extends string>({ label, value, onChange, options }: {
  label: string;
  value: T;
  onChange: (next: T) => void;
  options: Array<{ value: T; label: string; disabled?: boolean }>;
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-control border border-border" role="group" aria-label={label}>
      {options.map((opt, i) => (
        <button
          key={opt.value}
          type="button"
          aria-pressed={value === opt.value}
          disabled={opt.disabled}
          onClick={() => onChange(opt.value)}
          className={cn(
            'px-sm py-xxs text-metadata transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            i > 0 && 'border-l border-border',
            value === opt.value ? 'bg-primary text-primary-foreground' : 'hover:bg-accent',
            opt.disabled && 'cursor-not-allowed opacity-50',
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

const SessionRowDisplay: React.FC<{ session: AgentSessionRow }> = ({ session }) => {
  const navigate = useNavigate();
  // A session says what it is for in the operator's own words; a legacy
  // assist row is project-wide and has nothing to name.
  const subject = session.kind === 'project'
    ? session.purpose || 'Project session'
    : 'Project-wide';

  // One rule for where a row opens (utils/agentRuns); a legacy row with no
  // page offers no Open.
  const openPath = sessionRowPath(session);

  // 5.304.0 — "active" on a legacy row no agent session can act on is
  // stalled: Agent Sessions' rule, not an age.
  const stalled = isStalledRun(session);

  return (
    <div className="flex flex-wrap items-center gap-xs">
      {/* Sentence case (5.304.0): two upper-case pills per row shouted. */}
      <RunKindBadge kind={session.kind} className="normal-case tracking-normal" />
      <Badge
        variant={stalled ? 'warning' : session.status === 'active' ? 'success' : 'muted'}
        className="normal-case tracking-normal"
        title={stalled
          ? 'Still open, but no agent session can act on it — it will not move on its own.'
          : undefined}
      >
        {stalled ? 'Stalled' : session.status.replace('_', ' ')}
      </Badge>
      <p className="min-w-0 flex-1 truncate text-metadata">
        <strong>#{session.id}</strong> · {subject}{' '}
        <span className="text-caption text-muted-foreground">
          {session.user_username && `by ${session.user_username}`}
          {session.generated_by_model && ` · ${session.generated_by_model}`}
          {session.started_at && ` · ${fmtRelative(session.started_at)}`}
        </span>
      </p>
      {openPath && (
        <Button size="sm" variant="ghost" className="h-7 text-info" onClick={() => navigate(openPath)}>
          Open
        </Button>
      )}
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
      description="Agent sessions on this project."
      actions={<>
          {/* A failed refetch keeps the previous rows under its error. */}
          <UpdatedAt at={runsLoadedAt} stale={!!error} hideWhenFresh />
          <Button size="sm" variant="ghost" className="h-7 text-info" onClick={() => navigate('/agent-activity')}>
            Open Agent Sessions
          </Button>
      </>}
    >
        {/* v5.294.0 (UX review) — ONE control row: whose runs and which
            status, as two switches of the same shape. It was upper-case
            status chips under the heading and an All/Mine toggle beside it. */}
        <div className="mb-sm flex flex-wrap items-center gap-sm">
          <Segmented
            label="Runs status filter"
            value={statusFilter}
            onChange={setStatusFilter}
            options={RUNS_STATUS_OPTIONS}
          />
          <Segmented
            label="Scope of runs view"
            value={scopeView}
            onChange={handleScopeChange}
            options={[
              { value: 'all', label: 'Everyone' },
              { value: 'mine', label: 'Mine', disabled: !user },
            ]}
          />
          {/* Says what is listed: "Last 10 runs" over six rows read as ten. */}
          <span className="ml-auto text-caption text-muted-foreground">
            {rows && rows.length < (statusFilter === 'all' ? 10 : 50)
              ? `All ${rows.length} ${statusFilter === 'all' ? '' : `${statusFilter} `}run${rows.length === 1 ? '' : 's'}`
              : statusFilter === 'all'
                ? 'The 10 most recent runs, of every kind'
                : `The 50 most recent ${statusFilter} runs`}
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

/** A change's tone as text colour: severity keeps its colour, the rest read
 *  as ordinary links. */
const SINCE_TONE_TEXT: Record<SinceChip['tone'], string> = {
  info: 'text-info',
  secondary: 'text-info',
  destructive: 'font-medium text-sev-critical',
  warning: 'font-medium text-sev-high',
};

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
          {/* 5.304.0 — plain in-app links in sentence case, separated by
              dots.  They were upper-case filled pills (a pink "3 NEW HOSTS")
              with an external-link icon on links that never leave the app. */}
          <div className="flex min-w-0 flex-wrap items-center gap-x-xs gap-y-xxs text-metadata">
            {chips.map((c, i) => (
              <React.Fragment key={c.key}>
                {i > 0 && <span className="text-muted-foreground" aria-hidden>·</span>}
                {c.href ? (
                  <Link to={c.href} title={c.hint} aria-label={`${c.label} — ${c.hint}`}
                    className={cn(
                      'rounded underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                      SINCE_TONE_TEXT[c.tone],
                    )}>
                    {c.label}
                  </Link>
                ) : (
                  <span title={c.hint} className={SINCE_TONE_TEXT[c.tone]}>{c.label}</span>
                )}
              </React.Fragment>
            ))}
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
// imports that failed or finished partial (interrupted execution runs were a
// second kind until 5.320.0).  Each says what is blocked and carries the one
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
        Blocked work (failed imports) could not be checked — this is not a
        confirmation that nothing is blocked.
      </p>
    );
  }
  if (!blockers) return null;
  const importCount = blockers.failed_import_count + blockers.partial_import_count;
  if (importCount === 0) return null;

  const importSummary = [
    blockers.failed_import_count > 0
      ? `${blockers.failed_import_count} import${blockers.failed_import_count === 1 ? '' : 's'} failed`
      : null,
    blockers.partial_import_count > 0
      ? `${blockers.partial_import_count} finished partial`
      : null,
  ].filter(Boolean).join(' · ');

  return (
    <div className="space-y-xs border-l-4 border-l-warning py-xs pl-md">
        <div className="flex items-center gap-xs">
          <AlertTriangle className="size-4 shrink-0 text-warning" aria-hidden />
          <h2 className="text-metadata font-semibold text-foreground">Blocked</h2>
          <span className="text-caption text-muted-foreground"
            title="Failed or partial imports nobody has dismissed. Nothing here moves until someone acts.">
            waiting on someone — each line has the action that unblocks it
          </span>
        </div>

        {importCount > 0 && (
          <div className="flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs">
            <span className="shrink-0 text-metadata text-foreground">{importSummary}</span>
            <span
              className="min-w-0 flex-1 truncate text-caption text-muted-foreground"
              title={blockers.imports.map((i) => `${i.filename}${i.message ? ` — ${i.message}` : ''}`).join('\n')}
            >
              {filenameSummary(blockers.imports.map((i) => i.filename))}
              {importCount > blockers.imports.length && ` and ${importCount - blockers.imports.length} more`}
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

    </div>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const Operations: React.FC = () => {
  const navigate = useNavigate();
  const { currentProject } = useProject();
  // The "scope registered, no hosts" setup card hands the operator's agent a
  // scan task (5.313.0: through the one agent session — no per-scope key).
  // Scanning uploads scans, so PROJECT analyst+ (5.313.1: it read the global
  // role, which is binary — every member passed).  A project whose role has
  // not loaded leaves the decision to the server, as canStartAgentSession does.
  const canStartScan = currentProject?.my_role === undefined
    || projectRoleAtLeast(currentProject.my_role, 'analyst');

  const [coverage, setCoverage] = useState<ProjectCoverageResponse | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-section errors for the non-structural fetches.  Pre-fix a failed
  // stats request silently degraded to an empty card,
  // which reads as "nothing needs attention" — falsely implying a clean
  // project.  Track each so we can show "unavailable" (with Retry) instead
  // of a deceptively-empty section.  (UX review #8.)
  const [statsError, setStatsError] = useState<string | null>(null);
  // P2 — Operations owns ONE /workbench fetch covering the personal cards
  // (My Queue / My Tasks) + the since-last-visit diff, and prop-drives them.
  // The page-level Refresh re-runs this in lockstep with the coverage/stats
  // fetches, so everything refreshes together.  (The Team Review card that
  // also consumed this payload's team_review field was removed; the field
  // is left on the response for now.)
  const [workbench, setWorkbench] = useState<WorkbenchResponse | null>(null);
  const [workbenchLoading, setWorkbenchLoading] = useState(true);
  // v5.304.1 — "Worth a look" loads on its own request: on a large project it
  // was most of the workbench's time, and My work waited for it.
  const [investigate, setInvestigate] = useState<InvestigationQueueResponse | null>(null);
  const [investigateLoading, setInvestigateLoading] = useState(true);
  const [investigateUnavailable, setInvestigateUnavailable] = useState(false);
  const investigateGenRef = useRef(0);
  // v5.308.0 — the tier the queue is narrowed to (null = every tier). A ref
  // too, so a refresh or an action reloads the tier being looked at.
  const [investigateTier, setInvestigateTier] = useState<number | null>(null);
  const investigateTierRef = useRef<number | null>(null);
  // `quiet`: after an action in a queue — keep what is shown until the new
  // queue arrives instead of flashing the loading line.
  const loadInvestigate = useCallback((quiet = false) => {
    const gen = ++investigateGenRef.current;
    if (!quiet) setInvestigateLoading(true);
    getInvestigationQueue(investigateTierRef.current)
      .then((q) => {
        if (gen !== investigateGenRef.current) return;
        setInvestigate(q);
        setInvestigateUnavailable(false);
      })
      .catch(() => {
        if (gen !== investigateGenRef.current) return;
        setInvestigate(null);
        setInvestigateUnavailable(true);
      })
      .finally(() => {
        if (gen !== investigateGenRef.current) return;
        setInvestigateLoading(false);
      });
  }, []);
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
    setStatsLoading(true);
    setWorkbenchLoading(true);
    setWorkbenchError(null);

    // Workbench is independent of the coverage/stats core load — fetch it
    // alongside but isolate its failure so a workbench outage shows the
    // cards' own error state (with Retry) instead of blanking the page.
    loadInvestigate();
    getWorkbench({ includeInvestigate: false })
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
    // Promise.all rejection (e.g. /dashboard/stats) blanked coverage too.
    // Only coverage is structural (it gates the whole page), so only its
    // failure raises the page-level error; stats degrade on their own.
    // (No scan-freshness fetch since 5.255.2: a project is one assessment
    // window, so the age of a scan is not something Operations chases.)
    const [coverageR, statsR] = await Promise.allSettled([
      getProjectCoverage(),
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
    if (statsR.status === 'fulfilled') {
      setStats(statsR.value);
      setStatsError(null);
      markLoaded('stats');
    } else {
      setStatsError(formatApiError(statsR.reason, 'Could not load project statistics.'));
    }

    setCoverageLoading(false);
    setStatsLoading(false);
  }, [loadInvestigate]);

  useEffect(() => {
    reload();
  }, [reload]);

  // After an action in a queue (take into review, re-open, claim, undo): the
  // workbench only, without spinners (5.304.0).  The full `reload` blanked
  // every section, collapsed the expanded lists and moved the page under
  // the pointer after each click.
  const refreshWorkbenchQuietly = useCallback(() => {
    getWorkbench({ includeInvestigate: false })
      .then((wb) => {
        setWorkbench(wb);
        markLoaded('workbench');
      })
      .catch(() => { /* the next full refresh reports it */ });
    // Taking a host into review moves it out of "Worth a look".
    loadInvestigate(true);
  }, [loadInvestigate]);

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

  // v4.29.0 — the agent-session entry.  Lives on Operations because it's the
  // project-level coordination hub.  Since 5.313.0 it is the ONLY way an agent
  // starts: the per-object buttons (a scope's scan, a plan's work) open the
  // same dialog with a task for the agent (AgentTaskButton).
  const [assistDialogOpen, setAssistDialogOpen] = useState(false);
  // `POST /assist/start` needs project auditor — a viewer is offered no entry
  // (nor the `?start=` deep link, which then just drops its param).
  const canStartAgent = useCanStartAgentSession();
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

  const workCardProps = {
    queue: workbench?.my_queue ?? null,
    tasks: workbench?.my_tasks ?? null,
    findings: workbench?.my_findings ?? null,
    investigate,
    investigateUnavailable,
    investigateLoading,
    onRetryInvestigate: () => loadInvestigate(),
    investigateTier,
    onInvestigateTier: (tier: number | null) => {
      investigateTierRef.current = tier;
      setInvestigateTier(tier);
      loadInvestigate(true);
    },
    followups: workbench?.followups ?? null,
    followupsUnavailable: workbench?.followups_unavailable ?? false,
    loading: workbenchLoading,
    error: workbenchError,
    onRetry: reload,
    onChanged: refreshWorkbenchQuietly,
  };

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Operations</h1>
          {!isBrandNewProject && (
            <p className="text-metadata text-muted-foreground">
              Project-wide coordination view — coverage, queue, agent sessions.
            </p>
          )}
        </div>
        {!isBrandNewProject && (
          <>
            {canStartAgent && (
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
            )}
            {/* One freshness control for the page (v5.294.0): the OLDEST
                load on it, and one refresh that reaches every section. */}
            <LastUpdated
              compact
              lastFetched={oldestLoad(loadedAt)}
              onRefresh={refreshAll}
              isLoading={coverageLoading}
              label="Operations"
            />
          </>
        )}
      </div>

      <StartAssistDialog
        open={canStartAgent && assistDialogOpen}
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
          you're authorized to assess — everything else (coverage, triage, plans, agent
          scanning) lights up once a scope exists.
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
          No hosts have been discovered yet. The fastest way to get started is to have your
          agent <strong className="text-foreground">scan</strong> your registered scope and
          upload the output, or to upload a scan you already have.
          <div className="mt-sm flex flex-wrap gap-sm">
            {canStartScan && (
              <AgentTaskButton
                variant="default"
                label="Scan with your agent"
                instruction={agentInstruction.scanScope(
                  coverage.scopes.length === 1 ? coverage.scopes[0].scope_id : undefined,
                )}
              />
            )}
            <Button size="sm" variant="outline" onClick={() => navigate('/scans')}>
              Upload an Existing Scan
            </Button>
          </div>
        </SetupBlock>
      )}

      {coverage && coverage.total_hosts > 0 && (
        // v5.267.0 — one column read top to bottom (UI_STYLE_GUIDE §7): a
        // lead sentence, the callouts, then sections over thin rules. Five
        // cards in a two-column grid made every queue the same weight.
        <div className="flex min-w-0 flex-col gap-lg">
          {workbench && !workbenchError && (
            <OperationsLead
              workbench={workbench}
              worthALook={investigateUnavailable ? 0 : (investigate?.queue_total ?? 0)}
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
              what is blocked → my work → runs → the project's state. */}
          <BlockersStrip
            blockers={workbench?.blockers ?? null}
            unavailable={workbench?.blockers_unavailable ?? false}
          />
          {/* Personal surface: the action queue (what needs doing) beside the
              recent-activity feed (what I was just doing). My work, Needs
              another look and Worth a look are personal/engagement queues
              from the single /workbench fetch (P2); the Mine/All toggle
              scopes only Runs. min-w-0: a grid item's default min-width is
              its content, so one unbreakable value would widen the column. */}
          {/* 5.304.0 — only My work shares a row with the activity feed; the
              two engagement-wide queues run the full width below.  In the
              2/3 column they left ~1500px of empty page beside them once the
              feed (eight rows) ended. */}
          <div className="grid gap-lg lg:grid-cols-3 [&>*]:min-w-0">
            <div className="lg:col-span-2">
              <MyWorkCard
                part="mine"
                {...workCardProps}
                updated={<UpdatedAt at={loadedAt.workbench ?? null} hideWhenFresh />}
              />
            </div>
            <MyActivityCard refreshKey={refreshKey} />
          </div>
          <MyWorkCard part="engagement" {...workCardProps} />
          {/* 5.306.0 — the same team-wide question as the queues above
              ("who has been where?") as ground: one request, loaded when the
              section nears the viewport, three.js in its own chunk. */}
          <AddressTerrainSection refreshKey={refreshKey} />
          {/* Exposure + neglect analytics live on the Posture pages —
              reachable from the nav, not duplicated here. */}
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
                hideWhenFresh
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
 * the sentence and the sections cannot disagree.  Blocked work colours it; a queue alone does not (work is the page's normal state).
 */
const OperationsLead: React.FC<{ workbench: WorkbenchResponse; worthALook: number }> = ({
  workbench, worthALook,
}) => {
  const { total } = personalWorkCounts(workbench.my_queue, workbench.my_tasks, workbench.my_findings);
  const b = workbench.blockers;
  const known = !workbench.blockers_unavailable && b;
  const failed = known ? b.failed_import_count : 0;
  const partial = known ? b.partial_import_count : 0;
  const blocked = failed + partial;
  const followups = workbench.followups_unavailable ? 0 : (workbench.followups?.total ?? 0);
  // Loaded on its own request (5.304.1): 0 until it arrives, so the clause
  // appears when the count is known rather than as a guess.
  const worth = worthALook;
  const n = (v: number) => v.toLocaleString();
  const s = (v: number, one: string, many: string) => `${n(v)} ${v === 1 ? one : many}`;

  // 5.304.0 — two sentences, yours and the team's, each thing named for what
  // it is.  One run-on sentence ("14 blocked items to unblock, 58 items in
  // your queue, 29 reviewed hosts … and 114 untouched hosts …") mixed the
  // two and needed a second line to explain which was which.
  const mine = [
    total > 0 ? `${s(total, 'item', 'items')} in your queue` : null,
  ].filter((p): p is string => !!p);
  const team = [
    failed > 0 ? `${s(failed, 'import', 'imports')} failed` : null,
    partial > 0 ? `${s(partial, 'import', 'imports')} finished partial` : null,
    followups > 0 ? `${s(followups, 'reviewed host needs', 'reviewed hosts need')} another look` : null,
    worth > 0 ? `${s(worth, 'unreviewed host is', 'unreviewed hosts are')} worth a look` : null,
  ].filter((p): p is string => !!p);

  const list = (parts: string[]) => (parts.length <= 1
    ? parts.join('')
    : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`);
  const tone: LeadTone = blocked > 0 ? 'critical' : mine.length || team.length ? 'neutral' : 'clear';

  return (
    <PostureLead
      tone={tone}
      restsOn="Your queue: tests assigned to you, hosts you have in review, and findings you own. Everything in the second sentence is team-wide — anyone can take it."
    >
      {mine.length > 0 ? `You have ${list(mine)}.` : 'Nothing is waiting on you.'}
      {team.length > 0 && ` Across the team: ${list(team)}.`}
    </PostureLead>
  );
};

export default Operations;
