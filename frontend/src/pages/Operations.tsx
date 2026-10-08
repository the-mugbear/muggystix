/**
 * Operations — what should I do next?  The signed-in person's page, and only
 * theirs (owner, 2026-10-02, 5.330.0): project status is Posture's.
 *
 * Top to bottom (5.331.0 — tabs, one list at a time; UI_STYLE_GUIDE §42):
 *
 *   header            title · Start Agent Session · updated-at + refresh
 *   lead              the kinds of work said apart, no total; every number
 *                     opens the tab (and filter) it counts
 *   since last visit  what changed while the reader was away (only then)
 *   blocked           stopped imports, with the action that unblocks them
 *   tab bar           Findings · Hosts · Tests · Changed since review · Pick up,
 *                     each with its count
 *   the one list      the selected tab's full table, 10 rows a page
 *   Your agent sessions       one line, the reader's own sessions
 *
 * It was six lists stacked at one weight — "My work" with five groups of
 * five-row samples in sentence rows, then two queue tables — each with its
 * own "more", and the same host could show three times.  The tab is in the
 * URL (`?tab=`; with none, the first non-empty tab in bar order opens), the
 * counts come from one light workbench call, and only the selected tab's rows
 * are fetched (`components/operations/OperationsTabs`).
 *
 * What left in 5.330.0, and where it is: the measures strip (tested x of y,
 * untouched with a critical observation — the terrain's sentence states both),
 * "Where the team has been" and the Exposure block (scanner observations by
 * severity, the three scope states) are on Posture; teammates' reviews are on
 * Hosts (`has:changed_since_review`, `conclusion:needs_evidence`); every
 * session of the project is on Agent Sessions.  Earlier (5.329.0) the Runs
 * list, the recent-activity column and the Project state section went
 * (Agent Sessions, Collaboration and Posture hold them).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Loader2, MessageCircleQuestion, RefreshCw, Sparkles } from 'lucide-react';
import StartAssistDialog from '../components/StartAssistDialog';
import {
  OperationsBlockers,
  ProjectCoverageResponse,
  SinceLastVisit,
  WorkbenchResponse,
  getProjectCoverage,
  getWorkbench,
  getInvestigationQueue,
  markWorkbenchSeen,
} from '../services/api';
import type { FindingNeed, MyTaskReason } from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { projectRoleAtLeast } from '../utils/projectRole';
import { useProjectRole } from '../hooks/useProjectRole';
import { useCanStartAgentSession } from '../hooks/useCanStartAgentSession';
import { formatApiError } from '../utils/apiErrors';
import AgentTaskButton from '../components/agent-sessions/AgentTaskButton';
import AgentSessionsLine from '../components/operations/AgentSessionsLine';
import OperationsTabs from '../components/operations/OperationsTabs';
import LastUpdated from '../components/LastUpdated';
import { Alert, AlertDescription, AlertTitle } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { InfoTip } from '../components/ui/info-tip';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import { sinceChips, type SinceChip } from '../utils/sinceLastVisit';
import { filenameSummary } from '../utils/filenameSummary';
import { agentInstruction } from '../utils/agentRuns';
import { cn } from '../utils/cn';
import {
  firstNonEmptyTab, needFromParams, operationsTabCounts, tabFromParams, tabSearch,
  testKindFromParams, tierFromParams,
  type OperationsTab, type OperationsTabCounts, type TabFilter,
} from '../utils/operationsTabs';
import { useMyAssistSessions } from '../hooks/useMyAssistSessions';

/** The page's independently fetched sources, each with its own load time. */
type LoadedSource = 'workbench' | 'coverage';
/** The oldest load on the page; null until something has loaded. */
const oldestLoad = (loaded: Partial<Record<LoadedSource, Date>>): Date | null => {
  const times = Object.values(loaded).filter((d): d is Date => d instanceof Date);
  return times.length ? times.reduce((a, b) => (a.getTime() <= b.getTime() ? a : b)) : null;
};

const IMPORT_ERRORS_PATH = '/parse-errors?status=needs_attention';

const LINK = 'rounded underline decoration-1 underline-offset-4 hover:text-info focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

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

const Operations: React.FC = () => {
  const navigate = useNavigate();
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
  const [error, setError] = useState<string | null>(null);
  // Operations owns ONE light /workbench fetch: every tab's count, the
  // blockers and the since-last-visit diff — no rows (5.331.0).  A tab's rows
  // are its panel's own request, made when the tab is opened.
  const [workbench, setWorkbench] = useState<WorkbenchResponse | null>(null);
  const [workbenchLoading, setWorkbenchLoading] = useState(true);
  const [workbenchError, setWorkbenchError] = useState<string | null>(null);

  // The untouched queue's SIZE, on its own request (v5.304.1: on a large
  // project the queue was most of the workbench's time): one row is asked
  // for, the totals are whole-queue.  null = not known — loading, or it could
  // not be computed — and never shown as 0.
  const [pickupTotal, setPickupTotal] = useState<number | null>(null);
  const [pickupLoading, setPickupLoading] = useState(true);
  const pickupGenRef = useRef(0);
  // `quiet`: after an action — keep the count shown until the new one arrives.
  const loadPickupTotal = useCallback((quiet = false) => {
    const gen = ++pickupGenRef.current;
    if (!quiet) setPickupLoading(true);
    return getInvestigationQueue(null, { limit: 1 })
      .then((q) => {
        if (gen !== pickupGenRef.current) return;
        setPickupTotal(q.queue_total);
      })
      .catch(() => {
        if (gen !== pickupGenRef.current) return;
        setPickupTotal(null);
      })
      .finally(() => {
        if (gen !== pickupGenRef.current) return;
        setPickupLoading(false);
      });
  }, []);

  // The tab, the Pick up tier, the Tests kind and the Findings need live in
  // the URL, so a link to "the exploitable criticals", "what is free to
  // claim" or "what needs a decision" can be shared, survives a reload, and
  // Back / Forward walk the tabs.  Nothing is remembered anywhere else.
  const [pageParams, setPageParams] = useSearchParams();
  const urlTab = tabFromParams(pageParams);
  const tier = tierFromParams(pageParams);
  const testKind = testKindFromParams(pageParams);
  const findingNeed = needFromParams(pageParams);
  const setTab = useCallback((tab: OperationsTab) => {
    // A new history entry: Back returns to the tab the reader came from.
    setPageParams(tabSearch(pageParams, tab));
  }, [pageParams, setPageParams]);
  // A tab's own filter narrows its list, which then starts from its first page.
  const setTabFilter = useCallback((key: 'tier' | 'kind' | 'need', value: string | null) => {
    const params = new URLSearchParams(pageParams);
    if (value == null) params.delete(key); else params.set(key, value);
    params.delete('page');
    setPageParams(params, { replace: true });
  }, [pageParams, setPageParams]);
  const setTier = useCallback(
    (next: number | null) => setTabFilter('tier', next == null ? null : String(next)), [setTabFilter]);
  const setTestKind = useCallback((next: MyTaskReason | null) => setTabFilter('kind', next), [setTabFilter]);
  const setFindingNeed = useCallback((next: FindingNeed | null) => setTabFilter('need', next), [setTabFilter]);

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

  // When each independently fetched source last SUCCEEDED. Several sections
  // keep their previous data on a failed refresh; the page's Refresh shows the
  // oldest of them.
  const [loadedAt, setLoadedAt] = useState<Partial<Record<LoadedSource, Date>>>({});
  const markLoaded = useCallback((source: LoadedSource) => {
    setLoadedAt((prev) => ({ ...prev, [source]: new Date() }));
  }, []);

  const reload = useCallback(async () => {
    const gen = ++reloadGenRef.current;
    const isStale = () => gen !== reloadGenRef.current;
    setError(null);
    setCoverageLoading(true);
    setWorkbenchLoading(true);
    setWorkbenchError(null);

    // The workbench and the queue's total are independent of the coverage
    // load — each isolates its own failure, so an outage shows as counts that
    // "could not be checked" instead of blanking the page.
    void loadPickupTotal();
    getWorkbench({ includeInvestigate: false, includeRows: false })
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

    // Coverage is structural: it says whether the project has scopes and
    // hosts at all, which decides between the setup blocks and the page — so
    // its failure raises the page-level error.  (Its scope states and the
    // scanner-observation counts are shown on Posture since 5.330.0.)
    try {
      const value = await getProjectCoverage();
      // A newer reload superseded us while this was in flight — drop the
      // payload so it can't overwrite the fresher one.
      if (isStale()) return;
      setCoverage(value);
      markLoaded('coverage');
    } catch (err) {
      if (isStale()) return;
      setError(formatApiError(err, 'Failed to load Operations data.'));
    }
    setCoverageLoading(false);
  }, [loadPickupTotal, markLoaded]);

  useEffect(() => {
    void reload();
    // Once per mount: `reload` is stable.
  }, [reload]);

  // After an action in a list (take, still reviewed, re-open, claim, undo):
  // the counts, without spinners (5.304.0) — the list re-reads its own rows
  // in place.  The full `reload` blanked the page and moved it under the
  // pointer after each click.
  const countsGenRef = useRef(0);
  const refreshCountsQuietly = useCallback(() => {
    const gen = ++countsGenRef.current;
    getWorkbench({ includeInvestigate: false, includeRows: false })
      .then((wb) => {
        // Two actions in a row: only the newer answer is the state now.
        if (gen !== countsGenRef.current) return;
        setWorkbench(wb);
        markLoaded('workbench');
      })
      .catch(() => { /* the next full refresh reports it */ });
    // Taking a host into review moves it out of the untouched queue.
    void loadPickupTotal(true);
  }, [loadPickupTotal, markLoaded]);

  // The page Refresh: the selected tab's list and the agent-sessions line
  // fetch for themselves, so `reload` alone left them showing what they
  // loaded on mount. The key is bumped only here (not inside `reload`, which
  // also runs on mount — that would fetch them twice).
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

  // The tab bar's counts: the workbench's own totals and the queue's.
  const counts: OperationsTabCounts = operationsTabCounts(workbench, pickupTotal);

  // With no `?tab=`, the first non-empty tab in bar order opens.  Decided
  // ONCE, when the counts first arrive (or fail): finishing the last finding
  // must not move the reader to another tab under their hands.
  const [defaultTab, setDefaultTab] = useState<OperationsTab | null>(null);
  useEffect(() => {
    if (defaultTab != null || workbenchLoading) return;
    setDefaultTab(workbench ? firstNonEmptyTab(operationsTabCounts(workbench, null)) : 'findings');
  }, [defaultTab, workbench, workbenchLoading]);
  const tab = urlTab ?? defaultTab;
  // A link that opens a tab of this page, keeping the page's parameters.
  const toTab = (target: OperationsTab, filter?: TabFilter | null) => tabSearch(pageParams, target, filter);

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

      {/* 5.332.2 — "declared" is a subnet entry, not the scope row: every
          project has its one (empty) scope from creation, and a new project
          was told "Scope is registered — scan your registered scope". */}
      {coverage && coverage.total_hosts === 0 && !coverage.scopes.some((s) => s.subnet_count > 0) && (
        <SetupBlock title="Welcome — let's set up this project">
          This project has no scope entries or scans yet. Start by registering the network ranges
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

      {coverage && coverage.scopes.some((s) => s.subnet_count > 0) && coverage.total_hosts === 0 && (
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
        // One column (UI_STYLE_GUIDE §7, §42): a lead sentence, the callouts,
        // the tab bar, the ONE selected list.  No measures strip (5.330.0):
        // the page's counts are in the lead and on the tabs, and project
        // status is on Posture.
        <div className="flex min-w-0 flex-col gap-lg">
          {workbench && !workbenchError && (
            <OperationsLead workbench={workbench} counts={counts} toTab={toTab} />
          )}
          {workbenchError && !workbenchLoading && (
            // The counts could not be read: the tabs say "—", and each list
            // still answers for itself.
            <Alert variant="destructive">
              <AlertTitle>Couldn't load your work's counts</AlertTitle>
              <AlertDescription>
                <p className="break-words">{workbenchError}</p>
                <p className="mt-xxs">The tabs below still load their own lists; a count shown as “—” could not be checked.</p>
                <Button size="sm" variant="outline" className="mt-xs" onClick={() => void reload()}>
                  <RefreshCw className="size-3.5" aria-hidden /> Retry
                </Button>
              </AlertDescription>
            </Alert>
          )}
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

          {/* The reader's work, one list at a time: the bar's counts come
              from the workbench; only the selected tab's rows are fetched. */}
          <OperationsTabs
            tab={tab}
            onTab={setTab}
            counts={counts}
            countsLoading={workbenchLoading}
            pickupLoading={pickupLoading}
            canWrite={canWrite}
            refreshKey={refreshKey}
            onCountsChanged={refreshCountsQuietly}
            tier={tier}
            onTier={setTier}
            testKind={testKind}
            onTestKind={setTestKind}
            testGroups={workbench?.my_tasks?.group_counts ?? null}
            findingNeed={findingNeed}
            onFindingNeed={setFindingNeed}
          />

          <AgentSessionsLine refreshKey={refreshKey} />
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

/** One linked number-phrase of the lead. */
interface LeadClause { key: string; to: string; text: string }

/** What the lead's words count — on the (i), not a caption line under it. */
const LEAD_DEFINITION = 'A decision is a finding under investigation or a proposal waiting for you; tests on hosts you review are listed with those hosts, not counted as assigned to you.';

const LeadLinks: React.FC<{ clauses: LeadClause[] }> = ({ clauses }) => (
  <>
    {clauses.map((c, i) => (
      <React.Fragment key={c.key}>
        {i > 0 && (i === clauses.length - 1 ? ' and ' : ', ')}
        <Link to={c.to} className={LINK}>{c.text}</Link>
      </React.Fragment>
    ))}
  </>
);

/** Lines two and three of the lead: the same sentence style, at reading weight. */
const LEAD_NEXT_LINE = 'mt-xxs block text-metadata font-normal';

/**
 * The page's lead (v5.267.0), from the same payloads the tabs render, so the
 * lead and the tabs cannot disagree.  5.329.0 — every number is a link to the
 * list it counts.  5.330.0 — no "Across the team": the changed reviews are the
 * reader's own.  5.331.0 — the links open TABS (`?tab=`).
 *
 * Owner, 2026-10-02 — NO grand total.  "You have 101 items in your
 * queue" added unlike things: decisions, report writing, and tests the reader
 * was never assigned (they sit on a host the reader reviews).  The kinds are
 * said apart, in the order someone would do them:
 *
 *   1. what needs the reader — findings to decide, findings to write up, tests
 *      assigned to them;
 *   2. what the reader holds — hosts In Review (with the tests on them), and
 *      their finished reviews that changed;
 *   3. what can be picked up — stopped imports, the untouched queue, tests
 *      free to claim.
 *
 * A clause whose number is 0 is left out; a count that is not known is never
 * said as zero.  Blocked work colours the lead; work alone does not (it is
 * the page's normal state).
 */
const OperationsLead: React.FC<{
  workbench: WorkbenchResponse;
  /** The tab bar's counts — the same numbers, so the lead and the tabs
   *  cannot disagree.  A count that is not known is never said as zero. */
  counts: OperationsTabCounts;
  toTab: (tab: OperationsTab, filter?: TabFilter | null) => string;
}> = ({ workbench, counts, toTab }) => {
  const b = workbench.blockers;
  const known = !workbench.blockers_unavailable && b;
  const failed = known ? b.failed_import_count : 0;
  const partial = known ? b.partial_import_count : 0;
  const blocked = failed + partial;
  const n = (v: number) => v.toLocaleString();
  const s = (v: number, one: string, many: string) => `${n(v)} ${v === 1 ? one : many}`;

  // 1 — what needs the reader, decisions first.
  const decide = counts.findingsDecide ?? 0;
  const write = counts.findingsWrite ?? 0;
  // A server that does not say the two kinds apart: the findings as one.
  const undivided = counts.findingsDecide == null && counts.findingsWrite == null ? (counts.findings ?? 0) : 0;
  const assigned = counts.testsAssigned ?? 0;
  const findingClauses: LeadClause[] = [];
  if (decide > 0) {
    findingClauses.push({
      key: 'decide', to: toTab('findings', 'decide'),
      text: `${s(decide, 'finding needs', 'findings need')} a decision`,
    });
  }
  if (write > 0) {
    findingClauses.push({
      key: 'write', to: toTab('findings', 'write'),
      text: decide > 0
        ? `${s(write, 'needs', 'need')} report text`
        : `${s(write, 'finding needs', 'findings need')} report text`,
    });
  }
  if (undivided > 0) {
    findingClauses.push({
      key: 'findings', to: toTab('findings', null),
      text: `${s(undivided, 'finding needs', 'findings need')} you`,
    });
  }
  const assignedClause: LeadClause | null = assigned > 0
    ? { key: 'assigned', to: toTab('tests', 'assigned'), text: `${s(assigned, 'test is', 'tests are')} assigned to you` }
    : null;
  const needsMe = findingClauses.length > 0 || assignedClause != null;

  // 2 — what the reader holds.  Tests on those hosts are listed with them:
  // nobody assigned them to the reader.
  const hosts = counts.hosts ?? 0;
  const onHosts = counts.testsInReview ?? 0;
  // The reader's own reviews (5.330.0); a failed check is not counted as zero
  // in words — its tab says "—".
  const changed = counts.changed ?? 0;
  const holds = hosts > 0 || onHosts > 0 || changed > 0;

  // 3 — stopped imports, and what can be picked up next.
  const worth = counts.pickup ?? 0;
  const claim = counts.toClaim ?? 0;
  const also: LeadClause[] = [
    failed > 0 ? { key: 'failed', to: IMPORT_ERRORS_PATH, text: `${s(failed, 'import', 'imports')} failed` } : null,
    partial > 0 ? { key: 'partial', to: IMPORT_ERRORS_PATH, text: `${s(partial, 'import', 'imports')} finished partial` } : null,
    worth > 0 ? { key: 'untouched', to: toTab('pickup'), text: `${s(worth, 'untouched host has', 'untouched hosts have')} a reason to look` } : null,
    claim > 0 ? { key: 'claim', to: toTab('tests', 'triage'), text: `${s(claim, 'test is', 'tests are')} free to claim` } : null,
  ].filter((p): p is LeadClause => !!p);

  const tone: LeadTone = blocked > 0 ? 'critical' : needsMe || holds || also.length ? 'neutral' : 'clear';

  return (
    <PostureLead tone={tone}>
      <span className="block" data-testid="lead-needs-me">
        {needsMe ? (
          <>
            <LeadLinks clauses={findingClauses} />
            {findingClauses.length > 0 && assignedClause && '; '}
            {assignedClause && <LeadLinks clauses={[assignedClause]} />}
            .{' '}
            <InfoTip text={LEAD_DEFINITION} label="What counts as a decision" />
          </>
        ) : 'Nothing is waiting on a decision from you.'}
      </span>
      {holds && (
        <>
          {' '}
          <span className={LEAD_NEXT_LINE} data-testid="lead-holds">
            {hosts > 0 ? (
              <>
                In review:{' '}
                <Link to={toTab('hosts')} className={LINK}>{s(hosts, 'host', 'hosts')}</Link>
                {onHosts > 0 && (
                  <>
                    , with{' '}
                    <Link to={toTab('tests', 'in_review')} className={LINK}>
                      {s(onHosts, 'test', 'tests')} on {hosts === 1 ? 'it' : 'them'}
                    </Link>
                  </>
                )}
              </>
            ) : onHosts > 0 && (
              <Link to={toTab('tests', 'in_review')} className={LINK}>
                {s(onHosts, 'test is', 'tests are')} on hosts you review
              </Link>
            )}
            {(hosts > 0 || onHosts > 0) && changed > 0 && '; '}
            {changed > 0 && (
              <Link to={toTab('changed')} className={LINK}>
                {s(changed, 'host you reviewed has', 'hosts you reviewed have')} changed since
              </Link>
            )}
            .
          </span>
        </>
      )}
      {also.length > 0 && (
        <>
          {' '}
          <span className={LEAD_NEXT_LINE} data-testid="lead-pick-up">
            To pick up: <LeadLinks clauses={also} />.
          </span>
        </>
      )}
    </PostureLead>
  );
};

export default Operations;
