/**
 * Operations — what should I do next?  The signed-in person's page, and only
 * theirs (owner, 2026-10-02, 5.330.0): project status is Posture's.
 *
 * Top to bottom:
 *
 *   header            title · Start Agent Session · updated-at + refresh
 *   lead              one sentence; every number links to what it counts
 *   since last visit  what changed while the reader was away
 *   blocked           stopped imports, with the action that unblocks them
 *   My work           only what needs the reader, each row saying what
 *   Changed since review      the reader's OWN reviews that are not done
 *   Untouched, with a reason  what to pick up next (nobody's yet)
 *   Your agent sessions       one line, the reader's own sessions
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
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, Loader2, MessageCircleQuestion, Sparkles } from 'lucide-react';
import StartAssistDialog from '../components/StartAssistDialog';
import {
  OperationsBlockers,
  ProjectCoverageResponse,
  SinceLastVisit,
  WorkbenchResponse,
  InvestigationQueueResponse,
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
import MyWorkCard, { MY_WORK_ID, personalWorkCounts } from '../components/MyWorkCard';
import AgentSessionsLine from '../components/operations/AgentSessionsLine';
import ChangedSinceReviewSection, { CHANGED_SINCE_REVIEW_TITLE } from '../components/operations/ChangedSinceReviewSection';
import UntouchedQueueSection, {
  UNTOUCHED_MAX_ROWS, UNTOUCHED_PAGE,
} from '../components/operations/UntouchedQueueSection';
import { UnavailableLine } from '../components/operations/QueueParts';
import UpdatedAt from '../components/UpdatedAt';
import LastUpdated from '../components/LastUpdated';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import PostureSection from '../components/posture/PostureSection';
import PostureLead, { type LeadTone } from '../components/posture/PostureLead';
import { sinceChips, type SinceChip } from '../utils/sinceLastVisit';
import { filenameSummary } from '../utils/filenameSummary';
import { agentInstruction } from '../utils/agentRuns';
import { cn } from '../utils/cn';
import { useMyAssistSessions } from '../hooks/useMyAssistSessions';

/** The page's independently fetched sources, each with its own load time. */
type LoadedSource = 'workbench' | 'coverage';
/** The oldest load on the page; null until something has loaded. */
const oldestLoad = (loaded: Partial<Record<LoadedSource, Date>>): Date | null => {
  const times = Object.values(loaded).filter((d): d is Date => d instanceof Date);
  return times.length ? times.reduce((a, b) => (a.getTime() <= b.getTime() ? a : b)) : null;
};

/** In-page targets of the lead's links. */
const CHANGED_ID = 'changed-since-review';
const UNTOUCHED_ID = 'untouched-queue';
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
  const [error, setError] = useState<string | null>(null);
  // Operations owns ONE /workbench fetch covering My work, the changed-since-
  // review queue, the blockers and the since-last-visit diff (P2).
  const [workbench, setWorkbench] = useState<WorkbenchResponse | null>(null);
  const [workbenchLoading, setWorkbenchLoading] = useState(true);
  const [workbenchError, setWorkbenchError] = useState<string | null>(null);

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

  const reload = useCallback(async () => {
    const gen = ++reloadGenRef.current;
    const isStale = () => gen !== reloadGenRef.current;
    setError(null);
    setCoverageLoading(true);
    setWorkbenchLoading(true);
    setWorkbenchError(null);

    // The workbench and the queue are independent of the coverage load —
    // each isolates its own failure, so an outage shows that section's error
    // state (with Retry) instead of blanking the page.
    void loadInvestigate();
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
  }, [loadInvestigate, markLoaded]);

  useEffect(() => {
    void reload();
    // Once per mount: `reload` is stable, and the tier in the URL is read
    // through its ref.
  }, [reload]);

  // After an action in a queue (take, still reviewed, re-open, claim, undo):
  // the workbench and the queue, without spinners (5.304.0).  The full
  // `reload` blanked every section and moved the page under the pointer
  // after each click.
  const refreshWorkbenchQuietly = useCallback(() => {
    getWorkbench({ includeInvestigate: false })
      .then((wb) => {
        setWorkbench(wb);
        markLoaded('workbench');
      })
      .catch(() => { /* the next full refresh reports it */ });
    // Taking a host into review moves it out of the untouched queue.
    void loadInvestigate(true);
  }, [loadInvestigate, markLoaded]);

  // The page Refresh: the agent-sessions line fetches for itself, so
  // `reload` alone left it showing what it loaded on mount. The key is
  // bumped only here (not inside `reload`, which also runs
  // on mount — that would fetch it twice).
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

  // The lead's in-page links (#my-work …): go to the section.
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
        // the callouts, then sections over thin rules.  No measures strip
        // (5.330.0): the page's two counts are in the lead and the section
        // headings, and project status is on Posture.
        <div className="flex min-w-0 flex-col gap-lg">
          {workbench && !workbenchError && (
            <OperationsLead
              workbench={workbench}
              untouched={investigateUnavailable ? null : (investigate?.queue_total ?? null)}
              here={here}
            />
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

          {/* Yours to re-check, then what nobody has picked up yet. */}
          <div id={CHANGED_ID} className="min-w-0 scroll-mt-md">
            {!workbenchLoading && !workbenchError && followupsUnavailable && (
              <PostureSection title={<span>{CHANGED_SINCE_REVIEW_TITLE}</span>}>
                <UnavailableLine onRetry={() => void reload()}>
                  Unavailable — the hosts you reviewed could not be checked for open questions or later
                  changes. This is not a confirmation that none changed.
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

/**
 * The page's lead (v5.267.0): what is waiting on the reader — their queue,
 * the hosts they reviewed that changed — then what stopped (imports) and what
 * can be picked up next (the untouched queue), from the same payloads the
 * sections below render, so the sentence and the sections cannot disagree.
 * 5.329.0 — every number is a link to the section or list it counts, and
 * "your queue" counts work: a finding you own is in it only when it needs
 * you.  5.330.0 — no "Across the team": the changed reviews are the reader's
 * own.  Blocked work colours it; a queue alone does not (work is the page's
 * normal state).
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
  // The reader's own reviews (5.330.0); a failed check is not counted as zero
  // in words — the section below says "unavailable".
  const changed = workbench.followups_unavailable ? 0 : (workbench.followups?.total ?? 0);
  const worth = untouched ?? 0;
  const n = (v: number) => v.toLocaleString();
  const s = (v: number, one: string, many: string) => `${n(v)} ${v === 1 ? one : many}`;

  // What else the page holds: stopped imports, and what can be picked up next.
  const also: Array<{ key: string; to: string; text: string }> = [
    failed > 0 ? { key: 'failed', to: IMPORT_ERRORS_PATH, text: `${s(failed, 'import', 'imports')} failed` } : null,
    partial > 0 ? { key: 'partial', to: IMPORT_ERRORS_PATH, text: `${s(partial, 'import', 'imports')} finished partial` } : null,
    worth > 0 ? { key: 'untouched', to: here(UNTOUCHED_ID), text: `${s(worth, 'untouched host has', 'untouched hosts have')} a reason to look` } : null,
  ].filter((p): p is { key: string; to: string; text: string } => !!p);

  const tone: LeadTone = blocked > 0 ? 'critical' : total > 0 || changed > 0 || also.length ? 'neutral' : 'clear';

  return (
    <PostureLead
      tone={tone}
      restsOn="Your queue: hosts you have in review, tests assigned to you or on those hosts, and findings you own that need something. Changed since review counts only reviews you finished. Failed imports and untouched hosts are nobody’s yet — anyone can take them; the project’s status is on Posture."
    >
      {total > 0 ? (
        <>You have <Link to={here(MY_WORK_ID)} className={LINK}>{s(total, 'item', 'items')}</Link> in your queue</>
      ) : 'Nothing is waiting on you'}
      {changed > 0 && (
        <>
          {total > 0 ? ', and ' : ', but '}
          <Link to={here(CHANGED_ID)} className={LINK}>
            {s(changed, 'host you reviewed has', 'hosts you reviewed have')} changed since
          </Link>
        </>
      )}
      .
      {also.length > 0 && (
        <>
          {' '}To pick up:{' '}
          {also.map((part, i) => (
            <React.Fragment key={part.key}>
              {i > 0 && (i === also.length - 1 ? ' and ' : ', ')}
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
