/**
 * Operations — what should I do next, and is the team covering the ground?
 *
 * Top to bottom (design review 2026-10-02, 5.329.0):
 *
 *   header            title · Start Agent Session · updated-at + refresh
 *   lead              one sentence; every number links to what it counts
 *   measures          ONE strip of four: tested · untouched with a critical
 *                     observation · changed since review · my queue
 *   since last visit  what changed while the reader was away
 *   blocked           stopped imports, with the action that unblocks them
 *   My work           only what needs the reader, each row saying what
 *   Changed since review      ┐ the two team queues: one-line rows,
 *   Untouched, with a reason  ┘ selection, bulk actions, "Open all N"
 *   Where the team has been   the terrain: sentence + hot block; map on demand
 *   Agent sessions    one line, the sentence Agent Sessions leads with
 *   Exposure          scanner observations by severity, the three scope
 *                     states, and the way to Posture
 *
 * It was three pages stacked into one — a personal queue, two triage queues
 * and a project status report: 3,600 px tall, 62 buttons, and the one number
 * that says where the engagement stands was the last thing on it.  The Runs
 * list, the recent-activity column and the Project state section are gone
 * (Agent Sessions, Collaboration and Posture hold them).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Loader2, MessageCircleQuestion, Sparkles } from 'lucide-react';
import StartAssistDialog from '../components/StartAssistDialog';
import {
  DashboardStats,
  OperationsBlockers,
  OperationsMeasures as OperationsMeasuresData,
  ProjectCoverageResponse,
  SinceLastVisit,
  WorkbenchResponse,
  InvestigationQueueResponse,
  getDashboardStats,
  getOperationsMeasures,
  getProjectCoverage,
  getWorkbench,
  getInvestigationQueue,
  markWorkbenchSeen,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { projectRoleAtLeast } from '../utils/projectRole';
import { useProjectRole } from '../hooks/useProjectRole';
import { useCanStartAgentSession } from '../hooks/useCanStartAgentSession';
import { formatApiError } from '../utils/apiErrors';
import AgentTaskButton from '../components/agent-sessions/AgentTaskButton';
import MyWorkCard, { MY_WORK_ID, TO_CLAIM_ID, personalWorkCounts } from '../components/MyWorkCard';
import AddressTerrainSection from '../components/operations/AddressTerrainSection';
import AgentSessionsLine from '../components/operations/AgentSessionsLine';
import ChangedSinceReviewSection, { CHANGED_SINCE_REVIEW_TITLE } from '../components/operations/ChangedSinceReviewSection';
import OperationsMeasures from '../components/operations/OperationsMeasures';
import UntouchedQueueSection, {
  UNTOUCHED_MAX_ROWS, UNTOUCHED_PAGE,
} from '../components/operations/UntouchedQueueSection';
import { UnavailableLine } from '../components/operations/QueueParts';
import UpdatedAt from '../components/UpdatedAt';
import LastUpdated from '../components/LastUpdated';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import PostureSection from '../components/posture/PostureSection';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import SeverityBar from '../components/ui/SeverityBar';
import { buildHostsUrl } from '../utils/drilldownLinks';
import { sinceChips, type SinceChip } from '../utils/sinceLastVisit';
import { filenameSummary } from '../utils/filenameSummary';
import { agentInstruction } from '../utils/agentRuns';
import { cn } from '../utils/cn';
import { useMyAssistSessions } from '../hooks/useMyAssistSessions';

/** The page's independently fetched sources, each with its own load time. */
type LoadedSource = 'workbench' | 'coverage' | 'stats' | 'measures';
/** The oldest load on the page; null until something has loaded. */
const oldestLoad = (loaded: Partial<Record<LoadedSource, Date>>): Date | null => {
  const times = Object.values(loaded).filter((d): d is Date => d instanceof Date);
  return times.length ? times.reduce((a, b) => (a.getTime() <= b.getTime() ? a : b)) : null;
};

/** In-page targets of the lead's and the measures' links. */
const CHANGED_ID = 'changed-since-review';
const UNTOUCHED_ID = 'untouched-queue';
const IMPORT_ERRORS_PATH = '/parse-errors?status=needs_attention';

const LINK = 'rounded underline decoration-1 underline-offset-4 hover:text-info focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

// ---------------------------------------------------------------------------
// Exposure — what stays of the old "Project state" section.
//
// Its coverage strip is the measures strip now, and the assessment itself is
// on Posture.  Two things live nowhere else, so they stay, at the bottom:
// scanner observations by severity (Posture and Findings show FINDINGS by
// severity; the raw, not-yet-judged rows are only counted here), and the
// three scope-coverage states.
// ---------------------------------------------------------------------------

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

const ExposureSection: React.FC<{
  stats: DashboardStats | null;
  statsLoading: boolean;
  statsError: string | null;
  onRetry: () => void;
  coverage: ProjectCoverageResponse;
  updated?: React.ReactNode;
}> = ({ stats, statsLoading, statsError, onRetry, coverage, updated }) => {
  const vuln = stats?.vulnerability_stats;
  // Informational is excluded from the bar (it dwarfs real severities); the
  // bar's denominator is the non-info total so its segments fill the rail.
  const actionableTotal = vuln ? vuln.critical + vuln.high + vuln.medium + vuln.low : 0;

  return (
    <PostureSection
      title={<span>Exposure</span>}
      description={<>
        What the scanners reported, not yet judged. The assessment — coverage, segments, patterns and
        evidence — is on{' '}
        <Link to="/posture" className="text-info hover:underline">Posture</Link>; the judged record on{' '}
        <Link to="/findings" className="text-info hover:underline">Findings</Link>.
      </>}
      actions={updated}
    >
      <div className="flex min-w-0 flex-col gap-md">
        {statsError ? (
          <UnavailableLine onRetry={onRetry}>
            Scanner observations could not be counted — this is not a clean project. {statsError}
          </UnavailableLine>
        ) : statsLoading && !stats ? (
          <p role="status" aria-live="polite" className="flex items-center gap-xs text-metadata text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden /> Counting scanner observations…
          </p>
        ) : vuln && actionableTotal > 0 ? (
          <div className="max-w-3xl" aria-busy={statsLoading || undefined}>
            <div className="mb-xs flex flex-wrap items-baseline justify-between gap-x-md gap-y-xxs">
              <h3 className="inline-flex items-center gap-xxs text-metadata font-semibold text-foreground">
                Scanner observations by severity
                <InfoTip text="One per scanner check per port, as imported — not yet judged, and a host usually carries several. Each severity opens the hosts carrying at least one observation of it; that host count is under the number." />
              </h3>
              <Link
                to={buildHostsUrl({ q: 'kind:vulnerability,misconfiguration,informational' })}
                className="text-caption tabular-nums text-muted-foreground hover:text-info hover:underline"
              >
                {actionableTotal.toLocaleString()} observations, informational excluded
                {' · '}{(vuln.hosts_with_vulnerabilities ?? 0).toLocaleString()} hosts carry one
              </Link>
            </div>
            <SeverityBar
              variant="summary"
              counts={vuln}
              total={actionableTotal}
              ariaLabel="Scanner observations by severity"
              // SeverityBar never renders info, but the callback is typed
              // over all severities — guard so the type narrows to HostSeverity.
              segmentHref={(sev) => (sev === 'info' ? null : buildHostsUrl({ severity: sev }))}
              // The counts are observations, the links hosts: the link says so.
              linkLabel={(sev) => {
                const n = vuln.hosts_by_severity?.[sev];
                return n == null ? null : `${n.toLocaleString()} host${n === 1 ? '' : 's'}`;
              }}
            />
          </div>
        ) : stats ? (
          <p className="text-metadata text-muted-foreground">
            No critical, high, medium or low scanner observation is recorded — upload a Nessus or
            OpenVAS scan to populate this.
          </p>
        ) : null}

        {/* The three coverage states, adding up to every host; each number
            opens its hosts.  Scope names are not shown: they are a relic. */}
        {coverage.total_scopes > 0 ? (
          <p className="flex min-w-0 flex-wrap items-center gap-x-xs gap-y-xxs text-metadata">
            <span className="font-semibold text-foreground">Scope</span>
            <InfoTip text="Every host is in exactly one of these: inside a scope subnet; in no subnet but reached through an in-scope name (the name was approved, not the address); or outside scope — discovered, but nobody approved testing it, so confirm it is in scope before acting on it." />
            <ScopeStateLink n={coverage.hosts_in_subnet_scope} q="scope:subnet" label="in scope subnets" />
            <span className="text-muted-foreground" aria-hidden>·</span>
            <ScopeStateLink n={coverage.hosts_name_scope_only} q="scope:name" label="reached only through an in-scope name" />
            <span className="text-muted-foreground" aria-hidden>·</span>
            <ScopeStateLink n={coverage.hosts_outside_scope} q="scope:none" label="outside scope" />
          </p>
        ) : (
          <p className="text-metadata text-muted-foreground">
            No scope is declared, so every host is outside scope.{' '}
            <Link to="/scopes" className="text-info hover:underline">Register a scope</Link>
          </p>
        )}

        <p className="text-metadata">
          <Link to="/posture" className="text-info hover:underline">
            Exposure and assessment coverage are on Posture →
          </Link>
        </p>
      </div>
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
              onClick={() => navigate(IMPORT_ERRORS_PATH)}>
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

/** `?tier=N` — the tier the untouched queue is narrowed to. */
const tierFrom = (params: URLSearchParams): number | null => {
  const v = Number(params.get('tier'));
  return Number.isInteger(v) && v >= 1 && v <= 5 ? v : null;
};

const Operations: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { currentProject } = useProject();
  // Write controls — Review, Still reviewed, Re-open, Claim, the selection
  // columns — follow the project role (UI_STYLE_GUIDE §40): hidden for a
  // reader, shown while the role is not known yet (the server decides).
  const { canWrite } = useProjectRole();
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
  // A failed stats request must read "could not be counted", never as an
  // empty bar — which says "a clean project".  (UX review #8.)
  const [statsError, setStatsError] = useState<string | null>(null);
  // Operations owns ONE /workbench fetch covering My work, the changed-since-
  // review queue, the blockers and the since-last-visit diff (P2).
  const [workbench, setWorkbench] = useState<WorkbenchResponse | null>(null);
  const [workbenchLoading, setWorkbenchLoading] = useState(true);
  const [workbenchError, setWorkbenchError] = useState<string | null>(null);

  // 5.329.0 — the measures strip's project-wide counts load beside the
  // workbench (GET /workbench/measures), so the first paint waits for
  // neither.  A failure is "unavailable", never zeros.
  const [measures, setMeasures] = useState<OperationsMeasuresData | null>(null);
  const [measuresLoading, setMeasuresLoading] = useState(true);
  const [measuresUnavailable, setMeasuresUnavailable] = useState(false);
  const measuresGenRef = useRef(0);

  // v5.304.1 — the untouched queue loads on its own request: on a large
  // project it was most of the workbench's time, and My work waited for it.
  const [investigate, setInvestigate] = useState<InvestigationQueueResponse | null>(null);
  const [investigateLoading, setInvestigateLoading] = useState(true);
  const [investigateUnavailable, setInvestigateUnavailable] = useState(false);
  const [investigateMoreBusy, setInvestigateMoreBusy] = useState(false);
  const investigateGenRef = useRef(0);
  // The tier the queue is narrowed to lives in the URL (?tier=), so a link to
  // "the exploitable criticals" can be shared and survives a reload.  Refs
  // too, so a refresh or an action reloads what is being looked at.
  const [pageParams, setPageParams] = useSearchParams();
  const investigateTier = tierFrom(pageParams);
  const investigateTierRef = useRef<number | null>(investigateTier);
  const investigateLimitRef = useRef(UNTOUCHED_PAGE);
  // `quiet`: after an action in a queue — keep what is shown until the new
  // queue arrives instead of flashing the loading line.
  const loadInvestigate = useCallback((quiet = false) => {
    const gen = ++investigateGenRef.current;
    if (!quiet) setInvestigateLoading(true);
    return getInvestigationQueue(investigateTierRef.current, { limit: investigateLimitRef.current })
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
  const setInvestigateTier = useCallback((tier: number | null) => {
    investigateTierRef.current = tier;
    investigateLimitRef.current = UNTOUCHED_PAGE;
    const next = new URLSearchParams(pageParams);
    if (tier == null) next.delete('tier'); else next.set('tier', String(tier));
    setPageParams(next, { replace: true });
    void loadInvestigate(true);
  }, [pageParams, setPageParams, loadInvestigate]);
  // Back / Forward (or an edited address) changes the tier without a click.
  useEffect(() => {
    if (investigateTierRef.current === investigateTier) return;
    investigateTierRef.current = investigateTier;
    investigateLimitRef.current = UNTOUCHED_PAGE;
    void loadInvestigate(true);
  }, [investigateTier, loadInvestigate]);
  const showMoreUntouched = useCallback(() => {
    investigateLimitRef.current = Math.min(UNTOUCHED_MAX_ROWS, investigateLimitRef.current + UNTOUCHED_PAGE);
    setInvestigateMoreBusy(true);
    void loadInvestigate(true).finally(() => setInvestigateMoreBusy(false));
  }, [loadInvestigate]);

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

  const loadMeasures = useCallback((quiet = false) => {
    const gen = ++measuresGenRef.current;
    if (!quiet) setMeasuresLoading(true);
    getOperationsMeasures()
      .then((m) => {
        if (gen !== measuresGenRef.current) return;
        setMeasures(m);
        setMeasuresUnavailable(false);
        markLoaded('measures');
      })
      .catch(() => {
        if (gen !== measuresGenRef.current) return;
        // Never keep an old number under a failed count: it would read as current.
        setMeasures(null);
        setMeasuresUnavailable(true);
      })
      .finally(() => {
        if (gen !== measuresGenRef.current) return;
        setMeasuresLoading(false);
      });
  }, [markLoaded]);

  const reload = useCallback(async () => {
    const gen = ++reloadGenRef.current;
    const isStale = () => gen !== reloadGenRef.current;
    setError(null);
    setCoverageLoading(true);
    setStatsLoading(true);
    setWorkbenchLoading(true);
    setWorkbenchError(null);

    // The workbench, the queue and the measures are independent of the
    // coverage/stats core load — each isolates its own failure, so an outage
    // shows that section's error state (with Retry) instead of blanking the page.
    void loadInvestigate();
    loadMeasures();
    getWorkbench({ includeInvestigate: false, includeMeasures: false })
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

    // RV-10b — settle the core fetches independently. Only coverage is
    // structural (it gates the whole page), so only its failure raises the
    // page-level error; the scanner-observation counts degrade on their own.
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
    if (statsR.status === 'fulfilled') {
      setStats(statsR.value);
      setStatsError(null);
      markLoaded('stats');
    } else {
      setStatsError(formatApiError(statsR.reason, 'Could not load project statistics.'));
    }

    setCoverageLoading(false);
    setStatsLoading(false);
  }, [loadInvestigate, loadMeasures, markLoaded]);

  useEffect(() => {
    void reload();
    // Once per mount: `reload` is stable, and the tier in the URL is read
    // through its ref.
  }, [reload]);

  // After an action in a queue (take, still reviewed, re-open, claim, undo):
  // the workbench, the queue and the measures, without spinners (5.304.0).
  // The full `reload` blanked every section and moved the page under the
  // pointer after each click.
  const refreshWorkbenchQuietly = useCallback(() => {
    getWorkbench({ includeInvestigate: false, includeMeasures: false })
      .then((wb) => {
        setWorkbench(wb);
        markLoaded('workbench');
      })
      .catch(() => { /* the next full refresh reports it */ });
    // Taking a host into review moves it out of the untouched queue and out
    // of "untouched with a critical observation".
    void loadInvestigate(true);
    loadMeasures(true);
  }, [loadInvestigate, loadMeasures, markLoaded]);

  // The page Refresh: the terrain and the agent-sessions line fetch for
  // themselves, so `reload` alone left them showing what they loaded on
  // mount. The key is bumped only here (not inside `reload`, which also runs
  // on mount — that would fetch both twice).
  const [refreshKey, setRefreshKey] = useState(0);
  const refreshAll = useCallback(() => {
    setRefreshKey((k) => k + 1);
    void reload();
  }, [reload]);

  // FRX·CRIT-2: brand-new projects (no scopes AND no hosts) see the welcome
  // block alone — Refresh chrome just adds noise before anything exists.
  const isBrandNewProject =
    !!coverage && coverage.total_hosts === 0 && coverage.total_scopes === 0;

  // v4.29.0 — the agent-session entry.  Lives on Operations because it's the
  // project-level coordination hub.  Since 5.313.0 it is the ONLY way an agent
  // starts: the per-object buttons (a scope's scan, a host's tests) open the
  // same dialog with a task for the agent (AgentTaskButton).
  const [assistDialogOpen, setAssistDialogOpen] = useState(false);
  // `POST /assist/start` needs project auditor — a viewer is offered no entry
  // (nor the `?start=` deep link, which then just drops its param).
  const canStartAgent = useCanStartAgentSession();
  // `?start=agent-session` opens the dialog on arrival — the Agent Sessions
  // page links here as "where a session is started". The param is dropped
  // once read so a refresh or Back does not reopen it.
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

  // The lead's and the measures' in-page links (#my-work …): go to the section.
  // A navigation asks once; the scroll happens when its section exists (it may
  // still be loading on arrival) and is then forgotten, so a later refresh of
  // the queues never drags the page back.
  const pendingHashRef = useRef<string | null>(null);
  useEffect(() => {
    pendingHashRef.current = location.hash.replace(/^#/, '') || null;
  }, [location.key, location.hash]);
  useEffect(() => {
    const id = pendingHashRef.current;
    if (!id) return;
    const el = document.getElementById(id);
    if (!el) return;
    pendingHashRef.current = null;
    el.scrollIntoView?.({ block: 'start' });
  });
  // A link to a section of this page, keeping the page's own parameters.
  const here = (id: string) => `${location.search}#${id}`;

  // One list owns j / k / Enter / x at a time: the queue the reader last
  // pointed at or focused.  Two window listeners would both move.
  const [keysIn, setKeysIn] = useState<'changed' | 'untouched' | null>(null);

  const myQueue = workbench && !workbenchError
    ? personalWorkCounts(workbench.my_queue, workbench.my_tasks, workbench.my_findings, workbench.my_work)
    : null;
  const followups = workbench?.followups ?? null;
  const followupsUnavailable = workbench?.followups_unavailable ?? false;

  return (
    <div className="min-w-0 p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title font-semibold">Operations</h1>
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
          you're authorized to assess — everything else (coverage, triage, tests, agent
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
        // One column read top to bottom (UI_STYLE_GUIDE §7): a lead sentence,
        // one strip of measures, the callouts, then sections over thin rules.
        <div className="flex min-w-0 flex-col gap-lg">
          {workbench && !workbenchError && (
            <OperationsLead
              workbench={workbench}
              untouched={investigateUnavailable ? null : (investigate?.queue_total ?? null)}
              here={here}
            />
          )}
          <OperationsMeasures
            measures={measures}
            measuresLoading={measuresLoading}
            measuresUnavailable={measuresUnavailable}
            onRetryMeasures={() => loadMeasures()}
            followups={followups}
            followupsUnavailable={followupsUnavailable}
            myQueue={myQueue}
            workbenchLoading={workbenchLoading}
            workbenchFailed={!!workbenchError}
            onRetryWorkbench={() => void reload()}
            changedHref={here(CHANGED_ID)}
            myWorkHref={here(MY_WORK_ID)}
            toClaimHref={here(TO_CLAIM_ID)}
          />
          {/* Since your last visit — what changed in this project while the
              operator was away (durable per-user cursor, P2). */}
          {workbench && !sinceDismissed && (
            <SinceLastVisitBanner
              since={workbench.since_last_visit}
              onDismiss={dismissSince}
              saving={sinceSaving}
              error={sinceError}
            />
          )}
          <BlockersStrip
            blockers={workbench?.blockers ?? null}
            unavailable={workbench?.blockers_unavailable ?? false}
          />

          {/* Mine: one full-width column.  (It shared the row with a feed of
              what the reader had already done.) */}
          <MyWorkCard
            queue={workbench?.my_queue ?? null}
            tasks={workbench?.my_tasks ?? null}
            findings={workbench?.my_findings ?? null}
            totals={workbench?.my_work ?? null}
            loading={workbenchLoading}
            error={workbenchError}
            onRetry={() => void reload()}
            onChanged={refreshWorkbenchQuietly}
            canWrite={canWrite}
            updated={<UpdatedAt at={loadedAt.workbench ?? null} hideWhenFresh />}
          />

          {/* The team's: yours to re-check, then nobody's yet. */}
          <div id={CHANGED_ID} className="min-w-0 scroll-mt-md">
            {!workbenchLoading && !workbenchError && followupsUnavailable && (
              <PostureSection title={<span>{CHANGED_SINCE_REVIEW_TITLE}</span>}>
                <UnavailableLine onRetry={() => void reload()}>
                  Unavailable — reviewed hosts could not be checked for open questions or later changes.
                </UnavailableLine>
              </PostureSection>
            )}
            {!workbenchLoading && !workbenchError && !followupsUnavailable && followups && (
              <ChangedSinceReviewSection
                data={followups}
                canWrite={canWrite}
                onChanged={refreshWorkbenchQuietly}
                keysActive={keysIn === 'changed'}
                onActivate={() => setKeysIn('changed')}
              />
            )}
          </div>
          <div id={UNTOUCHED_ID} className="min-w-0 scroll-mt-md">
            <UntouchedQueueSection
              data={investigate}
              loading={investigateLoading}
              unavailable={investigateUnavailable}
              onRetry={() => void loadInvestigate()}
              tier={investigateTier}
              onTier={setInvestigateTier}
              onMore={showMoreUntouched}
              moreBusy={investigateMoreBusy}
              canWrite={canWrite}
              onChanged={refreshWorkbenchQuietly}
              keysActive={keysIn === 'untouched'}
              onActivate={() => setKeysIn('untouched')}
            />
          </div>

          {/* The same team-wide question as ground: one request, loaded when
              the section nears the viewport; the map (three.js, its own
              chunk) only when the reader opens it. */}
          <AddressTerrainSection refreshKey={refreshKey} />

          <AgentSessionsLine refreshKey={refreshKey} />

          <ExposureSection
            stats={stats}
            statsLoading={statsLoading}
            statsError={statsError}
            onRetry={() => void reload()}
            coverage={coverage}
            updated={<UpdatedAt at={loadedAt.stats ?? null} stale={!!statsError} hideWhenFresh />}
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
 * The page's lead (v5.267.0): what is waiting on the reader, then on the team,
 * from the same payloads the sections below render — so the sentence and the
 * sections cannot disagree.  5.329.0 — every number is a link to the section
 * or list it counts, and "your queue" counts work: a finding you own is in it
 * only when it needs you.  Blocked work colours it; a queue alone does not
 * (work is the page's normal state).
 */
const OperationsLead: React.FC<{
  workbench: WorkbenchResponse;
  /** The untouched queue's size; null until it is known (it loads on its own
   *  request) or when it could not be computed — never counted as zero. */
  untouched: number | null;
  here: (id: string) => string;
}> = ({ workbench, untouched, here }) => {
  const { total } = personalWorkCounts(
    workbench.my_queue, workbench.my_tasks, workbench.my_findings, workbench.my_work,
  );
  const b = workbench.blockers;
  const known = !workbench.blockers_unavailable && b;
  const failed = known ? b.failed_import_count : 0;
  const partial = known ? b.partial_import_count : 0;
  const blocked = failed + partial;
  const changed = workbench.followups_unavailable
    ? 0 : (workbench.followups?.host_total ?? workbench.followups?.total ?? 0);
  const worth = untouched ?? 0;
  const n = (v: number) => v.toLocaleString();
  const s = (v: number, one: string, many: string) => `${n(v)} ${v === 1 ? one : many}`;

  const team: Array<{ key: string; to: string; text: string }> = [
    failed > 0 ? { key: 'failed', to: IMPORT_ERRORS_PATH, text: `${s(failed, 'import', 'imports')} failed` } : null,
    partial > 0 ? { key: 'partial', to: IMPORT_ERRORS_PATH, text: `${s(partial, 'import', 'imports')} finished partial` } : null,
    changed > 0 ? { key: 'changed', to: here(CHANGED_ID), text: `${s(changed, 'reviewed host has', 'reviewed hosts have')} changed since review` } : null,
    worth > 0 ? { key: 'untouched', to: here(UNTOUCHED_ID), text: `${s(worth, 'untouched host has', 'untouched hosts have')} a reason to look` } : null,
  ].filter((p): p is { key: string; to: string; text: string } => !!p);

  const tone: LeadTone = blocked > 0 ? 'critical' : total > 0 || team.length ? 'neutral' : 'clear';

  return (
    <PostureLead
      tone={tone}
      restsOn="Your queue: hosts you have in review, tests assigned to you or on those hosts, and findings you own that need something. Everything in the second sentence is team-wide — anyone can take it."
    >
      {total > 0 ? (
        <>You have <Link to={here(MY_WORK_ID)} className={LINK}>{s(total, 'item', 'items')}</Link> in your queue.</>
      ) : 'Nothing is waiting on you.'}
      {team.length > 0 && (
        <>
          {' '}Across the team:{' '}
          {team.map((part, i) => (
            <React.Fragment key={part.key}>
              {i > 0 && (i === team.length - 1 ? ' and ' : ', ')}
              <Link to={part.to} className={LINK}>{part.text}</Link>
            </React.Fragment>
          ))}
          .
        </>
      )}
    </PostureLead>
  );
};

export default Operations;
