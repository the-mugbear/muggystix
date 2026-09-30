/**
 * TestPlanLayout — parent shell for the routed test-plan sub-tabs.
 *
 * Owns: data fetch (plan + progress + sessions), header, metadata
 * panel, action buttons, dialogs (abandon / delete / report / edit /
 * import), tab nav.  Sub-tabs receive the shared state via
 * useOutletContext from `useTestPlanContext` below.
 *
 * v3 alpha.14 IA split: /test-plans/:id/plan, /runs, /activity. Old
 * /test-plans/:id index redirects to /plan.
 *
 * 5.313.0 — no approval and no per-plan keys. A plan is a record of intent
 * and results that you or your agent write and work: a draft with entries
 * can be worked straight away (execution moves it to in progress). "Work
 * with your agent" hands the plan to the operator's one agent session
 * (AgentTaskButton); resuming or renewing an agent happens on its session
 * (OwningSessionLink), never here.
 */
import { formatTimestamp } from '../../utils/relativeTime';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  NavLink,
  Outlet,
  useMatch,
  useNavigate,
  useOutletContext,
  useParams,
  useResolvedPath,
} from 'react-router-dom';
import {
  AlertTriangle,
  CircleSlash,
  ClipboardCheck,
  FileDown,
  FileUp,
  Loader2,
  Pencil,
  Trash2,
} from 'lucide-react';
import {
  archiveTestPlan,
  deleteTestPlan,
  downloadTestPlanBundle,
  ExecutionSessionSummary,
  getTestPlan,
  getTestPlanProgress,
  importTestPlanResults,
  ImportResultsResponse,
  listExecutionSessions,
  TestPlanDetail as TestPlanDetailType,
  TestPlanProgress,
  updateTestPlanMetadata,
} from '../../services/api';
import AgentTaskButton from '../../components/agent-sessions/AgentTaskButton';
import OwningSessionLink from '../../components/agent-sessions/OwningSessionLink';
import { agentInstruction } from '../../utils/agentRuns';
import { DetailSkeleton } from '../../components/PageSkeleton';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { useReportDownload } from '../../hooks/useReportDownload';
import { asAxiosError, formatApiError } from '../../utils/apiErrors';
import { formatStatusLabel } from '../../utils/statusMeta';
import { Alert, AlertDescription } from '../../components/ui/alert';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardContent } from '../../components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog';
import { ConfirmDialog } from '../../components/ui/confirm-dialog';
import { WorkflowDetailHeader } from '../../components/workflow/WorkflowDetailHeader';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../components/ui/select';
import { Textarea } from '../../components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '../../components/ui/tooltip';
import { cn } from '../../utils/cn';

type Tone = 'default' | 'success' | 'warning' | 'destructive' | 'info' | 'muted' | 'secondary' | 'outline';

const planStatusTone = (status: string | null | undefined): Tone => {
  switch (status) {
    case 'draft':
      return 'info';
    case 'in_progress':
      return 'warning';
    case 'completed':
      return 'success';
    case 'archived':
    default:
      return 'muted';
  }
};

const PlanMetaItem: React.FC<{
  label: string;
  value: string | null | undefined;
  fallback?: string;
}> = ({ label, value, fallback = '—' }) => {
  const hasValue = value != null && value !== '';
  return (
    <div className="min-w-36 max-w-64">
      <p className="text-micro uppercase tracking-wider text-muted-foreground">{label}</p>
      <p
        className={cn(
          'break-words text-metadata font-medium',
          !hasValue && 'italic text-muted-foreground',
        )}
      >
        {hasValue ? value : fallback}
      </p>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Context shared with sub-tabs
// ---------------------------------------------------------------------------

export interface TestPlanContext {
  planId: number;
  plan: TestPlanDetailType;
  progress: TestPlanProgress | null;
  allSessions: ExecutionSessionSummary[] | null;
  sessionsLoading: boolean;
  /** FBK·H10: surfaced so sub-tabs (RunsTab) can render a non-blocking
   *  Alert when the secondary list-sessions fetch fails — previously
   *  the error was swallowed and the user just saw a missing picker. */
  sessionsError: string | null;
  selectedSessionId: number | null;
  setSelectedSessionId: (id: number | null) => void;
  canManage: boolean;
  reload: () => Promise<void>;
  /** v2.85.0 — append the next page of entries to ``plan.entries``.
   *  No-op when ``plan.entries.length >= plan.entries_total``.  Resolves
   *  once the append is committed; rejects on fetch failure so the
   *  caller can render an error toast. */
  loadMoreEntries: () => Promise<void>;
  /** v2.85.0 — true while ``loadMoreEntries`` is in flight; PlanTab uses
   *  it to disable the load-more button + show a spinner. */
  isLoadingMoreEntries: boolean;
  openReportDialog: () => void;
  /** Opens the DELETE-confirm dialog.  Exposed so the /danger sub-tab
   *  can trigger it from a card-level Delete button without owning the
   *  dialog state itself. */
  openDeleteDialog: () => void;
}

export function useTestPlanContext(): TestPlanContext {
  return useOutletContext<TestPlanContext>();
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

/**
 * Audit FRX·L1 — tab strip uses role="tablist" + role="tab", so each
 * tab MUST carry aria-selected.  NavLink only sets aria-current, so
 * this wrapper computes isActive via useMatch and stamps both.
 */
interface TabNavLinkProps {
  to: string;
  tabClass: (isActive: boolean) => string;
  children: React.ReactNode;
}

const TabNavLink: React.FC<TabNavLinkProps> = ({ to, tabClass, children }) => {
  const resolved = useResolvedPath(to);
  const match = useMatch({ path: resolved.pathname, end: false });
  const isActive = match !== null;
  // v2.43.0 — UX review #1: this is route navigation, not a WAI-ARIA tabset.
  // role="tab" + aria-selected promised roving tabIndex, arrow-key nav,
  // aria-controls, and tabpanel relationships that were never implemented.
  // Downgrading to semantic <nav> + NavLink + aria-current="page" gives
  // assistive tech the correct mental model and the right keyboard
  // semantics (it's links — Tab cycles them; Enter/click activates).
  return (
    <NavLink to={to} aria-current={isActive ? 'page' : undefined} className={tabClass(isActive)}>
      {children}
    </NavLink>
  );
};

// v4.52.0 — split into two page sizes.  Initial fetch is small (50)
// so first paint of a plan with thousands of entries shows the page
// chrome + the first slice fast; subsequent "Load more" clicks pull
// the larger (200) chunk so each round-trip amortizes well.  Pre-fix
// both used 200, which made the initial-load cost on big plans the
// dominant factor in time-to-first-paint.  Match
// LOAD_MORE_ENTRIES_PAGE_SIZE on PlanTab's "Load more" rendering.
const INITIAL_ENTRIES_PAGE_SIZE = 50;
const LOAD_MORE_ENTRIES_PAGE_SIZE = 200;

const TestPlanLayout: React.FC = () => {
  const { planId } = useParams<{ planId: string }>();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const toast = useToast();
  const canManage = hasPermission('analyst');
  const id = Number(planId);

  const [plan, setPlan] = useState<TestPlanDetailType | null>(null);
  const [progress, setProgress] = useState<TestPlanProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null);
  const [allSessions, setAllSessions] = useState<ExecutionSessionSummary[] | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  // FBK·H10: surface listExecutionSessions failures rather than silently
  // setAllSessions(null) — the picker just vanishes otherwise.
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveReason, setArchiveReason] = useState('');

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');

  const [bundleLoading, setBundleLoading] = useState(false);

  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<ImportResultsResponse | null>(null);
  // v2.43.0 — UX review #3: ref used by the focusable Choose-File button
  // to drive the hidden <input type="file"> from a real keyboard target.
  const importInputRef = useRef<HTMLInputElement | null>(null);

  const [editPlanOpen, setEditPlanOpen] = useState(false);
  const [editPlanTitle, setEditPlanTitle] = useState('');
  const [editPlanDescription, setEditPlanDescription] = useState('');
  const [savingPlanMeta, setSavingPlanMeta] = useState(false);

  const report = useReportDownload(id);

  const loadPlan = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    // Audit PRF·M9: previously plan + progress fired in parallel via
    // Promise.all, but listExecutionSessions ran in a second effect
    // gated on `plan` resolving — costing a full extra round-trip
    // before the picker could render.  Fire all three in parallel and
    // settle independently so a slow / failing sessions endpoint
    // doesn't sink the whole page (and the per-section sessionsError
    // surface added for FBK·H10 still works).
    setSessionsLoading(true);
    setSessionsError(null);
    const [planRes, progressRes, sessionsRes] = await Promise.allSettled([
      // v2.85.0 — request the first page of entries server-side so a
      // 5000-entry plan no longer ships its entire entry array on the
      // initial load.  PlanTab's "Load more" hits loadMoreEntries() to
      // append subsequent pages.  Page size is generous so most plans
      // fit on page 1 and the affordance only appears for the long tail.
      getTestPlan(id, { entriesLimit: INITIAL_ENTRIES_PAGE_SIZE }),
      getTestPlanProgress(id),
      listExecutionSessions(id),
    ]);
    try {
      if (planRes.status === 'rejected') throw planRes.reason;
      const planData = planRes.value;
      setPlan(planData);
      if (progressRes.status === 'fulfilled') {
        setProgress(progressRes.value);
      } else {
        console.error('Failed to load test plan progress:', progressRes.reason);
      }
      // Sessions: only meaningful when there's more than one execution
      // session for the plan — otherwise the picker would just show
      // the single session that's already rendered inline by the
      // RunsTab.  Mirrors the prior gated-effect behaviour.
      if (
        planData.execution_session_count &&
        planData.execution_session_count > 1 &&
        sessionsRes.status === 'fulfilled'
      ) {
        setAllSessions(sessionsRes.value.sessions);
      } else if (
        planData.execution_session_count &&
        planData.execution_session_count > 1 &&
        sessionsRes.status === 'rejected'
      ) {
        console.error('Failed to load execution sessions:', sessionsRes.reason);
        setAllSessions(null);
        // FBK·H10: surface to RunsTab via context instead of silently
        // dropping back to "no picker".
        setSessionsError(
          formatApiError(sessionsRes.reason, 'Could not load other execution sessions.'),
        );
      } else {
        setAllSessions(null);
      }
    } catch (err: unknown) {
      console.error('Failed to load test plan:', err);
      setError(formatApiError(err, 'Failed to load test plan.'));
    } finally {
      setLoading(false);
      setSessionsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadPlan();
  }, [loadPlan]);

  // v2.85.0 — append the next page of entries.  Reads the current
  // length off the plan state (rather than threading it through args)
  // so concurrent calls fold correctly: the second call's `skip` lands
  // *after* the first append commits.
  const [isLoadingMoreEntries, setIsLoadingMoreEntries] = useState(false);
  const loadMoreEntries = useCallback(async () => {
    if (!plan || isLoadingMoreEntries) return;
    const loaded = plan.entries.length;
    const total = plan.entries_total ?? loaded;
    if (loaded >= total) return;
    setIsLoadingMoreEntries(true);
    try {
      const next = await getTestPlan(id, {
        entriesSkip: loaded,
        entriesLimit: LOAD_MORE_ENTRIES_PAGE_SIZE,
      });
      // Merge: keep the existing slice (so optimistic edits aren't
      // clobbered) and append the new page.  Server-side ordering is
      // by id asc, so dedup by id defensively in case the user fired
      // two clicks before the first request committed.
      setPlan((prev) => {
        if (!prev) return next;
        const seen = new Set(prev.entries.map((e) => e.id));
        const fresh = next.entries.filter((e) => !seen.has(e.id));
        return {
          ...prev,
          entries: [...prev.entries, ...fresh],
          entries_total: next.entries_total,
        };
      });
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to load more entries.');
      toast.error(message);
    } finally {
      setIsLoadingMoreEntries(false);
    }
  }, [plan, isLoadingMoreEntries, id, toast]);

  const handleArchive = async () => {
    setActionLoading(true);
    try {
      await archiveTestPlan(id, archiveReason || undefined);
      setArchiveOpen(false);
      setArchiveReason('');
      await loadPlan();
      toast.info('Plan abandoned.');
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to abandon plan.');
      setError(message);
      toast.error(message);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    setActionLoading(true);
    try {
      await deleteTestPlan(id);
      toast.success('Test plan deleted.');
      navigate('/test-plans');
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to delete test plan.');
      setError(message);
      toast.error(message);
      setActionLoading(false);
      setDeleteOpen(false);
    }
  };

  const openEditPlanDialog = () => {
    if (!plan) return;
    setEditPlanTitle(plan.title);
    setEditPlanDescription(plan.description || '');
    setEditPlanOpen(true);
  };

  const handleSavePlanMetadata = async () => {
    if (!plan) return;
    setSavingPlanMeta(true);
    try {
      await updateTestPlanMetadata(plan.id, {
        title: editPlanTitle,
        description: editPlanDescription,
      });
      toast.success('Test plan updated.');
      setEditPlanOpen(false);
      await loadPlan();
    } catch (err: unknown) {
      toast.error(formatApiError(err, 'Failed to update test plan.'));
    } finally {
      setSavingPlanMeta(false);
    }
  };

  const handleImportResults = async () => {
    if (!importFile) return;
    setImportLoading(true);
    setImportError(null);
    setImportResult(null);
    try {
      const result = await importTestPlanResults(id, importFile);
      setImportResult(result);
      toast.success(
        `Imported ${result.results_imported} result(s) and ${result.sanity_checks_imported} sanity check(s).`,
      );
      await loadPlan();
    } catch (err: unknown) {
      setImportError(formatApiError(err, 'Failed to import results.'));
    } finally {
      setImportLoading(false);
    }
  };

  const handleExportBundle = async () => {
    setBundleLoading(true);
    try {
      const { bundleId } = await downloadTestPlanBundle(id);
      toast.success(`Bundle downloaded (bundle_id: ${bundleId.slice(0, 12)}…).`);
      await loadPlan();
    } catch (err: unknown) {
      let msg = formatApiError(err, 'Failed to export test plan bundle.');
      const blob = asAxiosError(err).response?.data;
      if (blob instanceof Blob && blob.type?.includes('json')) {
        try {
          const text = await blob.text();
          const parsed = JSON.parse(text);
          if (parsed?.detail) msg = parsed.detail;
        } catch {
          /* ignore */
        }
      }
      toast.error(msg);
    } finally {
      setBundleLoading(false);
    }
  };

  if (loading) {
    return <DetailSkeleton />;
  }

  if (!plan) {
    return (
      <div className="p-md md:p-lg">
        <Alert variant="destructive">
          <AlertDescription>{error || 'Test plan not found'}</AlertDescription>
        </Alert>
      </div>
    );
  }

  // v2.85.0 — derive totals from server-side counts so partial-page
  // states stay correct.  ``plan.entries_total`` is populated whenever
  // the detail endpoint paginates; ``progress.by_status`` is the
  // authoritative source for per-status counts (computed server-side
  // in one GROUP BY against all entries, not just the loaded page).
  const totalEntries = plan.entries_total ?? plan.entries.length;
  const proposedCount = progress?.by_status?.proposed ?? 0;
  const dispositionedCount =
    progress != null
      ? Math.max(totalEntries - proposedCount, 0)
      : plan.entries.filter((e) => e.status !== 'proposed').length;
  const hasDispositions = dispositionedCount > 0;
  const deleteCanProceed = !hasDispositions || deleteConfirmText === 'DELETE';

  const tabClass = (active: boolean) =>
    cn(
      'border-b-2 px-md py-xs text-metadata font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
      active
        ? 'border-primary text-foreground'
        : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border',
    );

  const openDeleteDialog = () => {
    setDeleteConfirmText('');
    setDeleteOpen(true);
  };

  const context: TestPlanContext = {
    planId: id,
    plan,
    progress,
    allSessions,
    sessionsLoading,
    sessionsError,
    selectedSessionId,
    setSelectedSessionId,
    canManage,
    reload: loadPlan,
    loadMoreEntries,
    isLoadingMoreEntries,
    openReportDialog: report.openDialog,
    openDeleteDialog,
  };

  // A plan is worked once it has entries: a draft (execution moves it to in
  // progress) or a plan already in progress. Nothing waits on an approval.
  const workable =
    (plan.status === 'draft' && totalEntries > 0) || plan.status === 'in_progress';

  return (
    <div className="p-md md:p-lg">
      <WorkflowDetailHeader
        onBack={() => navigate('/test-plans')}
        backLabel="Back to test plans"
        title={plan.title}
        titleAdornment={
          plan.status !== 'archived' ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={openEditPlanDialog}
                  aria-label="Edit plan name and description"
                >
                  <Pencil className="size-4" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Edit plan name / description</TooltipContent>
            </Tooltip>
          ) : null
        }
        badges={
          <>
            <Badge variant={planStatusTone(plan.status)}>{formatStatusLabel(plan.status)}</Badge>
            {/* Drafting staleness — the backend decides the predicate so it
                can't drift against the browser clock. Resuming is done on the
                agent's session (5.313.0), not on the plan. */}
            {plan.is_stale && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="warning" className="cursor-help">
                    Possibly interrupted
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  This draft has no entries and no agent activity for 15+ minutes — its agent
                  may have stopped. Resume its agent session to continue.
                </TooltipContent>
              </Tooltip>
            )}
          </>
        }
        subtitle={
          plan.description ? (
            <span className="block whitespace-pre-wrap break-words">{plan.description}</span>
          ) : undefined
        }
        actions={
          canManage ? (
            <>
              {plan.agent_session_id != null && (
                <OwningSessionLink agentSessionId={plan.agent_session_id} />
              )}
              {workable && (
                <AgentTaskButton
                  variant="default"
                  label="Work with your agent"
                  instruction={agentInstruction.workPlan(plan.id)}
                  disabled={actionLoading}
                />
              )}
              {workable && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleExportBundle}
                  disabled={actionLoading || bundleLoading}
                >
                  {bundleLoading ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <FileDown className="size-4" aria-hidden />
                  )}
                  Export Bundle
                </Button>
              )}
              {(plan.status === 'in_progress' || plan.status === 'completed') && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    setImportFile(null);
                    setImportError(null);
                    setImportResult(null);
                    setImportOpen(true);
                  }}
                  disabled={actionLoading}
                >
                  <FileUp className="size-4" aria-hidden /> Import Results
                </Button>
              )}
              {(plan.status === 'in_progress' || plan.status === 'completed') && (
                <Button size="sm" variant="outline" onClick={report.openDialog} disabled={actionLoading}>
                  <ClipboardCheck className="size-4" aria-hidden /> Generate Report
                </Button>
              )}
            </>
          ) : null
        }
        destructiveAction={
          // Abandon — non-destructive terminal exit for any NON-terminal
          // plan (draft / in_progress), matching the backend's archive_plan
          // guard.  Pinned to the far right of the action bar; Delete lives
          // behind the Manage tab.
          canManage &&
          (plan.status === 'draft' || plan.status === 'in_progress') ? (
            <Button
              size="sm"
              variant="warning-outline"
              onClick={() => setArchiveOpen(true)}
              disabled={actionLoading}
            >
              <CircleSlash className="size-4" aria-hidden /> Abandon
            </Button>
          ) : null
        }
      />

      <Card className="mb-sm">
        <CardContent className="p-sm">
          <p className="mb-xs text-micro uppercase tracking-wider font-semibold text-muted-foreground">
            Plan details
          </p>
          <div className="flex flex-wrap gap-x-lg gap-y-sm">
            <PlanMetaItem label="Version" value={`v${plan.version}`} />
            <PlanMetaItem label="Author" value={plan.agent_name || plan.created_by_username} />
            <PlanMetaItem label="Created" value={formatTimestamp(plan.created_at)} />
            <PlanMetaItem label="Last updated" value={formatTimestamp(plan.updated_at)} />
            <PlanMetaItem label="Agent tool" value={plan.generated_by_tool} fallback="not recorded" />
            <PlanMetaItem label="Model" value={plan.generated_by_model} fallback="not recorded" />
            <PlanMetaItem
              label="Prompt version"
              value={plan.prompt_version ? `v${plan.prompt_version}` : null}
              fallback="not recorded"
            />
            {plan.completed_at && (
              <PlanMetaItem label="Completed" value={formatTimestamp(plan.completed_at)} />
            )}
          </div>

          {plan.filter_criteria &&
            Object.values(plan.filter_criteria).some(
              (v) => v !== null && v !== undefined && v !== '' && v !== false,
            ) && (
              <div className="mt-sm">
                <p className="mb-xxs text-micro uppercase tracking-wider font-semibold text-muted-foreground">
                  Selection filters applied at generation
                </p>
                <div className="flex flex-wrap gap-xs">
                  {plan.filter_criteria.subnets && (
                    <Badge variant="outline">Subnets: {plan.filter_criteria.subnets}</Badge>
                  )}
                  {plan.filter_criteria.ports && (
                    <Badge variant="outline">Ports: {plan.filter_criteria.ports}</Badge>
                  )}
                  {plan.filter_criteria.services && (
                    <Badge variant="outline">Services: {plan.filter_criteria.services}</Badge>
                  )}
                  {plan.filter_criteria.min_severity && (
                    <Badge
                      variant={
                        plan.filter_criteria.min_severity === 'critical'
                          ? 'destructive'
                          : plan.filter_criteria.min_severity === 'high'
                          ? 'warning'
                          : 'outline'
                      }
                    >
                      Min severity: {plan.filter_criteria.min_severity}
                    </Badge>
                  )}
                  {plan.filter_criteria.has_critical_vulns && (
                    <Badge variant="destructive">Only critical vulnerabilities</Badge>
                  )}
                  {plan.filter_criteria.has_high_vulns && (
                    <Badge variant="warning">Only high vulnerabilities</Badge>
                  )}
                  {plan.filter_criteria.search && (
                    <Badge variant="outline">Search: {plan.filter_criteria.search}</Badge>
                  )}
                </div>
                <p className="mt-xxs text-caption text-muted-foreground">
                  All chips above applied together (AND); commas within a chip are alternatives (OR).
                </p>
              </div>
            )}

          {/* 5.313.1 — a plan drafted from a recon run keeps its old
              source_kind, but runs are gone and there is nothing to show. */}
          {plan.source_kind && plan.source_kind !== 'unspecified' && plan.source_kind !== 'recon_session' && (
            <div className="mt-sm">
              <p className="mb-xxs text-micro uppercase tracking-wider font-semibold text-muted-foreground">
                Source provenance
              </p>
              <div className="flex flex-wrap gap-xs">
                {plan.source_kind === 'manual_hosts' && (
                  <Badge variant="outline">
                    {plan.source_host_ids?.length
                      ? `From ${plan.source_host_ids.length} manual host${plan.source_host_ids.length === 1 ? '' : 's'}`
                      : 'From manual host selection'}
                  </Badge>
                )}
                {plan.source_kind === 'filter_set' && (
                  <Badge variant="outline">From a filter expression</Badge>
                )}
                {plan.source_kind === 'inherited' && plan.source_plan_id && (
                  <button
                    type="button"
                    onClick={() => navigate(`/test-plans/${plan.source_plan_id}`)}
                    className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Badge variant="outline" className="cursor-pointer">
                      Derived from plan #{plan.source_plan_id}
                    </Badge>
                  </button>
                )}
              </div>
            </div>
          )}

        </CardContent>
      </Card>

      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {plan.new_hosts_since_creation > 0 && (
        <Alert variant="warning" className="mb-sm">
          <AlertDescription className="flex items-start gap-xs">
            <AlertTriangle className="mt-xxs size-4 shrink-0" aria-hidden />
            <span>
              {plan.new_hosts_since_creation} new host
              {plan.new_hosts_since_creation !== 1 ? 's' : ''} discovered since this plan was created.
              Consider updating the plan.
            </span>
          </AlertDescription>
        </Alert>
      )}

      {/* Lifecycle actions (Work with your agent / Export / Import / Report)
          and Abandon live in the WorkflowDetailHeader action bar above.
          Delete lives behind the Manage tab. */}

      {plan.archive_reason && (
        <Alert variant="info" className="mb-sm">
          <AlertDescription>
            <p className="font-semibold">Archived because</p>
            <p className="break-words">{plan.archive_reason}</p>
          </AlertDescription>
        </Alert>
      )}

      {/* v2.43.0 — UX review #1: dropped role="tablist".  This is route
          navigation, so <nav aria-label> + NavLink + aria-current="page"
          is the honest semantics.  See TabNavLink for the per-link
          treatment. */}
      <div className="mb-sm border-b border-border">
        <nav className="-mb-px flex flex-wrap" aria-label="Test plan sections">
          {/* Audit FRX·L1 (superseded by v2.43.0 UX #1): each NavLink now
              stamps aria-current="page" when active.  TabNavLink computes
              isActive once via useMatch and feeds both the class callback
              and the aria-current attribute. */}
          <TabNavLink to="plan" tabClass={tabClass}>
            {totalEntries > 0
              ? `Plan structure (${dispositionedCount}/${totalEntries})`
              : 'Plan structure'}
          </TabNavLink>
          <TabNavLink to="runs" tabClass={tabClass}>
            {(plan.execution_session_count ?? 0) > 0
              ? `Executions (${plan.execution_session_count})`
              : 'Executions'}
          </TabNavLink>
          <TabNavLink to="activity" tabClass={tabClass}>
            Agent activity
          </TabNavLink>
          {/* FRX·L4: "API calls" overlapped with "Agent activity" —
              the tab strip presented two near-identical surfaces.
              Renamed to "Writes" so the differentiator (write-mode
              preset filter inside ApiCallsTab) is reflected in the
              label.  Route path stays `/api-calls` for bookmark
              stability and direct-link compatibility. */}
          <TabNavLink to="api-calls" tabClass={tabClass}>
            Writes
          </TabNavLink>
          <TabNavLink
            to="danger"
            tabClass={(isActive) =>
              cn(
                tabClass(isActive),
                'ml-auto',
                // FRX·H3: route path stays `/danger` for bookmark
                // stability; only the visible label is "Manage" so the
                // tab no longer reads as a no-touch zone.  Visual
                // weight still shifts to destructive when the plan
                // has been worked on (audit H10).
                hasDispositions || plan.status === 'in_progress'
                  ? 'text-destructive font-semibold hover:text-destructive'
                  : 'text-muted-foreground hover:text-foreground',
              )
            }
          >
            Manage
            {(hasDispositions || plan.status === 'in_progress') && (
              <span className="ml-xxs">●</span>
            )}
          </TabNavLink>
        </nav>
      </div>

      <Outlet context={context} />

      {/* Abandon dialog */}
      <ConfirmDialog
        open={archiveOpen}
        onOpenChange={setArchiveOpen}
        busy={actionLoading}
        titleIcon={<CircleSlash className="size-5 text-warning" aria-hidden />}
        title="Abandon Test Plan"
        description={
          <>
            Moves the plan to <strong>archived</strong> — a terminal, non-destructive state. The
            plan, its entries, and any execution results are kept for the audit trail, but it leaves
            the active queue. Use this for plans that are no longer relevant (Delete, by contrast,
            removes everything permanently).
          </>
        }
        reason={{ value: archiveReason, onChange: setArchiveReason }}
        confirmLabel="Abandon plan"
        confirmIcon={<CircleSlash className="size-4" aria-hidden />}
        confirmVariant="warning"
        onConfirm={handleArchive}
      />

      {/* Delete dialog */}
      <Dialog open={deleteOpen} onOpenChange={(v) => !v && !actionLoading && setDeleteOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-xs">
              <Trash2 className="size-5 text-destructive" aria-hidden />
              Delete Test Plan
            </DialogTitle>
            <DialogDescription>
              Permanently removes the plan and all its entries, executions, and history.
              This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <p className="text-metadata">
            You are about to permanently delete <strong>{plan.title}</strong> and all of its
            entries and history. This cannot be undone.
          </p>
          {hasDispositions ? (
            <>
              {/* Audit M15: the Input used to live inside the
                  Alert, where the destructive red surface drowned
                  out the actual "Type DELETE" instruction.  Split:
                  Alert keeps the warning copy, Input lives below in
                  its own labelled block. */}
              <Alert variant="destructive">
                <AlertDescription>
                  <p className="mb-xxs font-semibold">
                    {dispositionedCount} of {totalEntries} entr
                    {dispositionedCount === 1 ? 'y has' : 'ies have'} already been reviewed.
                  </p>
                  <p>
                    Normally a partially-reviewed plan is kept unless the agent went off-topic and
                    the work has to be discarded. Make sure this is the right plan before continuing.
                  </p>
                </AlertDescription>
              </Alert>
              <div className="space-y-xxs">
                <label
                  htmlFor="delete-confirm-input"
                  className="text-metadata font-semibold text-foreground"
                >
                  Type <code className="font-mono">DELETE</code> to confirm
                </label>
                <Input
                  id="delete-confirm-input"
                  value={deleteConfirmText}
                  onChange={(e) => setDeleteConfirmText(e.target.value)}
                  autoFocus
                  placeholder="DELETE"
                  disabled={actionLoading}
                />
              </div>
            </>
          ) : (
            <Alert variant="info">
              <AlertDescription>
                No entries on this plan have been reviewed yet, so nothing dispositioned will be
                lost.
              </AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteOpen(false)} disabled={actionLoading}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={actionLoading || !deleteCanProceed}
            >
              {actionLoading ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Trash2 className="size-4" aria-hidden />
              )}
              Delete Plan
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Generate Report dialog */}
      <Dialog open={report.open} onOpenChange={(v) => !v && report.closeDialog()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-xs">
              <ClipboardCheck className="size-5 text-primary" aria-hidden />
              Generate Execution Report
            </DialogTitle>
            <DialogDescription>
              Download the most recent execution session for this plan as a report
              (per-host results, sanity-check outcomes, agent-recorded findings) in
              your preferred format.
            </DialogDescription>
          </DialogHeader>
          <p className="text-metadata text-muted-foreground">
            Downloads a report for the most recent execution session of this plan, including
            per-host sanity checks, test results, and findings.
          </p>
          <div>
            <Label htmlFor="report-format">Format</Label>
            <Select
              value={report.format}
              onValueChange={(v) => report.setFormat(v as typeof report.format)}
            >
              <SelectTrigger id="report-format" disabled={report.loading}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="html">HTML (viewable in browser)</SelectItem>
                <SelectItem value="json">JSON (structured data)</SelectItem>
                <SelectItem value="csv">CSV (spreadsheet)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {report.error && (
            <Alert variant="destructive">
              <AlertDescription>{report.error}</AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={report.closeDialog} disabled={report.loading}>
              Cancel
            </Button>
            <Button onClick={report.download} disabled={report.loading}>
              {report.loading ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <ClipboardCheck className="size-4" aria-hidden />
              )}
              Download
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit plan metadata dialog */}
      <Dialog
        open={editPlanOpen}
        onOpenChange={(v) => !v && !savingPlanMeta && setEditPlanOpen(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Test Plan</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-sm">
            <div>
              <Label htmlFor="edit-plan-title">Title</Label>
              <Input
                id="edit-plan-title"
                value={editPlanTitle}
                onChange={(e) => setEditPlanTitle(e.target.value)}
                autoFocus
              />
            </div>
            <div>
              <Label htmlFor="edit-plan-desc">Description</Label>
              <Textarea
                id="edit-plan-desc"
                value={editPlanDescription}
                onChange={(e) => setEditPlanDescription(e.target.value)}
                rows={4}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditPlanOpen(false)} disabled={savingPlanMeta}>
              Cancel
            </Button>
            <Button
              onClick={handleSavePlanMetadata}
              disabled={savingPlanMeta || !editPlanTitle.trim()}
            >
              {savingPlanMeta ? <Loader2 className="size-4 animate-spin" aria-hidden /> : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import Results dialog */}
      <Dialog
        open={importOpen}
        onOpenChange={(v) => !v && !importLoading && setImportOpen(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-xs">
              <FileUp className="size-5 text-primary" aria-hidden />
              Import Remote Agent Results
            </DialogTitle>
            <DialogDescription>
              Upload a <code>results.json</code> produced by a remote agent that executed
              the previously-exported bundle for this plan. The bundle_id inside the file
              must match an exported execution session.
            </DialogDescription>
          </DialogHeader>
          {!importResult ? (
            <>
              <p id="import-file-help" className="text-metadata text-muted-foreground">
                Upload the <code className="font-mono">results.json</code> file produced by a
                remote agent that executed the previously-exported bundle. The file is matched to
                the correct execution session by its <strong>bundle_id</strong> — make sure you're
                uploading a file for this plan.
              </p>
              <div>
                {/* v2.43.0 — UX review #3: pre-fix the picker trigger was
                    a styled <span> inside a <label>; keyboard users had no
                    visible focusable target, screen readers announced
                    "blank".  Now a real <Button> drives the hidden input
                    via inputRef, with explicit focus styles + an
                    aria-describedby hookup to the helper paragraph above. */}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => importInputRef.current?.click()}
                  disabled={importLoading}
                  aria-describedby="import-file-help"
                  className="gap-xs"
                >
                  <FileUp className="size-4" aria-hidden />
                  {importFile ? `Selected: ${importFile.name}` : 'Choose results.json'}
                </Button>
                <input
                  ref={importInputRef}
                  id="import-file"
                  type="file"
                  className="sr-only"
                  accept="application/json,.json"
                  onChange={(e) => setImportFile(e.target.files?.[0] || null)}
                  disabled={importLoading}
                  tabIndex={-1}
                  aria-hidden="true"
                />
              </div>
              {importError && (
                <Alert variant="destructive">
                  <AlertDescription>{importError}</AlertDescription>
                </Alert>
              )}
            </>
          ) : (
            <div className="flex flex-col gap-xs">
              <Alert variant="success">
                <AlertDescription>
                  Imported successfully. Session now: <strong>{importResult.session_status}</strong>;
                  plan now: <strong>{importResult.plan_status}</strong>.
                </AlertDescription>
              </Alert>
              <p className="text-metadata">
                <strong>Results imported:</strong> {importResult.results_imported}
                <br />
                <strong>Sanity checks imported:</strong> {importResult.sanity_checks_imported}
                <br />
                <strong>Feedback extracted:</strong> {importResult.feedback_extracted ? 'yes' : 'no'}
                <br />
                <strong>Final import:</strong> {importResult.is_final ? 'yes' : 'no (interim)'}
              </p>
              {importResult.parse_errors.length > 0 && (
                <Alert variant="warning">
                  <AlertDescription>
                    <p className="mb-xxs font-semibold">
                      Parse warnings ({importResult.parse_errors.length}):
                    </p>
                    <ul className="max-h-40 overflow-auto pl-md">
                      {importResult.parse_errors.map((e, i) => (
                        <li key={i} className="text-caption">
                          {e}
                        </li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
            </div>
          )}
          <DialogFooter>
            {!importResult ? (
              <>
                <Button variant="outline" onClick={() => setImportOpen(false)} disabled={importLoading}>
                  Cancel
                </Button>
                <Button onClick={handleImportResults} disabled={!importFile || importLoading}>
                  {importLoading ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <FileUp className="size-4" aria-hidden />
                  )}
                  Upload
                </Button>
              </>
            ) : (
              <Button onClick={() => setImportOpen(false)}>Done</Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  );
};

export default TestPlanLayout;
