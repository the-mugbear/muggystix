import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowLeftRight,
  Bot,
  Info,
  Loader2,
  RefreshCw,
  Search,
  SquareArrowOutUpRight,
} from 'lucide-react';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useSearchFocus } from '../hooks/useSearchFocus';
import { NavigableTableRow, NavigableTableCell } from '../components/NavigableTableRow';
import {
  getTestPlans,
  generateTestPlan,
  TestPlanSummary,
  GeneratePlanRequest,
  GeneratePlanResponse,
} from '../services/api';
import { formatStatusLabel } from '../utils/statusMeta';
import { formatApiError } from '../utils/apiErrors';
import { PlanSelection, takePlanSelection } from '../utils/planSelection';
import { useToast } from '../contexts/ToastContext';
import { useProject } from '../contexts/ProjectContext';
import { useNow } from '../hooks/useNow';
import { ListPageSkeleton } from '../components/PageSkeleton';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Checkbox } from '../components/ui/checkbox';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../components/ui/dialog';
import InAppAgentPanel from '../components/InAppAgentPanel';
import McpConnectPanel from '../components/McpConnectPanel';
import { CopyButton } from '../components/ui/code-block';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Input } from '../components/ui/input';
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
import { Textarea } from '../components/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { cn } from '../utils/cn';

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

function stripAttribution(text: string): string {
  return text.replace(/^🤖\s*\*{0,2}Agent-generated\*{0,2}\s*—\s*\S+\s*/i, '').trimStart();
}

const formatDate = (d?: string) =>
  d ? new Date(d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : '-';

// v5.288.0 — a new key: the old one ('testPlansWorkflowExpanded') was written
// as 'true' on every mount while expanded was the default, so reading it would
// keep the explainer open for everyone who had ever visited.
const WORKFLOW_EXPANDED_KEY = 'testPlans.workflowExplainer.expanded';

/** v5.288.0 — progress as the fraction it is ("1 of 4 entries done") over a
 *  small bar, instead of a bare "0%" whose empty bar was invisible. Done =
 *  completed or rejected, the same rule as completion_pct. */
const PlanProgress: React.FC<{ plan: TestPlanSummary }> = ({ plan }) => {
  const total = plan.entry_count;
  if (total === 0) {
    return <span className="text-caption text-muted-foreground">No entries yet</span>;
  }
  const done = plan.entries_done ?? Math.round((plan.completion_pct / 100) * total);
  const pct = Math.min(100, Math.max(0, (done / total) * 100));
  return (
    <div className="flex min-w-0 flex-col gap-xxs">
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-label={`${done} of ${total} entries done`}
      >
        <div className="h-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-caption text-muted-foreground">
        {done.toLocaleString()} of {total.toLocaleString()} {total === 1 ? 'entry' : 'entries'} done
      </p>
    </div>
  );
};

/** The plan author as a person: full name, else username. */
const authorName = (plan: TestPlanSummary): string | null =>
  plan.created_by_full_name?.trim() || plan.created_by_username || null;

// --- generate-plan dialog form state (v2.43.0 — MONO-3 migration) -----
// Pre-v2.43.0 the 7 form-input fields lived in 7 separate useState
// slots.  Reset required calling 7 setters in lockstep; adding a new
// field meant adding another slot + remembering to clear it.  The
// reducer collapses both: one slot, one action union, one reset.
// Async-side state (generating, generatingStartedAt, genError, genResult)
// stays as useState because those have their own lifecycle (timer hook,
// abort signal, etc.) and don't share the form's reset semantics.

type GenSeverity = '' | 'critical' | 'high' | 'medium' | 'low';

interface GenFormState {
  title: string;
  description: string;
  subnets: string;
  ports: string;
  services: string;
  minSeverity: GenSeverity;
}

const INITIAL_GEN_FORM: GenFormState = {
  title: '',
  description: '',
  subnets: '',
  ports: '',
  services: '',
  minSeverity: '',
};

type GenFormAction =
  | { type: 'setTitle'; value: string }
  | { type: 'setDescription'; value: string }
  | { type: 'setSubnets'; value: string }
  | { type: 'setPorts'; value: string }
  | { type: 'setServices'; value: string }
  | { type: 'setMinSeverity'; value: GenSeverity }
  | { type: 'reset'; title?: string };

function genFormReducer(state: GenFormState, action: GenFormAction): GenFormState {
  switch (action.type) {
    case 'setTitle': return { ...state, title: action.value };
    case 'setDescription': return { ...state, description: action.value };
    case 'setSubnets': return { ...state, subnets: action.value };
    case 'setPorts': return { ...state, ports: action.value };
    case 'setServices': return { ...state, services: action.value };
    case 'setMinSeverity': return { ...state, minSeverity: action.value };
    case 'reset': return { ...INITIAL_GEN_FORM, title: action.title ?? '' };
  }
}

const TestPlans: React.FC = () => {
  const navigate = useNavigate();
  const toast = useToast();
  const { currentProject } = useProject();
  const [searchParams, setSearchParams] = useSearchParams();
  const [plans, setPlans] = useState<TestPlanSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  // FRX·H4: client-side search over title + author username.  300ms
  // debounce so very fast typing doesn't thrash the filter loop.
  const [searchText, setSearchText] = useState('');
  // v2.43.0 — UX review #7: subscribe to the global `/` shortcut so the
  // documented "press / to focus search" behavior actually fires.
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  useSearchFocus(searchInputRef);
  const debouncedSearchText = useDebouncedValue(searchText, 300);

  const [generateOpen, setGenerateOpen] = useState(false);
  // v2.43.0 — MONO-3: 7 form fields collapsed into one reducer slot
  // (see genFormReducer above).  Reset is one dispatch instead of
  // seven setter calls; adding a field is one row in GenFormState +
  // GenFormAction.
  const [genForm, dispatchGenForm] = useReducer(genFormReducer, INITIAL_GEN_FORM);
  // FRX·M1: when arriving via a recon-detail "Generate Test Plan" CTA
  // (`?generate=1&source_recon_session_id=N`) the dialog auto-opens
  // with the source pre-filled.  We just stash the source id locally;
  // the generate flow's filter_criteria currently doesn't have a
  // dedicated recon-source field, so the value rides along for any
  // downstream surfacing.
  const [sourceReconSessionId, setSourceReconSessionId] = useState<number | null>(null);
  // v5.221.0 — a fixed host selection handed over from the Hosts bulk bar
  // (`?generate=1&source=selection`, payload in sessionStorage).  The agent's
  // candidates are restricted to it; the filter fields do not apply.
  const [sourceSelection, setSourceSelection] = useState<PlanSelection | null>(null);
  const [generating, setGenerating] = useState(false);
  // Wall-clock counter so the user sees the long-running LLM call is
  // still alive — generation routinely takes 30-120s on Opus, and a
  // static "Generating..." label gave no indication of progress
  // (audit C10).
  //
  // FBK·L2: track the start timestamp instead of an elapsed-seconds
  // counter so closing and reopening the dialog while the fire-and-
  // forget fetch is still in flight resumes the counter rather than
  // resetting it to 0.  `useNow(1000)` provides the tick.
  const [generatingStartedAt, setGeneratingStartedAt] = useState<number | null>(null);
  const nowTick = useNow(1000);
  useEffect(() => {
    if (generating && generatingStartedAt == null) {
      setGeneratingStartedAt(Date.now());
    } else if (!generating && generatingStartedAt != null) {
      setGeneratingStartedAt(null);
    }
  }, [generating, generatingStartedAt]);
  const genElapsed =
    generatingStartedAt != null ? Math.floor((nowTick - generatingStartedAt) / 1000) : 0;
  const [genError, setGenError] = useState<string | null>(null);
  const [genResult, setGenResult] = useState<GeneratePlanResponse | null>(null);
  // One-time-key acknowledgement gate — mirrors StartReconDialog / the
  // Execute dialog so the generate flow can't lose the shown-once agent
  // key to a stray close. Reset whenever a fresh key is shown.
  const [genKeyAcknowledged, setGenKeyAcknowledged] = useState(false);
  useEffect(() => {
    if (genResult?.api_key) setGenKeyAcknowledged(false);
  }, [genResult?.api_key]);

  // v5.288.0 — collapsed by default (it pushed the list below the fold), and
  // only the viewer's own toggle is remembered: written on change, never on
  // mount, and storage that throws (private window, blocked site data) just
  // means the default.
  const [workflowExpanded, setWorkflowExpanded] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(WORKFLOW_EXPANDED_KEY) === 'true';
    } catch {
      return false;
    }
  });
  const toggleWorkflowExpanded = (next: boolean) => {
    setWorkflowExpanded(next);
    try {
      window.localStorage.setItem(WORKFLOW_EXPANDED_KEY, String(next));
    } catch {
      /* storage unavailable — the choice lasts for this visit only */
    }
  };

  const loadPlans = useCallback(() => {
    setLoading(true);
    setError(null);
    getTestPlans({ status: statusFilter || undefined })
      .then(setPlans)
      .catch((err) => setError(formatApiError(err, 'Failed to load test plans.')))
      .finally(() => setLoading(false));
  }, [statusFilter]);

  // FRX·H4: client-side filter over title + author username.  Kept
  // local for now; server-side search is out of scope.
  const filteredPlans = useMemo(() => {
    const q = debouncedSearchText.trim().toLowerCase();
    if (!q) return plans;
    return plans.filter((p) => {
      const title = (p.title || '').toLowerCase();
      const agent = (p.agent_name || '').toLowerCase();
      const author = (p.created_by_username || '').toLowerCase();
      const fullName = (p.created_by_full_name || '').toLowerCase();
      return title.includes(q) || agent.includes(q) || author.includes(q) || fullName.includes(q);
    });
  }, [plans, debouncedSearchText]);

  // Re-fetch whenever the active project changes — without this, a
  // project switch via the topbar leaves the previous project's plan
  // list visible until something else (status filter, page mount)
  // forces a refresh.  Also re-fetches on mount, which covers the
  // navigate-back-from-detail case the user reported during 4.1.0
  // regression: after generating a plan, navigating to detail, and
  // returning to /test-plans the new plan was missing because the
  // component was already mounted with stale `plans`.
  useEffect(() => {
    loadPlans();
  }, [loadPlans, currentProject?.id]);

  const toggleSelect = (id: number) => {
    setSelectedIds((curr) => {
      if (curr.includes(id)) {
        // Unchecking — always allowed.
        return curr.filter((x) => x !== id);
      }
      if (curr.length >= 2) {
        // FBK·M4: previously we silently dropped the oldest selection
        // to make room for the new one.  That's a hidden mutation; the
        // user clicks a 3rd checkbox and one of the existing two just
        // disappears without explanation.  Warn instead and require
        // the user to deliberately uncheck one first.
        toast.warning('Compare supports two plans — uncheck one to swap.', {
          id: 'compare-limit',
        });
        return curr;
      }
      return [...curr, id];
    });
  };

  const compareEnabled = selectedIds.length === 2;
  const onCompare = () => {
    if (!compareEnabled) return;
    navigate(`/test-plans/compare?a=${selectedIds[0]}&b=${selectedIds[1]}`);
  };

  const openGenerateDialog = useCallback(() => {
    const nextPhase = plans.length + 1;
    dispatchGenForm({
      type: 'reset',
      title: `Penetration Test Plan — Phase ${nextPhase}`,
    });
    setGenError(null);
    setGenResult(null);
    setGenerateOpen(true);
  }, [plans.length]);

  // FRX·M1: auto-open the generate dialog when arriving from a recon
  // detail "Generate Test Plan" CTA.  We read the params once, open,
  // then clear them so a refresh doesn't keep popping the dialog.
  useEffect(() => {
    if (searchParams.get('generate') !== '1') return;
    const sourceId = searchParams.get('source_recon_session_id');
    if (sourceId) {
      const parsed = parseInt(sourceId, 10);
      if (!Number.isNaN(parsed)) setSourceReconSessionId(parsed);
    }
    // Arriving for a SELECTION without one must not fall through to an
    // unrestricted generate dialog: that would plan over the whole project,
    // not the hosts the operator chose.
    let open = true;
    if (searchParams.get('source') === 'selection') {
      const sel = takePlanSelection();
      if (sel) {
        setSourceSelection(sel);
      } else {
        toast.warning('The host selection was not found; pick the hosts again.');
        open = false;
      }
    }
    if (open) openGenerateDialog();
    const params = new URLSearchParams(searchParams);
    params.delete('generate');
    params.delete('source_recon_session_id');
    params.delete('source');
    setSearchParams(params, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleGenerate = async () => {
    setGenerating(true);
    setGenError(null);
    try {
      const req: GeneratePlanRequest = { title: genForm.title, description: genForm.description || undefined };
      const fc: GeneratePlanRequest['filter_criteria'] = {};
      if (genForm.subnets) fc.subnets = genForm.subnets;
      if (genForm.ports) fc.ports = genForm.ports;
      if (genForm.services) fc.services = genForm.services;
      if (genForm.minSeverity) fc.min_severity = genForm.minSeverity;
      if (Object.keys(fc).length > 0) req.filter_criteria = fc;
      // Preserve recon provenance when the dialog was opened from a recon
      // run (the dialog advertises "Source: recon run #N" — make it true).
      if (sourceReconSessionId != null) {
        req.source_kind = 'recon_session';
        req.source_recon_session_id = sourceReconSessionId;
      }
      // A fixed selection from the Hosts page: the agent plans against
      // exactly these hosts, so the filter fields are not sent.
      if (sourceSelection) {
        req.source_kind = 'manual_hosts';
        req.source_host_ids = sourceSelection.host_ids;
        delete req.filter_criteria;
        const why = sourceSelection.rationale ? `Why these hosts: ${sourceSelection.rationale}` : '';
        const note = `Fixed selection of ${sourceSelection.host_ids.length} hosts from the Hosts page (${sourceSelection.summary}).`;
        req.description = [req.description, note, why].filter(Boolean).join('\n\n');
      }

      const result = await generateTestPlan(req);
      setGenResult(result);
      // Refresh the listing as soon as generation succeeds (don't wait
      // for the user to close the dialog) so the new plan is on screen
      // when they return.  Previously the user had to click Close
      // AFTER seeing the success state, and if they instead clicked
      // "View Plan" → navigated to detail → returned via sidebar, the
      // list was stale.
      loadPlans();
    } catch (err: unknown) {
      // formatApiError unwraps FastAPI structured detail (incl. array
      // shapes that previously rendered as [object Object]) and
      // network-down cases that previously surfaced as 'Network Error'.
      setGenError(formatApiError(err, 'Failed to generate test plan.'));
    } finally {
      setGenerating(false);
    }
  };

  const closeGenerateDialog = () => {
    setGenerateOpen(false);
    setSourceReconSessionId(null);
    setGenKeyAcknowledged(false);
    // Always re-fetch on close — covers the dismiss-while-the-fetch-
    // is-in-flight edge case and the user-closes-then-the-agent-
    // submits sequence.  Cost is one extra GET; safety is much higher.
    loadPlans();
  };

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center justify-between gap-sm">
        <h1 className="text-page-title font-semibold">Test Plans</h1>
        <div className="flex flex-wrap items-center gap-xs">
          <Button onClick={loadPlans} size="sm" variant="outline" disabled={loading} aria-label="Refresh test plans">
            <RefreshCw className={cn('size-4', loading && 'animate-spin')} aria-hidden /> Refresh
          </Button>
          <Button onClick={openGenerateDialog} size="sm">
            <Bot className="size-4" aria-hidden /> Generate with AI
          </Button>
          <Button
            variant={compareEnabled ? 'default' : 'outline'}
            disabled={!compareEnabled}
            onClick={onCompare}
            size="sm"
          >
            <ArrowLeftRight className="size-4" aria-hidden />
            {compareEnabled ? 'Compare selected (2)' : `Compare (${selectedIds.length}/2)`}
          </Button>
          <div className="relative w-64">
            {/* FRX·H4: client-side search over title + author. v5.288.0 — a
                placeholder that fits; the "/" shortcut moves to the tooltip. */}
            <Search
              className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              ref={searchInputRef}
              type="search"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search title or author"
              title="Search title or author (press / to focus)"
              aria-label="Search test plans"
              className="pl-xl"
            />
          </div>
          <div className="min-w-40">
            <Select value={statusFilter || 'all'} onValueChange={(v) => setStatusFilter(v === 'all' ? '' : v)}>
              <SelectTrigger aria-label="Filter test plans by status">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All</SelectItem>
                <SelectItem value="draft">Draft</SelectItem>
                <SelectItem value="proposed">Proposed</SelectItem>
                <SelectItem value="approved">Approved</SelectItem>
                <SelectItem value="in_progress">In Progress</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
                <SelectItem value="rejected">Rejected</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {/* v5.288.0 — collapsed by default, and a section over a thin rule
          rather than a bordered card (UI_STYLE_GUIDE §7). The text was checked
          against the code: the agent submits explicitly (POST
          /agent/test-plans/{id}/submit — description and ≥1 entry required),
          approval is ANALYST+, execution needs an approved plan, entry
          statuses are TestEntryStatus, progress counts completed + rejected,
          and a live run does not close the plan. */}
      <Accordion
        type="single"
        collapsible
        value={workflowExpanded ? 'workflow' : ''}
        onValueChange={(v) => toggleWorkflowExpanded(v === 'workflow')}
        className="mb-md"
      >
        <AccordionItem value="workflow" data-testid="workflow-explainer">
          <AccordionTrigger className="py-xs">
            <div className="flex items-center gap-xs">
              <Info className="size-4 text-primary" aria-hidden />
              <span>How the test plan workflow works</span>
            </div>
          </AccordionTrigger>
          <AccordionContent>
            <div className="flex max-w-4xl flex-col gap-md text-metadata">
              <ol className="flex flex-col gap-sm pl-md [list-style-type:decimal]">
                <li>
                  <strong>Draft (agent).</strong> <em>Generate with AI</em> creates a{' '}
                  <strong>draft</strong> plan and an agent session for your agent (an agent in an
                  existing session can open a draft itself, and the Hosts page can start one from a
                  fixed selection). The agent reads the candidate hosts and adds entries — one per
                  host, with priority, phase, rationale and tool-specific commands.
                </li>
                <li>
                  <strong>Submit (agent).</strong> The agent submits the plan for review when it
                  is done — this is a step it takes, not an automatic one, and it needs a plan
                  description and at least one entry. The plan becomes <strong>proposed</strong>{' '}
                  and its approvers are notified. A plan whose agent stopped early stays a draft;
                  generation can be resumed from the plan.
                </li>
                <li>
                  <strong>Review (you).</strong> Read the entries and decide whether the direction
                  is sound: <strong>Approve Plan</strong>, or <strong>Reject</strong> with a
                  reason. It is one decision on the plan as a whole, by an analyst or project
                  admin; you do not need to disposition every entry first.
                </li>
                <li>
                  <strong>Execute.</strong> Only an approved plan can be executed —{' '}
                  <em>Execute with AI</em>, an agent session opening an execution run, or an
                  exported bundle whose results you import. The plan moves to{' '}
                  <strong>in progress</strong> when the first run opens. The agent checks each host
                  before testing it, records results, and closes each entry as{' '}
                  <em>completed</em> or <em>rejected</em> (not tested, with a reason). Testers can
                  also set an entry&rsquo;s status by hand: <em>proposed</em>, <em>approved</em>,{' '}
                  <em>in progress</em>, <em>completed</em> or <em>rejected</em>.
                </li>
                <li>
                  <strong>Wrap up.</strong> Progress counts the entries that are completed or
                  rejected. When a live run finishes every entry, the plan&rsquo;s stewards are
                  notified that it is ready to close — the run does not close the plan itself; an
                  imported bundle that closes every entry marks the plan{' '}
                  <strong>completed</strong>. <em>Abandon</em> archives a plan you no longer want.
                </li>
              </ol>

              <div>
                <h3 className="mb-xxs font-semibold">What shows on host pages</h3>
                <p className="text-muted-foreground">
                  Entries from approved, in-progress and completed plans appear on their
                  hosts&rsquo; pages, except entries marked rejected. Draft, proposed, rejected and
                  archived plans put nothing there.
                </p>
              </div>

              <div>
                <h3 className="mb-xxs font-semibold">
                  Why a plan can have fewer entries than the project has hosts
                </h3>
                <p className="mb-xs text-muted-foreground">
                  The agent does not write one entry per host. Before it starts, any filters you
                  set (subnets, ports, services, minimum severity) or a fixed host selection narrow
                  the candidates, and then:
                </p>
                <ol className="flex flex-col gap-xs pl-md text-muted-foreground [list-style-type:decimal]">
                  <li>
                    <strong className="text-foreground">Hosts with no open ports are left out</strong>{' '}
                    of the candidates it is given unless it asks for them — including hosts that
                    only came from a discovery sweep (ping, ARP, DNS) and were never port-scanned.
                    Port-scan them first if they matter.
                  </li>
                  <li>
                    <strong className="text-foreground">The agent follows a selection policy.</strong>{' '}
                    Hosts with critical or high vulnerabilities qualify. Medium-vulnerability hosts
                    qualify only with two or more identified services or a high-value port (SMB
                    445/139, RDP 3389, databases 1433/3306/5432/1521/27017, Redis 6379, VNC 5900).
                    An open port with no detected service does not count as a service — re-scan
                    with service detection (e.g. <code className="font-mono">nmap -sV</code>).
                  </li>
                </ol>
                <p className="mt-xs text-muted-foreground">
                  The planning context the agent reads counts the candidates it reviewed and how
                  many match the policy. If the numbers surprise you, the gap is usually scan
                  coverage, not the agent.
                </p>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      {error && (
        <Alert variant="destructive" className="mb-sm">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        // FBK·H3: previously a bare Loader2 inside a centred flex div,
        // which collapsed the table region and made the page feel
        // empty on every project switch.  ListPageSkeleton matches
        // the eventual shape so the layout stays stable.
        <ListPageSkeleton actionCount={4} tableProps={{ rows: 6, columns: 6 }} />
      ) : filteredPlans.length === 0 ? (
        // v5.288.0 — on the page, not in a card (§7).
        <div className="border-t border-border py-xl text-center" data-testid="plans-empty">
          <div>
            <Bot className="mx-auto mb-xs size-12 text-muted-foreground" aria-hidden />
            {plans.length === 0 && !statusFilter ? (
              // Genuinely empty: no plans for this project AND no
              // server-side status filter narrowing them away.
              <>
                <p className="text-metadata text-muted-foreground">
                  No test plans in{' '}
                  {currentProject?.name ? <strong>{currentProject.name}</strong> : 'this project'} yet.
                </p>
                <p className="mt-xxs text-caption text-muted-foreground">
                  Test plans are scoped to a project. If you generated one under a different project,
                  switch projects from the selector to find it.
                </p>
                <Button onClick={openGenerateDialog} className="mt-sm">
                  <Bot className="size-4" aria-hidden /> Generate with AI
                </Button>
              </>
            ) : (
              // Plans exist but the active status filter and/or search
              // text excluded them — don't claim the project is empty.
              <>
                <p className="text-metadata text-muted-foreground">
                  No test plans match the current filters.
                </p>
                <Button
                  variant="outline"
                  onClick={() => { setSearchText(''); setStatusFilter(''); }}
                  className="mt-sm"
                >
                  Clear filters
                </Button>
              </>
            )}
          </div>
        </div>
      ) : (
        <>
          {/* Desktop-only product: the table is the sole renderer;
              narrow widths scroll horizontally. v5.288.0 — on the page, not
              in a bordered card (§7); fixed layout with the width going to
              the title and the author, and a narrow checkbox column. */}
              <div className="overflow-x-auto">
                <Table style={{ tableLayout: 'fixed' }} className="min-w-[960px]">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10" />
                      <TableHead className="w-[40%]">Title</TableHead>
                      <TableHead className="w-[112px]">Status</TableHead>
                      <TableHead className="w-[18%]">Author</TableHead>
                      <TableHead className="w-[76px] text-center">Entries</TableHead>
                      <TableHead className="w-[150px]">Progress</TableHead>
                      <TableHead className="w-[112px]">Created</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredPlans.map((plan) => {
                      const strippedDesc = plan.description
                        ? stripAttribution(plan.description)
                        : '';
                      return (
                          <NavigableTableRow key={plan.id} selected={selectedIds.includes(plan.id)}>
                            <TableCell className="w-10 pr-0">
                              <Checkbox
                                checked={selectedIds.includes(plan.id)}
                                onCheckedChange={() => toggleSelect(plan.id)}
                                aria-label={`Select plan ${plan.id} for comparison`}
                              />
                            </TableCell>
                            {/* Title links to detail — flat row matching the
                                recon + execution workflow lists (no inline
                                expand). */}
                            <NavigableTableCell
                              to={`/test-plans/${plan.id}`}
                              ariaLabel={`Open plan #${plan.id}: ${plan.title}`}
                            >
                              {/* Audit RSP·H6 — wrap in min-w-0
                                  block so `truncate` actually clips
                                  with long titles inside table-cell. */}
                              {/* v5.288.0 — the title wraps to two lines
                                  (full text in the tooltip) instead of
                                  cutting off after a few words. */}
                              <div className="min-w-0 max-w-full" title={plan.title}>
                                <p className="line-clamp-2 break-words">
                                  {plan.title}
                                  {/* Version lived only in the removed
                                      mobile card; a plan's version matters
                                      when several revisions exist, so it
                                      moves here rather than being dropped.
                                      Matches ExecutionsList's `#id v{n}`. */}
                                  <span className="ml-xxs shrink-0 text-caption font-normal text-muted-foreground">
                                    v{plan.version}
                                  </span>
                                </p>
                                {strippedDesc && (
                                  <p className="truncate text-caption text-muted-foreground">
                                    {strippedDesc}
                                  </p>
                                )}
                              </div>
                            </NavigableTableCell>
                            <TableCell>
                              <Badge variant={planStatusTone(plan.status)} className="whitespace-nowrap">
                                {formatStatusLabel(plan.status)}
                              </Badge>
                            </TableCell>
                            <TableCell>
                              {/* v5.288.0 — the person first, by full name
                                  (username in the tooltip); the agent that
                                  drafted it under them. Both wrap. */}
                              <p
                                className="line-clamp-2 break-words"
                                title={plan.created_by_username ?? undefined}
                              >
                                {authorName(plan) ?? plan.agent_name ?? '-'}
                              </p>
                              {plan.agent_name && authorName(plan) && (
                                <p
                                  className="line-clamp-2 break-words text-caption text-muted-foreground"
                                  title={plan.agent_name}
                                >
                                  agent: {plan.agent_name}
                                </p>
                              )}
                            </TableCell>
                            <TableCell className="text-center">{plan.entry_count}</TableCell>
                            <TableCell>
                              <PlanProgress plan={plan} />
                            </TableCell>
                            <TableCell>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <span>{formatDate(plan.created_at)}</span>
                                </TooltipTrigger>
                                <TooltipContent>{plan.created_at}</TooltipContent>
                              </Tooltip>
                            </TableCell>
                          </NavigableTableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
        </>
      )}

      <Dialog
        open={generateOpen}
        onOpenChange={(v) => {
          if (v) return; // opening — always allow
          if (generating) return; // generation in flight — block close
          if (genResult && !genKeyAcknowledged) return; // one-time key not yet saved
          closeGenerateDialog();
        }}
      >
        {/* Mirrors StartReconDialog / the Execute dialog: xl width, a
            DialogDescription, a scrolling DialogBody, the same key +
            instructions + InAppAgentPanel handoff, and a "copied the
            key" acknowledgement gate so the shown-once key can't be lost
            to a stray close. */}
        <DialogContent size="xl" showClose={!genResult || genKeyAcknowledged}>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-xs">
              <Bot className="size-5 text-primary" aria-hidden />
              {genResult ? 'Plan Created' : 'Generate Test Plan with AI'}
            </DialogTitle>
            <DialogDescription>
              {genResult
                ? 'Copy the agent API key and instructions below into your terminal-side agent (Claude Code, Codex, Cursor). The key is shown once — confirm you copied it before closing.'
                : 'Mints a single-plan agent key and a prompt for your terminal-side agent, which populates this plan with structured test entries for your review. Narrow the candidate hosts with the optional filters below.'}
            </DialogDescription>
          </DialogHeader>

          <DialogBody className="flex flex-col gap-md">
          {genResult ? (
            <div className="flex flex-col gap-sm">
              <Alert variant="success">
                <AlertDescription>
                  Created: <strong>{genResult.plan_title}</strong>
                </AlertDescription>
              </Alert>

              <div>
                <div className="mb-xxs flex items-center justify-between">
                  <p className="text-metadata font-semibold">Agent API Key (shown once)</p>
                  <CopyButton text={genResult.api_key} label="Copy API key" />
                </div>
                <div className="break-all rounded-control border border-border bg-accent p-sm font-mono text-caption">
                  {genResult.api_key}
                </div>
              </div>

              {/* Three handoff routes as tabs, not a stack (v5.191.0, mirroring
                  StartAssistDialog): the prompt dump no longer sits between the
                  operator and the MCP config. */}
              {(() => {
                const hasMcp = (genResult.mcp_clients?.length ?? 0) > 0;
                return (
                  <Tabs defaultValue={hasMcp ? 'mcp' : 'prompt'}>
                    <TabsList className="mb-xs">
                      {hasMcp && <TabsTrigger value="mcp">Connect via MCP</TabsTrigger>}
                      <TabsTrigger value="prompt">Paste the prompt</TabsTrigger>
                      <TabsTrigger value="inapp">Run in-app</TabsTrigger>
                    </TabsList>
                    {hasMcp && (
                      <TabsContent value="mcp">
                        <McpConnectPanel
                          clients={genResult.mcp_clients ?? []}
                          withCertTrust
                          blurb={
                            'The planning tools appear natively in your client instead of as curl ' +
                            'recipes. Plan generation only reads and proposes; nothing runs until ' +
                            'you approve the plan.'
                          }
                        />
                      </TabsContent>
                    )}
                    <TabsContent value="prompt">
                      <div className="mb-xxs flex items-center justify-between">
                        <p className="text-metadata text-muted-foreground">
                          Paste into a terminal agent — it drives the same session with curl.
                        </p>
                        <CopyButton text={genResult.instructions} label="Copy instructions" />
                      </div>
                      <div className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
                        {genResult.instructions}
                      </div>
                    </TabsContent>
                    <TabsContent value="inapp">
                      <InAppAgentPanel
                        prompt={genResult.instructions}
                        contextLabel={`generation of plan #${genResult.plan_id}`}
                      />
                    </TabsContent>
                  </Tabs>
                );
              })()}
            </div>
          ) : (
            <div className="flex flex-col gap-sm">
              {genError && (
                <Alert variant="destructive">
                  <AlertDescription>{genError}</AlertDescription>
                </Alert>
              )}

              {sourceReconSessionId != null && (
                // FRX·M1: surface the source recon run when the dialog
                // was auto-opened from a recon-detail CTA so the
                // operator can confirm the hand-off lineage.
                <Alert variant="info">
                  <AlertDescription>
                    Source: recon run <strong>#{sourceReconSessionId}</strong>. The agent will use
                    host data populated by that run.
                  </AlertDescription>
                </Alert>
              )}
              {sourceSelection && (
                <Alert variant="info">
                  <AlertDescription>
                    Source: a fixed selection of{' '}
                    <strong>{sourceSelection.host_ids.length.toLocaleString()} hosts</strong> from the
                    Hosts page ({sourceSelection.summary}). The agent's candidate hosts are restricted
                    to that list; the host filters below do not apply.
                    {sourceSelection.rationale && (
                      <span className="mt-xxs block break-words text-caption">
                        Why: {sourceSelection.rationale}
                      </span>
                    )}
                  </AlertDescription>
                </Alert>
              )}

              <div>
                <Label htmlFor="gen-title">Plan Title</Label>
                <Input
                  id="gen-title"
                  value={genForm.title}
                  onChange={(e) => dispatchGenForm({ type: 'setTitle', value: e.target.value })}
                  required
                  autoFocus
                />
              </div>
              <div>
                <Label htmlFor="gen-description">Description</Label>
                <Textarea
                  id="gen-description"
                  value={genForm.description}
                  onChange={(e) => dispatchGenForm({ type: 'setDescription', value: e.target.value })}
                  rows={2}
                />
              </div>

              <Accordion type="single" collapsible>
                <AccordionItem value="filters" className="rounded-panel border border-border">
                  <AccordionTrigger className="px-md">Host Filters (optional)</AccordionTrigger>
                  <AccordionContent className="px-md">
                    <Alert variant="info" className="mb-sm">
                      <AlertDescription>
                        If no filters are set, all hosts in the project will be available as
                        candidates for the AI agent. Use filters to narrow scope to specific
                        subnets, services, or vulnerability levels.
                      </AlertDescription>
                    </Alert>
                    <p className="mb-sm text-caption text-muted-foreground">
                      Filters narrow by intersection — comma-separated values within a field match
                      any one (OR); separate fields all apply (AND).
                    </p>
                    <div className="flex flex-col gap-sm">
                      <div>
                        <Label htmlFor="gen-subnets">Subnets</Label>
                        <Input
                          id="gen-subnets"
                          value={genForm.subnets}
                          onChange={(e) => dispatchGenForm({ type: 'setSubnets', value: e.target.value })}
                          placeholder="e.g. 10.0.0.0/24, 192.168.1.0/24"
                        />
                        <p className="mt-xxs text-caption text-muted-foreground">
                          Comma-separated CIDR blocks
                        </p>
                      </div>
                      <div>
                        <Label htmlFor="gen-ports">Ports</Label>
                        <Input
                          id="gen-ports"
                          value={genForm.ports}
                          onChange={(e) => dispatchGenForm({ type: 'setPorts', value: e.target.value })}
                          placeholder="e.g. 22, 80, 443, 445"
                        />
                        <p className="mt-xxs text-caption text-muted-foreground">
                          Comma-separated port numbers — only hosts with these ports open will be
                          included
                        </p>
                      </div>
                      <div>
                        <Label htmlFor="gen-services">Services</Label>
                        <Input
                          id="gen-services"
                          value={genForm.services}
                          onChange={(e) => dispatchGenForm({ type: 'setServices', value: e.target.value })}
                          placeholder="e.g. ssh, http, smb"
                        />
                        <p className="mt-xxs text-caption text-muted-foreground">
                          Comma-separated service names (ssh, http, rdp, smb, mysql, etc.)
                        </p>
                      </div>
                      <div>
                        <Label htmlFor="gen-severity">Minimum vulnerability severity</Label>
                        <Select
                          value={genForm.minSeverity || 'none'}
                          onValueChange={(v) =>
                            dispatchGenForm({
                              type: 'setMinSeverity',
                              value: v === 'none' ? '' : (v as GenSeverity),
                            })
                          }
                        >
                          <SelectTrigger id="gen-severity">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none">No vulnerability filter</SelectItem>
                            <SelectItem value="critical">Critical (only)</SelectItem>
                            <SelectItem value="high">High or above</SelectItem>
                            <SelectItem value="medium">Medium or above</SelectItem>
                            <SelectItem value="low">Low or above</SelectItem>
                          </SelectContent>
                        </Select>
                        <p className="mt-xxs text-caption text-muted-foreground">
                          Hosts must have ≥1 vulnerability at this severity or above.
                        </p>
                      </div>
                    </div>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </div>
          )}
          </DialogBody>

          <DialogFooter>
            {genResult ? (
              <div className="flex w-full flex-col gap-xs">
                <label className="flex items-start gap-xs text-metadata">
                  <Checkbox
                    checked={genKeyAcknowledged}
                    onCheckedChange={(v) => setGenKeyAcknowledged(v === true)}
                    aria-label="I copied the agent API key"
                  />
                  <span>
                    I copied the agent API key. It is shown only once; any previous key for this
                    plan's agent has been revoked.
                  </span>
                </label>
                <div className="flex flex-wrap justify-end gap-xs">
                  <Button
                    variant="outline"
                    onClick={closeGenerateDialog}
                    disabled={!genKeyAcknowledged}
                  >
                    Close
                  </Button>
                  <Button
                    onClick={() => {
                      closeGenerateDialog();
                      navigate(`/test-plans/${genResult.plan_id}`);
                    }}
                    disabled={!genKeyAcknowledged}
                  >
                    View Plan
                    <SquareArrowOutUpRight className="size-3" aria-hidden />
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <Button variant="outline" onClick={closeGenerateDialog} disabled={generating}>
                  Cancel
                </Button>
                <Button onClick={handleGenerate} disabled={generating || !genForm.title.trim()}>
                  {generating ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <Bot className="size-4" aria-hidden />
                  )}
                  {generating ? `Generating… (${genElapsed}s)` : 'Generate'}
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default TestPlans;
