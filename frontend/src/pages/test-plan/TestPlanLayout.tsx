/**
 * TestPlanLayout — parent shell for the routed test-plan sub-tabs.
 *
 * Owns: data fetch (plan + progress + sessions), header, metadata
 * panel, action buttons, dialogs (reject / delete / execute / report /
 * edit / import), tab nav.  Sub-tabs receive the shared state via
 * useOutletContext from `useTestPlanContext` below.
 *
 * v3 alpha.14 IA split: /test-plans/:id/plan, /runs, /activity. Old
 * /test-plans/:id index redirects to /plan.
 */
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
  Check,
  CheckCircle2,
  CircleSlash,
  ClipboardCheck,
  Copy,
  ExternalLink,
  FileDown,
  FileUp,
  Loader2,
  Pencil,
  Play,
  RotateCcw,
  Trash2,
  XCircle,
} from 'lucide-react';
import {
  approveTestPlan,
  archiveTestPlan,
  deleteTestPlan,
  downloadTestPlanBundle,
  ExecuteResponse,
  executeTestPlan,
  GeneratePlanResponse,
  resumeExecutionSession,
  resumePlanGeneration,
  ExecutionSessionSummary,
  getTestPlan,
  getTestPlanProgress,
  importTestPlanResults,
  ImportResultsResponse,
  listExecutionSessions,
  rejectTestPlan,
  rotateTestPlanKey,
  TestPlanDetail as TestPlanDetailType,
  TestPlanProgress,
  updateTestPlanMetadata,
} from '../../services/api';
import InAppAgentPanel from '../../components/InAppAgentPanel';
import { NextStepBanner } from '../../components/NextStepBanner';
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
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/dialog';
import { ConfirmDialog } from '../../components/ui/confirm-dialog';
import { WorkflowDetailHeader } from '../../components/workflow/WorkflowDetailHeader';
import { Checkbox } from '../../components/ui/checkbox';
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
      return 'muted';
    case 'proposed':
      return 'info';
    case 'approved':
      return 'default';
    case 'in_progress':
      return 'warning';
    case 'completed':
      return 'success';
    case 'rejected':
      return 'destructive';
    case 'archived':
      return 'muted';
    default:
      return 'muted';
  }
};

const formatTimeLeft = (seconds: number | null | undefined): string => {
  if (seconds == null) return '';
  const abs = Math.abs(seconds);
  if (abs < 60) return `${Math.round(abs)}s`;
  if (abs < 3600) return `${Math.round(abs / 60)}m`;
  if (abs < 86400) {
    const h = Math.floor(abs / 3600);
    const m = Math.round((abs % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  const d = Math.floor(abs / 86400);
  const h = Math.round((abs % 86400) / 3600);
  return h > 0 ? `${d}d ${h}h` : `${d}d`;
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
  /** Resume an interrupted execution session — re-mints a fresh agent
   *  key for the SAME session and opens the agent-instructions dialog.
   *  Used by the Resume button on RunsTab. */
  handleResume: (sessionId: number, looksInterrupted: boolean) => void;
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
  const [newApiKey, setNewApiKey] = useState<string | null>(null);
  const [newApiKeyExpiresAt, setNewApiKeyExpiresAt] = useState<string | null>(null);
  // v4.7.5 — gate the regenerated-key banner's dismiss button on a
  // successful clipboard write.  Without this gate operators could
  // dismiss the only on-screen copy of a one-time secret before they
  // had it captured anywhere — the v2.45.1-era UI showed an optimistic
  // success toast that lied about whether the clipboard had received
  // the value (denied permission / insecure origin / RDP hardening
  // are all common failure modes for a security-team product).
  const [regeneratedKeyCopied, setRegeneratedKeyCopied] = useState(false);
  const [rotatingKey, setRotatingKey] = useState(false);

  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null);
  const [allSessions, setAllSessions] = useState<ExecutionSessionSummary[] | null>(null);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  // FBK·H10: surface listExecutionSessions failures rather than silently
  // setAllSessions(null) — the picker just vanishes otherwise.
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveReason, setArchiveReason] = useState('');

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');

  const [executeOpen, setExecuteOpen] = useState(false);
  const [executeResult, setExecuteResult] = useState<ExecuteResponse | null>(null);
  const [executeLoading, setExecuteLoading] = useState(false);
  const [executeError, setExecuteError] = useState<string | null>(null);
  // Non-null while the execute dialog is in "resume" mode — holds the
  // id of the interrupted session being resumed.  Drives the dialog's
  // resume-specific title/copy; null = a fresh /execute.
  const [resumeSessionId, setResumeSessionId] = useState<number | null>(null);
  // True when the session being resumed looks interrupted (stale/paused)
  // — drives reassuring vs. warning copy in the confirm dialog.
  const [resumeLooksInterrupted, setResumeLooksInterrupted] = useState(true);

  // Plan-generation resume state — parallel to the execution resume
  // above.  The dialog opens in confirm state; the POST fires only on
  // the operator's deliberate click; on success the dialog flips to
  // show the new api_key + instructions block.
  const [resumeGenOpen, setResumeGenOpen] = useState(false);
  const [resumeGenResult, setResumeGenResult] = useState<GeneratePlanResponse | null>(null);
  const [resumeGenLoading, setResumeGenLoading] = useState(false);
  const [resumeGenError, setResumeGenError] = useState<string | null>(null);
  const [resumeGenKeySaved, setResumeGenKeySaved] = useState(false);
  useEffect(() => {
    if (resumeGenResult?.api_key) {
      setResumeGenKeySaved(false);
    }
  }, [resumeGenResult?.api_key]);
  // Transient "Copied!" icon feedback (flips back to false after 1.5s).
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedInstructions, setCopiedInstructions] = useState(false);
  // FRX·H8: sticky flag — Done is gated on the operator having copied
  // the API key at least once during this dialog session.  The key is
  // shown only once, so closing the dialog without saving it elsewhere
  // is destructive.  Reset whenever a new key is shown.
  const [apiKeySaved, setApiKeySaved] = useState(false);
  useEffect(() => {
    if (executeResult?.api_key) {
      setApiKeySaved(false);
    }
  }, [executeResult?.api_key]);

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

  const handleApprove = async () => {
    setActionLoading(true);
    try {
      await approveTestPlan(id);
      await loadPlan();
      toast.success('Plan approved.');
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to approve plan.');
      setError(message);
      toast.error(message);
    } finally {
      setActionLoading(false);
    }
  };

  const handleRotateKey = async () => {
    setRotatingKey(true);
    try {
      const resp = await rotateTestPlanKey(id);
      setNewApiKey(resp.api_key);
      setNewApiKeyExpiresAt(resp.expires_at);
      // v4.7.5 — reset the copy-acknowledgement gate for the new key
      // so the dismiss button starts disabled (operator must copy
      // this freshly-minted key before the banner can close).
      setRegeneratedKeyCopied(false);
      await loadPlan();
      toast.success('New agent key issued.');
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to rotate agent key.');
      setError(message);
      toast.error(message);
    } finally {
      setRotatingKey(false);
    }
  };

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

  const handleReject = async () => {
    setActionLoading(true);
    try {
      await rejectTestPlan(id, rejectReason || undefined);
      setRejectOpen(false);
      setRejectReason('');
      await loadPlan();
      toast.info('Plan rejected.');
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to reject plan.');
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

  const handleExecute = async () => {
    setExecuteLoading(true);
    setExecuteError(null);
    try {
      const result = await executeTestPlan(id);
      setExecuteResult(result);
      toast.success('Execution session created.');
      await loadPlan();
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to start execution session.');
      setExecuteError(message);
      toast.error(message);
    } finally {
      setExecuteLoading(false);
    }
  };

  const openEditPlanDialog = () => {
    if (!plan) return;
    setEditPlanTitle(plan.title);
    setEditPlanDescription(plan.description || '');
    setEditPlanOpen(true);
  };

  // FRX·M9: shared between the action bar's "Execute with AI" button
  // and the NextStepBanner rendered for approved-but-not-yet-run plans.
  const openExecuteDialog = () => {
    setResumeSessionId(null);
    setExecuteResult(null);
    setExecuteError(null);
    setExecuteOpen(true);
  };

  // Resume opens the agent-instructions dialog in a confirm state — the
  // API call does NOT fire here.  Resuming re-mints the agent key and
  // REVOKES the current one, so an agent still running on the old key
  // would be cut off; the operator confirms (handleResumeConfirm) after
  // the dialog explains that.  `looksInterrupted` (stale/paused) selects
  // reassuring vs. warning copy.
  const handleResume = (sessionId: number, looksInterrupted: boolean) => {
    setResumeSessionId(sessionId);
    setResumeLooksInterrupted(looksInterrupted);
    setExecuteResult(null);
    setExecuteError(null);
    setExecuteOpen(true);
  };

  // Fires the resume request after the operator confirms in the dialog.
  const handleResumeConfirm = async () => {
    if (resumeSessionId == null) return;
    setExecuteLoading(true);
    setExecuteError(null);
    try {
      const result = await resumeExecutionSession(id, resumeSessionId);
      setExecuteResult(result);
      toast.success(`Execution session #${resumeSessionId} resumed.`);
      await loadPlan();
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to resume execution session.');
      setExecuteError(message);
      toast.error(message);
    } finally {
      setExecuteLoading(false);
    }
  };

  // Plan-generation resume opens the dialog in confirm state.  Resuming
  // re-mints the plan's agent key and revokes the prior one, so an
  // agent still running on the dead key will be cut off — confirm
  // before firing.
  const openResumeGenDialog = () => {
    setResumeGenResult(null);
    setResumeGenError(null);
    setResumeGenKeySaved(false);
    setResumeGenOpen(true);
  };

  const handleResumeGenConfirm = async () => {
    setResumeGenLoading(true);
    setResumeGenError(null);
    try {
      const result = await resumePlanGeneration(id);
      setResumeGenResult(result);
      toast.success('Plan generation resumed — fresh agent key minted.');
      await loadPlan();
    } catch (err: unknown) {
      const message = formatApiError(err, 'Failed to resume plan generation.');
      setResumeGenError(message);
      toast.error(message);
    } finally {
      setResumeGenLoading(false);
    }
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

  // Returns true iff the clipboard write succeeded.  Callers that gate
  // safety state (the Execute dialog's Done button, the regenerated-key
  // banner) MUST await this and only advance their state on true —
  // pre-v4.7.5 the callers fired their "saved" flag synchronously
  // alongside this call, so a failed clipboard write (denied
  // permission, insecure origin, remote-desktop hardening) silently
  // unlocked the safety gate.
  const handleCopy = async (
    text: string,
    setter: (v: boolean) => void,
  ): Promise<boolean> => {
    try {
      await navigator.clipboard.writeText(text);
      setter(true);
      setTimeout(() => setter(false), 1500);
      return true;
    } catch {
      toast.warning('Could not copy to clipboard.');
      return false;
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
    handleResume,
  };

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
            {/* Plan-generation staleness — the third leg of the
                agentic-workflow Resume affordance (parallel to recon and
                execution).  Backend decides the predicate so it can't
                drift against the browser clock. */}
            {plan.is_stale && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="warning" className="cursor-help">
                    Possibly interrupted
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  No plan-generation agent activity for 15+ minutes — this draft may have been
                  interrupted. Use Resume to re-issue a key and continue.
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
              {plan.is_stale && (
                <Button size="sm" variant="outline" onClick={openResumeGenDialog}>
                  <RotateCcw className="size-4" aria-hidden /> Resume
                </Button>
              )}
              {(plan.status === 'proposed' || plan.status === 'rejected') && (
                <Button size="sm" onClick={handleApprove} disabled={actionLoading}>
                  <CheckCircle2 className="size-4" aria-hidden /> Approve Plan
                </Button>
              )}
              {plan.status === 'proposed' && (
                <Button
                  size="sm"
                  variant="outline"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setRejectOpen(true)}
                  disabled={actionLoading}
                >
                  <XCircle className="size-4" aria-hidden /> Reject
                </Button>
              )}
              {(plan.status === 'approved' || plan.status === 'in_progress') && (
                <Button size="sm" onClick={openExecuteDialog} disabled={actionLoading}>
                  <Play className="size-4" aria-hidden /> Execute with AI
                </Button>
              )}
              {(plan.status === 'approved' || plan.status === 'in_progress') && (
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
          // plan (draft / proposed / approved / in_progress), matching the
          // backend's archive_plan guard.  Pinned to the far right of the
          // action bar; Delete lives behind the Manage tab.
          canManage &&
          (plan.status === 'draft' ||
            plan.status === 'proposed' ||
            plan.status === 'approved' ||
            plan.status === 'in_progress') ? (
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
            <PlanMetaItem label="Created" value={new Date(plan.created_at).toLocaleString()} />
            <PlanMetaItem label="Last updated" value={new Date(plan.updated_at).toLocaleString()} />
            <PlanMetaItem label="Agent tool" value={plan.generated_by_tool} fallback="not recorded" />
            <PlanMetaItem label="Model" value={plan.generated_by_model} fallback="not recorded" />
            <PlanMetaItem
              label="Prompt version"
              value={plan.prompt_version ? `v${plan.prompt_version}` : null}
              fallback="not recorded"
            />
            {plan.approved_at && (
              <PlanMetaItem label="Approved" value={new Date(plan.approved_at).toLocaleString()} />
            )}
            {plan.rejected_at && (
              <PlanMetaItem label="Rejected" value={new Date(plan.rejected_at).toLocaleString()} />
            )}
            {plan.completed_at && (
              <PlanMetaItem label="Completed" value={new Date(plan.completed_at).toLocaleString()} />
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

          {plan.source_kind && plan.source_kind !== 'unspecified' && (
            <div className="mt-sm">
              <p className="mb-xxs text-micro uppercase tracking-wider font-semibold text-muted-foreground">
                Source provenance
              </p>
              <div className="flex flex-wrap gap-xs">
                {plan.source_kind === 'recon_session' && plan.source_recon_session_id && (
                  <button
                    type="button"
                    onClick={() => navigate(`/recon/runs/${plan.source_recon_session_id}`)}
                    className="rounded-chip focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Badge variant="secondary" className="cursor-pointer">
                      From recon run #{plan.source_recon_session_id}
                    </Badge>
                  </button>
                )}
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

          {plan.api_key.has_key && (
            <div className="mt-sm">
              <p className="mb-xxs text-micro uppercase tracking-wider font-semibold text-muted-foreground">
                Agent API key
              </p>
              <div className="flex flex-wrap items-center gap-sm">
                <Badge variant={plan.api_key.is_active ? 'success' : 'warning'}>
                  {plan.api_key.is_active ? 'Active' : 'Expired'}
                </Badge>
                <span className="text-caption text-muted-foreground">
                  {plan.api_key.is_active
                    ? `Expires in ${formatTimeLeft(plan.api_key.expires_in_seconds)}`
                    : `Expired ${formatTimeLeft(plan.api_key.expires_in_seconds)} ago`}
                </span>
                {plan.api_key.key_prefix && (
                  <span className="font-mono text-caption text-muted-foreground">
                    {plan.api_key.key_prefix}…
                  </span>
                )}
                {canManage && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleRotateKey}
                    disabled={rotatingKey}
                    className="ml-auto"
                  >
                    {rotatingKey ? 'Regenerating…' : 'Regenerate key'}
                  </Button>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {newApiKey && (
        <Alert variant="success" className="mb-sm">
          <AlertDescription>
            <p className="font-semibold">New agent API key — copy now, shown only once.</p>
            {newApiKeyExpiresAt && (
              <p className="mt-xxs text-caption text-muted-foreground">
                Expires {new Date(newApiKeyExpiresAt).toLocaleString()}.
              </p>
            )}
            <div className="mt-xs flex items-center gap-xs">
              <code className="flex-1 break-all rounded-control bg-accent p-xs font-mono text-caption">
                {newApiKey}
              </code>
              <Button
                size="sm"
                onClick={async () => {
                  // v4.7.5 — await the clipboard write so the success
                  // toast only fires after the write actually landed.
                  // Pre-fix the toast fired synchronously, which lied
                  // to operators about a denied/blocked clipboard
                  // permission — they'd dismiss the banner thinking
                  // the key was safe and lose it.
                  try {
                    await navigator.clipboard.writeText(newApiKey);
                    setRegeneratedKeyCopied(true);
                    toast.success('Copied to clipboard');
                  } catch {
                    toast.warning(
                      'Could not copy to clipboard — copy the key manually before dismissing.',
                    );
                  }
                }}
              >
                Copy
              </Button>
              <Button
                variant="ghost"
                size="icon"
                disabled={!regeneratedKeyCopied}
                onClick={() => {
                  // v4.7.5 — dismiss gated on a successful copy.
                  // Mirrors the Execute dialog's apiKeySaved gate
                  // (operator must positively acknowledge they have
                  // the key before the only on-screen copy disappears).
                  setNewApiKey(null);
                  setNewApiKeyExpiresAt(null);
                  setRegeneratedKeyCopied(false);
                }}
                aria-label={
                  regeneratedKeyCopied
                    ? 'Dismiss'
                    : 'Copy the key before dismissing'
                }
              >
                <XCircle className="size-4" aria-hidden />
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

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

      {/* FRX·M9: explicit next-step banner for the silent "approved but
          never run" state — without this, an approved plan looks the
          same as a completed one and the operator has to hunt for the
          Execute action. */}
      {canManage &&
        plan.status === 'approved' &&
        (plan.execution_session_count ?? 0) === 0 && (
          <NextStepBanner
            title="Plan approved"
            body="Run the plan with Execute with AI to start the engagement."
            primaryCta={{ label: 'Execute with AI', onClick: openExecuteDialog }}
            tone="success"
            className="mb-sm"
          />
        )}

      {/* Lifecycle actions (Approve / Reject / Execute / Export / Import /
          Report) and Abandon now live in the WorkflowDetailHeader action
          bar above.  Delete lives behind the Manage tab. */}

      {plan.rejection_reason && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>
            <p className="font-semibold">Rejection Reason:</p>
            <p className="break-words">{plan.rejection_reason}</p>
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

      {/* Reject dialog */}
      <ConfirmDialog
        open={rejectOpen}
        onOpenChange={setRejectOpen}
        busy={actionLoading}
        title="Reject Test Plan"
        description="The plan stays in the project for revision. Optionally explain why so the author can address the concern before resubmitting."
        reason={{ value: rejectReason, onChange: setRejectReason }}
        confirmLabel="Reject"
        confirmVariant="destructive"
        onConfirm={handleReject}
      />

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

      {/* Execute dialog */}
      {/*
        v4.7.5 — close affordance is GATED while a one-time API key is on
        screen and the user hasn't yet acknowledged copying it.  Pre-fix
        Esc / overlay click / the corner X all bypassed the Done-button
        gate; an operator could lose a non-recoverable key with a stray
        keystroke.  Mirrors the StartReconDialog pattern from UX2-1
        (v2.44.4 / 4.7.0).
      */}
      <Dialog
        open={executeOpen}
        onOpenChange={(v) => {
          if (v) return; // opening — always allow
          if (executeLoading) return; // network in flight — block close
          if (executeResult && !apiKeySaved) return; // key not yet saved
          setExecuteOpen(false);
        }}
      >
        <DialogContent
          className="max-w-3xl"
          showClose={!executeResult || apiKeySaved}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-xs">
              <Play className="size-5 text-primary" aria-hidden />
              {resumeSessionId != null ? 'Resume Execution Session' : 'Execute with AI'}
            </DialogTitle>
            <DialogDescription>
              {resumeSessionId != null
                ? `Re-mint a fresh agent key (24h TTL) for execution session #${resumeSessionId}. Prior per-test results and sanity checks are preserved — the agent reads them from the execution context and continues from where it stopped.`
                : "Mint a single-plan, single-execution agent key (24h TTL). Hand it to your terminal-side agent (Claude Code, Codex, Cursor) along with the prompt that's generated below. The agent executes one entry at a time and asks for approval per command — nothing runs autonomously."}
            </DialogDescription>
          </DialogHeader>
          {/* v4.12.1 — wrap the body in DialogBody so the multi-KB
              instructions + InAppAgentPanel scroll INSIDE the dialog
              frame instead of pushing the footer off-screen.  Without
              this, on shorter viewports the Done button became
              unreachable — the "can't close the modal" symptom.
              Mirrors StartReconDialog's pattern. */}
          <DialogBody className="flex flex-col gap-md">
          {!executeResult ? (
            <>
              {resumeSessionId != null ? (
                resumeLooksInterrupted ? (
                  <p className="text-metadata">
                    Resuming session <strong>#{resumeSessionId}</strong> issues a fresh
                    24h agent key and continues the run — all prior per-test results and
                    sanity checks are preserved. The session's previous key is revoked.
                  </p>
                ) : (
                  <Alert variant="warning">
                    <AlertDescription>
                      Session <strong>#{resumeSessionId}</strong> shows{' '}
                      <strong>recent agent activity</strong> — it may still be running.
                      Resuming issues a new key and <strong>revokes the current one</strong>:
                      an agent still working on this session will stop. Continue only if
                      you know the run is interrupted.
                    </AlertDescription>
                  </Alert>
                )
              ) : (
                <p className="text-metadata">
                  This will create an <strong>execution session</strong> and generate a time-limited
                  API key plus an instructions block you can copy to your AI agent. The agent will
                  guide you through each test, asking for your approval before running any commands.
                </p>
              )}
              {executeError && (
                <Alert variant="destructive">
                  <AlertDescription>{executeError}</AlertDescription>
                </Alert>
              )}
            </>
          ) : (
            <div className="flex flex-col gap-sm">
              <Alert variant="success">
                <AlertDescription>
                  Execution session <strong>#{executeResult.execution_session_id}</strong>{' '}
                  {resumeSessionId != null ? 'resumed' : 'created'}. Copy the API key and
                  instructions below, then paste them to your AI agent.
                </AlertDescription>
              </Alert>

              <div>
                <p className="mb-xxs text-metadata font-semibold">API Key (shown once)</p>
                <div className="flex items-center gap-xs rounded-control bg-accent p-xs">
                  <p className="flex-1 break-all font-mono text-caption">{executeResult.api_key}</p>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleCopy(executeResult.api_key, setCopiedKey)}
                        aria-label="Copy API key to clipboard"
                      >
                        {copiedKey ? (
                          <Check className="size-4 text-success" aria-hidden />
                        ) : (
                          <Copy className="size-4" aria-hidden />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{copiedKey ? 'Copied!' : 'Copy API key'}</TooltipContent>
                  </Tooltip>
                </div>
                {!apiKeySaved && (
                  // FRX·H8: explicit warning while the operator hasn't
                  // copied the key yet — Done is disabled in this state.
                  <Alert variant="warning" className="mt-xs">
                    <AlertDescription>
                      This API key is shown only once. Copy it to your password manager before
                      closing this dialog — there is no way to recover it later.
                    </AlertDescription>
                  </Alert>
                )}
              </div>

              <div>
                <div className="mb-xxs flex items-center justify-between">
                  <p className="text-metadata font-semibold">Instructions (copy to agent)</p>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleCopy(executeResult.instructions, setCopiedInstructions)}
                        aria-label="Copy agent instructions to clipboard"
                      >
                        {copiedInstructions ? (
                          <Check className="size-4 text-success" aria-hidden />
                        ) : (
                          <Copy className="size-4" aria-hidden />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      {copiedInstructions ? 'Copied!' : 'Copy instructions'}
                    </TooltipContent>
                  </Tooltip>
                </div>
                <div className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
                  {executeResult.instructions}
                </div>
              </div>

              <div>
                <p className="mb-xxs text-metadata font-semibold">Run with In-App Agent</p>
                <InAppAgentPanel
                  prompt={executeResult.instructions}
                  contextLabel={`execution of plan #${executeResult.plan_id}`}
                />
              </div>
            </div>
          )}
          </DialogBody>
          <DialogFooter>
            {!executeResult ? (
              <>
                <Button variant="outline" onClick={() => setExecuteOpen(false)} disabled={executeLoading}>
                  Cancel
                </Button>
                {resumeSessionId != null ? (
                  <Button
                    onClick={handleResumeConfirm}
                    disabled={executeLoading}
                    variant={resumeLooksInterrupted ? 'default' : 'destructive'}
                  >
                    {executeLoading ? (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <RotateCcw className="size-4" aria-hidden />
                    )}
                    {resumeLooksInterrupted ? 'Resume session' : 'Resume anyway'}
                  </Button>
                ) : (
                  <Button onClick={handleExecute} disabled={executeLoading}>
                    {executeLoading ? (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    ) : (
                      <Play className="size-4" aria-hidden />
                    )}
                    Start Execution Session
                  </Button>
                )}
              </>
            ) : (
              // v4.12.1 — mirror StartReconDialog: explicit
              // acknowledgement checkbox rather than gating Done on
              // clipboard-write success.  Pre-fix, clipboard failure
              // (insecure origin / denied permission / RDP hardening)
              // silently left Done disabled AND the X hidden — the
              // user was trapped in the dialog with a one-time key
              // they couldn't save.  Checkbox always works regardless
              // of clipboard availability; user can read the key off
              // screen and tick the box.
              <div className="flex w-full flex-col gap-xs">
                <label className="flex items-start gap-xs text-metadata">
                  <Checkbox
                    checked={apiKeySaved}
                    onCheckedChange={(v) => setApiKeySaved(v === true)}
                    aria-label="I copied the agent API key"
                  />
                  <span>
                    I copied the agent API key.  It is shown only once;
                    no recovery path after this dialog closes.
                  </span>
                </label>
                <div className="flex flex-wrap justify-end gap-xs">
                  <Button
                    onClick={() => setExecuteOpen(false)}
                    disabled={!apiKeySaved}
                  >
                    Close
                  </Button>
                </div>
              </div>
            )}
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
                <SelectItem value="pdf">PDF (printable)</SelectItem>
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

      {/* Plan-generation Resume dialog — third leg of the agentic-
          workflow Resume affordance.  Confirm state explains the
          revoke-prior-key consequence; success state shows the new
          api_key + instructions block to copy to a fresh agent. */}
      <Dialog
        open={resumeGenOpen}
        onOpenChange={(v) => {
          if (v) return;
          if (resumeGenLoading) return;
          if (resumeGenResult && !resumeGenKeySaved) return;
          setResumeGenOpen(false);
        }}
      >
        <DialogContent
          className="max-w-3xl"
          showClose={!resumeGenResult || resumeGenKeySaved}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-xs">
              <RotateCcw className="size-5 text-primary" aria-hidden />
              Resume Plan Generation
            </DialogTitle>
            <DialogDescription>
              Re-mints a fresh agent key (24h TTL) and rebuilds the plan-generation
              instructions for this draft plan.  Existing entries are preserved — the
              agent continues from <code>/context</code> with the
              <code>not_in_plan_id</code> cursor.
            </DialogDescription>
          </DialogHeader>
          {/* Wrap in DialogBody so the multi-KB instructions don't push
              the footer off-screen on shorter viewports (same hazard
              the Execute dialog had — see 4.12.1 fix above). */}
          <DialogBody className="flex flex-col gap-md">
          {!resumeGenResult ? (
            <>
              <Alert variant="warning">
                <AlertDescription>
                  Resuming issues a new key and <strong>revokes the current one</strong>.
                  Any agent still running on the old key will be cut off the next time it
                  calls in.  Use this when the draft has been silent because the agent
                  process died — not while a healthy agent is still working.
                </AlertDescription>
              </Alert>
              {resumeGenError && (
                <Alert variant="destructive">
                  <AlertDescription>{resumeGenError}</AlertDescription>
                </Alert>
              )}
            </>
          ) : (
            <div className="flex flex-col gap-sm">
              <Alert variant="success">
                <AlertDescription>
                  Plan generation resumed.  Copy the API key and instructions below, then
                  paste them to your AI agent.
                </AlertDescription>
              </Alert>
              <div>
                <p className="mb-xxs text-metadata font-semibold">API Key (shown once)</p>
                <div className="flex items-center gap-xs rounded-control bg-accent p-xs">
                  <p className="flex-1 break-all font-mono text-caption">
                    {resumeGenResult.api_key}
                  </p>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleCopy(resumeGenResult.api_key, () => {})}
                        aria-label="Copy API key"
                      >
                        <Copy className="size-4" aria-hidden />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Copy API key</TooltipContent>
                  </Tooltip>
                </div>
                {!resumeGenKeySaved && (
                  <Alert variant="warning" className="mt-xs">
                    <AlertDescription>
                      This API key is shown only once. Copy it before closing.
                    </AlertDescription>
                  </Alert>
                )}
              </div>
              <div>
                <p className="mb-xxs text-metadata font-semibold">Instructions</p>
                <div className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
                  {resumeGenResult.instructions}
                </div>
              </div>
            </div>
          )}
          </DialogBody>
          <DialogFooter>
            {!resumeGenResult ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => setResumeGenOpen(false)}
                  disabled={resumeGenLoading}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  onClick={handleResumeGenConfirm}
                  disabled={resumeGenLoading}
                >
                  {resumeGenLoading ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <RotateCcw className="size-4" aria-hidden />
                  )}
                  Resume generation
                </Button>
              </>
            ) : (
              // Explicit acknowledgement checkbox — same parity fix
              // as the Execute dialog (and StartReconDialog) so the
              // operator isn't trapped when clipboard is unavailable.
              <div className="flex w-full flex-col gap-xs">
                <label className="flex items-start gap-xs text-metadata">
                  <Checkbox
                    checked={resumeGenKeySaved}
                    onCheckedChange={(v) => setResumeGenKeySaved(v === true)}
                    aria-label="I copied the agent API key"
                  />
                  <span>
                    I copied the agent API key.  It is shown only once;
                    no recovery path after this dialog closes.
                  </span>
                </label>
                <div className="flex flex-wrap justify-end gap-xs">
                  <Button
                    onClick={() => setResumeGenOpen(false)}
                    disabled={!resumeGenKeySaved}
                  >
                    Close
                  </Button>
                </div>
              </div>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default TestPlanLayout;
