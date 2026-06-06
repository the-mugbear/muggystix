import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowLeftRight,
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Info,
  Loader2,
  RefreshCw,
  Search,
  SquareArrowOutUpRight,
} from 'lucide-react';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useSearchFocus } from '../hooks/useSearchFocus';
import { NavigableTableRow } from '../components/NavigableTableRow';
import {
  getTestPlans,
  generateTestPlan,
  TestPlanSummary,
  GeneratePlanRequest,
  GeneratePlanResponse,
} from '../services/api';
import { formatStatusLabel } from '../utils/statusMeta';
import { formatApiError } from '../utils/apiErrors';
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
import { Card, CardContent } from '../components/ui/card';
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

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const toast = useToast();
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
        toast.success('Copied to clipboard');
      },
      () => {
        toast.warning('Could not copy to clipboard. Select the text manually instead.');
      },
    );
  };
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={handleCopy}
          aria-label={label || 'Copy to clipboard'}
        >
          {copied ? (
            <Check className="size-4 text-success" aria-hidden />
          ) : (
            <Copy className="size-4" aria-hidden />
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{copied ? 'Copied' : label || 'Copy'}</TooltipContent>
    </Tooltip>
  );
}

const WORKFLOW_EXPANDED_KEY = 'testPlansWorkflowExpanded';

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
  minRisk: string;
}

const INITIAL_GEN_FORM: GenFormState = {
  title: '',
  description: '',
  subnets: '',
  ports: '',
  services: '',
  minSeverity: '',
  minRisk: '',
};

type GenFormAction =
  | { type: 'setTitle'; value: string }
  | { type: 'setDescription'; value: string }
  | { type: 'setSubnets'; value: string }
  | { type: 'setPorts'; value: string }
  | { type: 'setServices'; value: string }
  | { type: 'setMinSeverity'; value: GenSeverity }
  | { type: 'setMinRisk'; value: string }
  | { type: 'reset'; title?: string };

function genFormReducer(state: GenFormState, action: GenFormAction): GenFormState {
  switch (action.type) {
    case 'setTitle': return { ...state, title: action.value };
    case 'setDescription': return { ...state, description: action.value };
    case 'setSubnets': return { ...state, subnets: action.value };
    case 'setPorts': return { ...state, ports: action.value };
    case 'setServices': return { ...state, services: action.value };
    case 'setMinSeverity': return { ...state, minSeverity: action.value };
    case 'setMinRisk': return { ...state, minRisk: action.value };
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
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
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

  const [workflowExpanded, setWorkflowExpanded] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true;
    const saved = window.localStorage.getItem(WORKFLOW_EXPANDED_KEY);
    return saved === null ? true : saved === 'true';
  });
  useEffect(() => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(WORKFLOW_EXPANDED_KEY, String(workflowExpanded));
    }
  }, [workflowExpanded]);

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
      return title.includes(q) || agent.includes(q) || author.includes(q);
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

  const toggleExpand = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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
    openGenerateDialog();
    const params = new URLSearchParams(searchParams);
    params.delete('generate');
    params.delete('source_recon_session_id');
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
      if (genForm.minRisk && parseInt(genForm.minRisk)) fc.min_risk_score = parseInt(genForm.minRisk);
      if (Object.keys(fc).length > 0) req.filter_criteria = fc;

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
          <div className="relative min-w-52">
            {/* FRX·H4: client-side search over title + author. */}
            <Search
              className="pointer-events-none absolute left-sm top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              ref={searchInputRef}
              type="search"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              placeholder="Search title or author… (press / to focus)"
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

      <Accordion
        type="single"
        collapsible
        value={workflowExpanded ? 'workflow' : ''}
        onValueChange={(v) => setWorkflowExpanded(v === 'workflow')}
        className="mb-sm"
      >
        <AccordionItem value="workflow" className="rounded-panel border border-border">
          <AccordionTrigger className="px-md">
            <div className="flex items-center gap-xs">
              <Info className="size-4 text-primary" aria-hidden />
              <span>How the test plan workflow works</span>
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-md">
            <div className="flex flex-col gap-sm">
              <p className="text-metadata text-muted-foreground">
                A test plan moves through five phases. Each phase has a clear handoff so you always
                know whose turn it is.
              </p>
              <ol className="flex flex-col gap-sm pl-md text-metadata [list-style-type:decimal]">
                <li>
                  <strong>Generation (agent).</strong> Click <em>Generate with AI</em>, optionally
                  narrow the scope with filters, and an AI agent creates candidate test entries —
                  one per host, with priority, phase, rationale, and tool-specific commands. The
                  plan starts as a <strong>draft</strong>.
                </li>
                <li>
                  <strong>Submission (agent).</strong> When the agent finishes populating the plan
                  it submits automatically. The plan moves to <strong>proposed</strong> and the
                  agent's job is over.
                </li>
                <li>
                  <strong>Plan-level review (you).</strong> Open the plan, read through the entries,
                  and decide whether the agent's overall direction is sound. Click{' '}
                  <strong>Approve Plan</strong> to accept the work, or <strong>Reject</strong> with
                  a reason if the agent went off-topic. You do <em>not</em> need to disposition
                  every entry first — approval is a single yes/no on the plan as a whole.
                </li>
                <li>
                  <strong>Execution (testers).</strong> After approval, every entry is considered
                  queued. As you run each test, change its status from <em>proposed</em> →{' '}
                  <em>in&nbsp;progress</em> → <em>completed</em> (and fill in{' '}
                  <strong>findings</strong> if you discover anything). If you decide on closer
                  inspection that a specific entry isn't worth running, mark it <em>rejected</em>{' '}
                  with a note explaining why.
                </li>
                <li>
                  <strong>Wrap-up.</strong> The Execution Progress bar fills as entries reach a
                  terminal state (completed or rejected). Once everything is closed out, the plan
                  is done.
                </li>
              </ol>

              <Alert variant="info">
                <AlertDescription>
                  <strong>What surfaces on host pages.</strong> Once a plan is approved, its
                  entries appear on the corresponding host detail pages so testers can see what work
                  is queued for each host. Entries you reject during execution disappear from the
                  host page automatically. Plans that are still draft, proposed, or rejected do not
                  leak entries to host pages.
                </AlertDescription>
              </Alert>

              <Alert variant="warning">
                <AlertDescription>
                  <p className="mb-xs font-semibold">
                    Why your plan may have fewer entries than the project has hosts
                  </p>
                  <p className="mb-xs">
                    The agent does <em>not</em> create one entry per host. Two filters run before
                    any entry is written, and both can drop hosts silently:
                  </p>
                  <ol className="flex flex-col gap-xs pl-md [list-style-type:decimal]">
                    <li>
                      <strong>Hosts with no open ports are excluded by default.</strong> This
                      includes hosts that arrived from a discovery sweep (ping, ARP, DNS) but were
                      never port-scanned. They show as <em>up</em> in the Hosts list but the agent
                      treats them as having no actionable surface. If you have a lot of these, run
                      a port scan against them first — they'll be invisible to the agent until you
                      do.
                    </li>
                    <li>
                      <strong>The remaining hosts pass through a selection policy.</strong> Hosts
                      with critical or high vulnerabilities always qualify. Medium-vuln hosts only
                      qualify if they expose <em>multiple identified services</em> or open a
                      high-value port (SMB 445/139, RDP 3389, databases 1433/3306/5432/1521/27017,
                      Redis 6379, VNC 5900). Hosts with no vulnerabilities and ordinary ports are
                      filtered out. A port that's open but has no detected service name does{' '}
                      <em>not</em> count toward the "multiple services" rule — re-scan with service
                      detection (e.g. <code className="font-mono">nmap -sV</code>) to promote those
                      hosts.
                    </li>
                  </ol>
                  <p className="mt-xs">
                    When the agent finishes it reports a count like{' '}
                    <em>"36 reviewed candidates, 3 hosts that match policy"</em>. The first number
                    is the post-port-scan filter; the second is the post-policy filter and matches
                    the entry count of the resulting plan. If those numbers surprise you, the gap
                    is usually scan coverage, not the agent.
                  </p>
                </AlertDescription>
              </Alert>
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
        <Card>
          <CardContent className="p-xl text-center">
            <Bot className="mx-auto mb-xs size-12 text-muted-foreground" aria-hidden />
            {plans.length === 0 ? (
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
              <>
                <p className="text-metadata text-muted-foreground">
                  No test plans match the current search.
                </p>
                <Button
                  variant="outline"
                  onClick={() => setSearchText('')}
                  className="mt-sm"
                >
                  Clear search
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Mobile cards */}
          <div className="flex flex-col gap-xs md:hidden">
            {filteredPlans.map((plan) => {
              const isExpanded = expandedIds.has(plan.id);
              const strippedDesc = plan.description ? stripAttribution(plan.description) : '';
              return (
                <Card key={plan.id}>
                  <CardContent
                    className="cursor-pointer p-sm"
                    onClick={() => toggleExpand(plan.id)}
                  >
                    <div className="flex items-start gap-xs">
                      <Checkbox
                        checked={selectedIds.includes(plan.id)}
                        onCheckedChange={() => toggleSelect(plan.id)}
                        onClick={(e) => e.stopPropagation()}
                        aria-label={`Select plan ${plan.id} for comparison`}
                        className="mt-xxs"
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={isExpanded ? 'Collapse test plan' : 'Expand test plan'}
                        aria-expanded={isExpanded}
                        className="-ml-xxs -mt-xxs"
                      >
                        {isExpanded ? (
                          <ChevronUp className="size-4" aria-hidden />
                        ) : (
                          <ChevronDown className="size-4" aria-hidden />
                        )}
                      </Button>
                      <div className="min-w-0 flex-1">
                        <div className="mb-xs flex flex-wrap items-center gap-xs">
                          <Badge variant={planStatusTone(plan.status)}>
                            {formatStatusLabel(plan.status)}
                          </Badge>
                          <span className="text-caption text-muted-foreground">
                            {formatDate(plan.created_at)}
                          </span>
                        </div>
                        <p className="break-words font-semibold">{plan.title}</p>
                        {strippedDesc && (
                          <p className="mt-xxs whitespace-pre-wrap break-words text-metadata text-muted-foreground">
                            {strippedDesc}
                          </p>
                        )}
                        <div className="mt-xs flex flex-wrap gap-md text-caption text-muted-foreground">
                          <span>Author: {plan.agent_name || plan.created_by_username || '-'}</span>
                          <span>Entries: {plan.entry_count}</span>
                          <span>v{plan.version}</span>
                        </div>
                        <div className="mt-xs">
                          <div className="mb-xxs flex justify-between text-caption text-muted-foreground">
                            <span>Progress</span>
                            <span>{plan.completion_pct.toFixed(0)}%</span>
                          </div>
                          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                            <div
                              className="h-full bg-primary transition-all"
                              style={{ width: `${plan.completion_pct}%` }}
                            />
                          </div>
                        </div>
                      </div>
                    </div>
                    {isExpanded && (
                      <div className="mt-sm">
                        {plan.rejection_reason && (
                          <Alert variant="destructive" className="mb-xs">
                            <AlertDescription className="break-words">
                              <strong>{plan.status === 'archived' ? 'Abandon reason:' : 'Rejection reason:'}</strong>{' '}
                              {plan.rejection_reason}
                            </AlertDescription>
                          </Alert>
                        )}
                        <div className="flex flex-col gap-xs">
                          {plan.agent_name && plan.created_by_username && (
                            <p className="break-words text-caption text-muted-foreground">
                              Agent: {plan.agent_name} via {plan.created_by_username}
                            </p>
                          )}
                          {plan.approved_at && (
                            <p className="text-caption text-muted-foreground">
                              Approved {formatDate(plan.approved_at)}
                            </p>
                          )}
                          <Button
                            size="sm"
                            variant="outline"
                            className="self-start"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigate(`/test-plans/${plan.id}`);
                            }}
                          >
                            View Details
                            <SquareArrowOutUpRight className="size-3" aria-hidden />
                          </Button>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {/* Desktop table */}
          <div className="hidden md:block">
            <Card>
              <CardContent className="p-0">
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-12" />
                        <TableHead className="w-10" />
                        <TableHead className="w-[26%]">Title</TableHead>
                        <TableHead className="w-[10%]">Status</TableHead>
                        <TableHead className="w-[14%]">Author</TableHead>
                        <TableHead className="w-[7%] text-center">Entries</TableHead>
                        <TableHead className="w-[17%]">Progress</TableHead>
                        <TableHead className="w-[10%]">Created</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredPlans.map((plan) => {
                        const isExpanded = expandedIds.has(plan.id);
                        const strippedDesc = plan.description
                          ? stripAttribution(plan.description)
                          : '';
                        return (
                          <React.Fragment key={plan.id}>
                            {/* v2.43.0 — UX review #2: dropped role="link"/
                                tabIndex/whole-row onClick.  Expand is now
                                driven exclusively by the chevron button
                                (which already existed but was decorative);
                                checkbox selection lives in its own cell. */}
                            <NavigableTableRow selected={selectedIds.includes(plan.id)}>
                              <TableCell className="w-12">
                                <Checkbox
                                  checked={selectedIds.includes(plan.id)}
                                  onCheckedChange={() => toggleSelect(plan.id)}
                                  aria-label={`Select plan ${plan.id} for comparison`}
                                />
                              </TableCell>
                              <TableCell>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => toggleExpand(plan.id)}
                                  aria-label={isExpanded ? 'Collapse test plan' : 'Expand test plan'}
                                  aria-expanded={isExpanded}
                                >
                                  {isExpanded ? (
                                    <ChevronUp className="size-4" aria-hidden />
                                  ) : (
                                    <ChevronDown className="size-4" aria-hidden />
                                  )}
                                </Button>
                              </TableCell>
                              <TableCell>
                                {/* Audit RSP·H6 — wrap in min-w-0
                                    block so `truncate` actually clips
                                    with long titles inside table-cell. */}
                                <div className="min-w-0 max-w-full">
                                  <p className="truncate">{plan.title}</p>
                                  {strippedDesc && (
                                    <p className="truncate text-caption text-muted-foreground">
                                      {strippedDesc}
                                    </p>
                                  )}
                                </div>
                              </TableCell>
                              <TableCell>
                                <Badge variant={planStatusTone(plan.status)} className="whitespace-nowrap">
                                  {formatStatusLabel(plan.status)}
                                </Badge>
                              </TableCell>
                              <TableCell>
                                <p className="truncate">
                                  {plan.agent_name || plan.created_by_username || '-'}
                                </p>
                                {plan.agent_name && plan.created_by_username && (
                                  <p className="truncate text-caption text-muted-foreground">
                                    via {plan.created_by_username}
                                  </p>
                                )}
                              </TableCell>
                              <TableCell className="text-center">{plan.entry_count}</TableCell>
                              <TableCell>
                                <div className="flex flex-col gap-xxs">
                                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                                    <div
                                      className="h-full bg-primary transition-all"
                                      style={{ width: `${plan.completion_pct}%` }}
                                    />
                                  </div>
                                  <p className="text-caption text-muted-foreground">
                                    {plan.completion_pct.toFixed(0)}%
                                  </p>
                                </div>
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

                            {isExpanded && (
                              <TableRow>
                                <TableCell colSpan={8} className="bg-accent p-md">
                                  {strippedDesc && (
                                    <p className="mb-xs whitespace-pre-wrap break-words text-metadata text-muted-foreground">
                                      {strippedDesc}
                                    </p>
                                  )}
                                  {plan.rejection_reason && (
                                    <Alert variant="destructive" className="mb-xs">
                                      <AlertDescription className="break-words">
                                        <strong>Rejection reason:</strong> {plan.rejection_reason}
                                      </AlertDescription>
                                    </Alert>
                                  )}
                                  <div className="flex flex-wrap items-center gap-xs">
                                    <span className="text-caption text-muted-foreground">
                                      v{plan.version}
                                    </span>
                                    <span className="text-caption text-muted-foreground">
                                      {plan.entry_count} {plan.entry_count === 1 ? 'entry' : 'entries'}
                                    </span>
                                    {plan.approved_at && (
                                      <span className="text-caption text-muted-foreground">
                                        Approved {formatDate(plan.approved_at)}
                                      </span>
                                    )}
                                    <div className="flex-1" />
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        navigate(`/test-plans/${plan.id}`);
                                      }}
                                    >
                                      View Details
                                      <SquareArrowOutUpRight className="size-3" aria-hidden />
                                    </Button>
                                  </div>
                                </TableCell>
                              </TableRow>
                            )}
                          </React.Fragment>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
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

              <div>
                <div className="mb-xxs flex items-center justify-between">
                  <p className="text-metadata font-semibold">Instructions</p>
                  <CopyButton text={genResult.instructions} label="Copy instructions" />
                </div>
                <div className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
                  {genResult.instructions}
                </div>
              </div>

              <div>
                <p className="mb-xxs text-metadata font-semibold">Run with In-App Agent</p>
                <InAppAgentPanel
                  prompt={genResult.instructions}
                  contextLabel={`generation of plan #${genResult.plan_id}`}
                />
              </div>
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
                      <div className="max-w-40">
                        <Label htmlFor="gen-min-risk">Min Risk Score</Label>
                        <Input
                          id="gen-min-risk"
                          value={genForm.minRisk}
                          onChange={(e) => dispatchGenForm({ type: 'setMinRisk', value: e.target.value })}
                          type="number"
                          min={0}
                          max={100}
                        />
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
