/**
 * Tests on this host — the to-do list of checks proposed for it, by a person
 * or an agent (5.320.0; reshaped 5.322.0 after the design review).
 *
 * Every test is ONE row: status, priority, what it checks, what its results
 * come to, and the one action — Record result. Its command sits on a second
 * quiet line with a copy button, because the command and that button are what
 * the analyst came for. Everything else (why, who proposed it, the evidence,
 * promoting a result) is one click away. An open test used to be ~340 px
 * beside 50 px weakness rows.
 *
 * The list is held by `hostTestsController`, which the Weaknesses section
 * reads too: a test that confirms a weakness is also shown on that weakness.
 * Recording a result happens in the controller's side panel. A row opens by
 * itself when its result showed an issue and nobody has made a finding of it
 * — the next step must not be hidden behind a collapsed row.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Bot, ChevronDown, ChevronRight, ClipboardList, Loader2, MoreHorizontal, Plus, RefreshCw } from 'lucide-react';

import {
  createFindingFromEvidence,
  PromotedEvidence,
  listEvidenceRecords,
  updateHostTest,
  type EvidenceRecord,
  type HostTest,
} from '../../services/api';
import { agentInstruction } from '../../utils/agentRuns';
import { formatApiError } from '../../utils/apiErrors';
import { cn } from '../../utils/cn';
import {
  hostTestStatusLabel,
  hostTestStatusVariant,
  resolveCommand,
  stripAgentMark,
  testNeedsWork,
  testResultState,
  type ResultTone,
} from '../../utils/hostTests';
import { formatRelativeTime } from '../../utils/relativeTime';
import SafeMarkdown from '../SafeMarkdown';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../ui/dropdown-menu';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Textarea } from '../ui/textarea';
import { EvidenceItem } from './HostEvidenceSection';
import { InspectorSection, openInspectorSection } from './InspectorSection';
import { openIssue } from './VulnerabilityGroup';
import {
  CopyButton,
  HOST_TESTS_LIMIT,
  HostTestsProvider,
  OPEN_HOST_TEST_EVENT,
  useHostTests,
  useHostTestsController,
  type HostTestsController,
} from './hostTestsController';

const EVIDENCE_PAGE = 10;

type Filter = 'active' | 'done' | 'dismissed' | 'all';

const FILTERS: Array<{ value: Filter; label: string; empty: string }> = [
  { value: 'active', label: 'To do', empty: 'No tests to do on this host.' },
  { value: 'done', label: 'Done', empty: 'No finished tests on this host.' },
  { value: 'dismissed', label: 'Dismissed', empty: 'No dismissed tests on this host.' },
  { value: 'all', label: 'All', empty: 'No tests on this host yet.' },
];

const PRIORITY_VARIANT: Record<string, React.ComponentProps<typeof Badge>['variant']> = {
  critical: 'severity-critical',
  high: 'severity-high',
  medium: 'severity-medium',
  low: 'severity-low',
  info: 'severity-info',
};

const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;
type FindingSeverity = (typeof SEVERITIES)[number];

export const TONE_CLASS: Record<ResultTone, string> = {
  warn: 'text-warning',
  ok: 'text-success',
  info: 'text-info',
  muted: 'text-muted-foreground',
};

const isActive = (status: string): boolean => status === 'proposed' || status === 'in_progress';
/** "To do" is what needs a person, which includes a finished test whose result
 *  nobody has decided on; "Done" is the rest of the finished ones. */
const filterOf = (t: HostTest): Exclude<Filter, 'all'> =>
  testNeedsWork(t) ? 'active' : t.status === 'dismissed' ? 'dismissed' : 'done';
const inFilter = (t: HostTest, f: Filter): boolean => f === 'all' || filterOf(t) === f;

/** The step after a result that showed an issue. A test that confirms a
 *  scanner observation promotes THAT observation (one click — the issue names
 *  and rates the finding, and it joins the issue's finding if there is one);
 *  any other test makes a new finding, title and severity prefilled. */
const PromoteEvidence: React.FC<{
  rec: EvidenceRecord;
  test: HostTest;
  onCreated: (made: PromotedEvidence) => void;
}> = ({ rec, test, onCreated }) => {
  const linked = !!test.issue_key;
  // The result says what was found; the test's description says what was
  // checked ("Confirm missing X…"), which is not a finding's name.
  const [title, setTitle] = useState(
    (rec.summary.replace(/^Legacy \w+: /, '').split('\n')[0] || test.description).slice(0, 200),
  );
  const [severity, setSeverity] = useState<FindingSeverity>(
    (SEVERITIES as readonly string[]).includes(test.priority) ? (test.priority as FindingSeverity) : 'medium',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      // Title and severity go with a linked test too, but the server uses
      // them only if the observation it named has since left the host: while
      // it is there, the observation names and rates its own finding.
      const made = await createFindingFromEvidence(rec.id, { title: title.trim(), severity });
      onCreated(made);
    } catch (err) {
      setError(formatApiError(err, 'Could not create the finding.'));
    } finally {
      setBusy(false);
    }
  };

  if (linked) {
    return (
      <div className="space-y-xxs">
        <div className="flex min-w-0 flex-wrap items-center gap-xs">
          <Button size="sm" disabled={busy} onClick={() => void create()}>
            {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Promote to finding
          </Button>
          <span className="min-w-0 break-words text-caption text-muted-foreground">
            Confirms “{test.issue_title ?? 'the weakness'}” on this host. Joins the issue&rsquo;s finding if it has one.
          </span>
        </div>
        {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}
      </div>
    );
  }
  // The form is shown at once: a button that only revealed a second button
  // with the same label read as a click that did not take (5.323.0).
  return (
    <div className="space-y-xs rounded-panel border border-border p-xs">
      <div className="flex min-w-0 flex-wrap items-end gap-xs">
        <div className="min-w-0 flex-1">
          <Label htmlFor={`promote-title-${rec.id}`}>Finding title</Label>
          <Input id={`promote-title-${rec.id}`} value={title} maxLength={500} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Label htmlFor={`promote-severity-${rec.id}`}>Severity</Label>
          <Select value={severity} onValueChange={(v) => setSeverity(v as FindingSeverity)}>
            <SelectTrigger id={`promote-severity-${rec.id}`} className="h-9 w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SEVERITIES.map((sev) => <SelectItem key={sev} value={sev}>{sev}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>
      <p className="text-caption text-muted-foreground">
        Created as confirmed on this host, with this record as its evidence. Write the report text on the finding.
      </p>
      <div className="flex flex-wrap gap-xs">
        <Button size="sm" disabled={busy || title.trim().length === 0} onClick={() => void create()}>
          {busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Create finding
        </Button>
      </div>
      {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}
    </div>
  );
};

/** The evidence records that answer one test, loaded when the test is opened. */
const TestEvidence: React.FC<{ test: HostTest; ctl: HostTestsController }> = ({ test, ctl }) => {
  const testId = test.id;
  const count = test.evidence_count;
  const [items, setItems] = useState<EvidenceRecord[] | null>(null);
  const [total, setTotal] = useState(count);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async (offset: number) => {
    setLoading(true);
    try {
      const page = await listEvidenceRecords({ host_test_id: testId, limit: EVIDENCE_PAGE, offset });
      setItems((prev) => (offset === 0 ? page.items : [...(prev ?? []), ...page.items]));
      setTotal(page.total);
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Could not load the evidence for this test.'));
    } finally {
      setLoading(false);
    }
  };

  // Again whenever a result is added (the count is the signal).
  useEffect(() => {
    if (count > 0) void load(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [testId, count]);

  if (count === 0) {
    return (
      <p className="text-caption text-muted-foreground">
        No result recorded yet, so the host is not counted as tested by this test.
      </p>
    );
  }
  if (error && !items) {
    return (
      <p role="alert" className="text-caption text-destructive">
        {error} <Button variant="ghost" size="sm" onClick={() => void load(0)}>Retry</Button>
      </p>
    );
  }
  if (!items) return <p role="status" className="text-caption text-muted-foreground">Loading evidence…</p>;
  return (
    <div className="min-w-0">
      <p className="text-caption font-semibold text-muted-foreground">
        Evidence · {total.toLocaleString()} record{total === 1 ? '' : 's'}
      </p>
      <ul className="mt-xxs space-y-sm border-l-2 border-border pl-sm">
        {items.map((rec) => {
          const proposalId = ctl.proposalByEvidence[rec.id];
          const unpromoted = rec.outcome === 'finding' && rec.finding_id == null;
          return (
            <li key={rec.id} className="min-w-0 space-y-xxs" data-evidence={rec.id}>
              <EvidenceItem rec={rec} />
              {unpromoted && proposalId != null && (
                <p className="text-caption">
                  <Link to="/proposals" className="text-info hover:underline">
                    Your agent proposed a finding from this. Review proposal #{proposalId}
                  </Link>
                </p>
              )}
              {ctl.canEdit && unpromoted && (
                <PromoteEvidence
                  rec={rec}
                  test={test}
                  onCreated={(made) => {
                    setItems((prev) => prev?.map((r) => (r.id === rec.id ? { ...r, finding_id: made.finding_id } : r)) ?? prev);
                    ctl.onFindingCreated(made.finding_id, made);
                  }}
                />
              )}
            </li>
          );
        })}
      </ul>
      {error && <p role="alert" className="text-caption text-destructive">{error}</p>}
      {items.length < total && (
        <Button variant="ghost" size="sm" disabled={loading} onClick={() => void load(items.length)}>
          Show more evidence ({(total - items.length).toLocaleString()} left)
        </Button>
      )}
    </div>
  );
};

/** One test, one row (two lines when it has a command). */
export const HostTestRow: React.FC<{
  test: HostTest;
  ctl: HostTestsController;
  defaultOpen?: boolean;
  /** The test a link brought the reader to (`#host-test-<id>`): marked, so it
   *  is plain which of the host's tests they came for. */
  linked?: boolean;
  onDirty?: (id: number, dirty: boolean) => void;
}> = ({ test, ctl, defaultOpen = false, linked = false, onDirty }) => {
  const needsDecision = (test.unpromoted_findings ?? 0) > 0;
  const [open, setOpen] = useState(defaultOpen || needsDecision);
  // A link can arrive while the row is already mounted (the inspector stays
  // mounted across hosts and hash changes).
  useEffect(() => { if (defaultOpen) setOpen(true); }, [defaultOpen]);
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A result that showed an issue opens the row: the next step is inside it.
  useEffect(() => { if (needsDecision) setOpen(true); }, [needsDecision]);

  const dirty = dismissing && reason.trim().length > 0;
  useEffect(() => { onDirty?.(test.id, dirty); }, [dirty, onDirty, test.id]);
  useEffect(() => () => onDirty?.(test.id, false), [onDirty, test.id]);

  const patch = async (change: Parameters<typeof updateHostTest>[1]) => {
    setSaving(true);
    setError(null);
    try {
      ctl.replace(await updateHostTest(test.id, change));
      setDismissing(false);
      setReason('');
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 409) {
        ctl.markStale();
        void ctl.reload();
      } else {
        setError(formatApiError(err, 'Could not save the change.'));
      }
    } finally {
      setSaving(false);
    }
  };

  const active = isActive(test.status);
  const canClaim = ctl.userId != null && active && test.assigned_to_id !== ctl.userId;
  const Chevron = open ? ChevronDown : ChevronRight;
  const state = testResultState(test);
  const command = test.command ? resolveCommand(test.command, test.host_ip, test.target_fqdn) : null;
  const rationale = stripAgentMark(test.rationale);
  const who = test.created_by ?? 'unknown';
  const provenance = [
    test.source === 'agent'
      ? `Proposed by ${[test.agent_client, test.agent_model].filter(Boolean).join(' · ') || 'an agent'} for ${who}`
      : `Proposed by ${who}`,
    formatRelativeTime(test.created_at, { fallback: '' }),
    test.assigned_to ? `assigned to ${test.assigned_to}` : 'unassigned',
    test.label,
    test.target_fqdn ? `aimed at ${test.target_fqdn}` : null,
  ].filter(Boolean).join(' · ');
  const links = (test.references ?? []).filter((r) => /^https?:\/\//i.test(r));

  return (
    <li
      id={`host-test-${test.id}`}
      className={cn(
        'min-w-0 scroll-mt-16 border-b border-border py-xxs last:border-b-0',
        linked && 'rounded bg-primary/10 px-xs ring-2 ring-inset ring-ring',
      )}
      data-testid={`host-test-${test.id}`}
      data-linked={linked ? 'true' : undefined}
      aria-current={linked ? 'true' : undefined}
    >
      {linked && (
        <p className="pt-xxs text-caption font-medium text-primary">The test you opened</p>
      )}
      <div className="flex min-w-0 items-center gap-xs">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-xs rounded py-xxs text-left hover:bg-accent/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Chevron className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <Badge variant={hostTestStatusVariant(test.status)} className="shrink-0 whitespace-nowrap">
            {hostTestStatusLabel(test.status)}
          </Badge>
          <Badge variant={PRIORITY_VARIANT[test.priority] ?? 'muted'} className="shrink-0 whitespace-nowrap">
            {test.priority}
          </Badge>
          <span className="min-w-0 flex-1 truncate text-metadata" title={test.description}>
            {test.tool && <span className="font-semibold">{test.tool} · </span>}
            {test.description}
          </span>
        </button>
        <span
          className={cn('max-w-[14rem] shrink-0 truncate text-caption', TONE_CLASS[state.tone])}
          title={state.label}
          data-testid={`host-test-state-${test.id}`}
        >
          {state.label}
        </span>
        {ctl.canEdit && active && (
          <Button size="sm" className="h-7 shrink-0" disabled={saving} onClick={() => ctl.openResult(test)}>
            Record result
          </Button>
        )}
        {ctl.canEdit && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="icon" variant="ghost" className="size-7 shrink-0" aria-label={`More actions for test ${test.id}`} disabled={saving}>
                <MoreHorizontal className="size-4" aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {canClaim && (
                <DropdownMenuItem onSelect={() => void patch({ expected_revision: test.revision, assigned_to_id: ctl.userId })}>
                  {test.assigned_to_id == null ? 'Claim' : 'Take over'}
                </DropdownMenuItem>
              )}
              {!active && (
                <DropdownMenuItem onSelect={() => void patch({ expected_revision: test.revision, status: 'proposed' })}>
                  Reopen
                </DropdownMenuItem>
              )}
              {test.status === 'done' && (
                <DropdownMenuItem onSelect={() => ctl.openResult(test)}>Record another result</DropdownMenuItem>
              )}
              {test.status !== 'dismissed' && (
                <DropdownMenuItem onSelect={() => { setOpen(true); setError(null); setDismissing(true); }}>
                  Dismiss…
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

      {/* The command is the thing to copy: on the row, not behind a click. */}
      {command && !open && (
        <div className="flex min-w-0 items-center gap-xxs pl-md">
          <code className="min-w-0 flex-1 truncate font-mono text-caption text-muted-foreground" title={command}>
            {command}
          </code>
          <CopyButton text={command} />
        </div>
      )}

      {open && (
        <div className="min-w-0 space-y-xs pb-xs pl-md pt-xxs">
          {test.issue_key && (
            <p className="flex min-w-0 items-baseline gap-xxs text-caption">
              <span className="shrink-0 text-muted-foreground">Confirms:</span>
              <button
                type="button"
                className="min-w-0 truncate text-left text-info hover:underline"
                title={test.issue_title ?? undefined}
                onClick={() => openIssue(test.issue_key)}
              >
                {test.issue_title ?? 'a weakness on this host'}
              </button>
            </p>
          )}
          {command && (
            <div className="flex min-w-0 items-start gap-xs rounded-control bg-accent p-xs">
              <pre className="m-0 min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-caption">{command}</pre>
              <CopyButton text={command} />
            </div>
          )}
          {test.expected_result && (
            <p className="break-words text-metadata text-muted-foreground">
              <span className="font-semibold text-foreground">Expected: </span>{test.expected_result}
            </p>
          )}
          {rationale && (
            <div className="min-w-0 text-metadata">
              <p className="font-semibold">Why</p>
              <SafeMarkdown text={rationale} className="text-muted-foreground" />
            </div>
          )}
          {links.length > 0 && (
            <p className="min-w-0 truncate text-caption">
              {links.map((ref) => (
                <a key={ref} href={ref} target="_blank" rel="noopener noreferrer" className="mr-sm text-info hover:underline" title={ref}>
                  {(() => { try { return new URL(ref).hostname; } catch { return ref; } })()}
                </a>
              ))}
            </p>
          )}
          <p className="break-words text-caption text-muted-foreground">{provenance}</p>
          {test.status === 'dismissed' && test.dismissed_reason && (
            <p className="whitespace-pre-wrap break-words text-metadata">
              <span className="font-semibold">Dismissed: </span>{test.dismissed_reason}
            </p>
          )}
          {test.tester_summary && (
            <p className="whitespace-pre-wrap break-words text-metadata">
              <span className="font-semibold">Tester summary: </span>{test.tester_summary}
            </p>
          )}

          {dismissing && (
            <div className="space-y-xxs rounded-panel border border-border p-xs">
              <Label htmlFor={`host-test-dismiss-${test.id}`}>Why this test should not be run</Label>
              <Textarea
                id={`host-test-dismiss-${test.id}`}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                maxLength={2000}
                rows={2}
              />
              <div className="flex flex-wrap gap-xs">
                <Button size="sm" variant="destructive" disabled={saving || reason.trim().length === 0}
                  onClick={() => void patch({
                    expected_revision: test.revision, status: 'dismissed', dismissed_reason: reason.trim(),
                  })}>
                  Dismiss test
                </Button>
                <Button size="sm" variant="ghost" disabled={saving} onClick={() => { setDismissing(false); setReason(''); }}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
          {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}

          <TestEvidence test={test} ctl={ctl} />
        </div>
      )}
    </li>
  );
};

const SectionBody: React.FC<{ ctl: HostTestsController; onDirtyChange?: (dirty: boolean) => void }> = ({
  ctl, onDirtyChange,
}) => {
  const [dirtyIds, setDirtyIds] = useState<Record<number, boolean>>({});
  const onDirty = React.useCallback((id: number, dirty: boolean) => {
    setDirtyIds((prev) => (!!prev[id] === dirty ? prev : { ...prev, [id]: dirty }));
  }, []);
  const hasDraft = Object.values(dirtyIds).some(Boolean) || ctl.resultDraft;
  useEffect(() => { onDirtyChange?.(hasDraft); }, [hasDraft, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  // A test linked by the URL (`#host-test-12`) is shown whatever its status.
  // Re-read on every navigation: the inspector stays mounted across hosts, so
  // a hash read once at mount pointed at the previous link's test.
  const location = useLocation();
  const linkedId = useMemo(() => {
    const match = typeof window !== 'undefined' ? window.location.hash.match(/^#host-test-(\d+)$/) : null;
    return match ? Number(match[1]) : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key, location.hash]);
  const [filter, setFilter] = useState<Filter>(linkedId != null ? 'all' : 'active');
  const tests = ctl.tests;
  const loaded = tests !== null;
  useEffect(() => {
    if (linkedId == null || !loaded) return;
    // The section may be collapsed (a per-viewer preference) — a link to a
    // test inside it must open it, or the reader lands on a host page with
    // nothing pointing at what they came for.
    setFilter('all');
    openInspectorSection('host-detail-proposed-tests');
    requestAnimationFrame(() => {
      document.getElementById(`host-test-${linkedId}`)?.scrollIntoView?.({ block: 'center' });
    });
  }, [linkedId, loaded]);

  // The weakness row's "open this test" (5.322.0): show it whatever the filter.
  const [openId, setOpenId] = useState<number | null>(null);
  useEffect(() => {
    const onOpen = (e: Event) => {
      const id = (e as CustomEvent<number>).detail;
      setFilter('all');
      setOpenId(id);
      requestAnimationFrame(() => document.getElementById(`host-test-${id}`)?.scrollIntoView?.({ block: 'center' }));
    };
    window.addEventListener(OPEN_HOST_TEST_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_HOST_TEST_EVENT, onOpen);
  }, []);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { active: 0, done: 0, dismissed: 0, all: 0 };
    for (const t of tests ?? []) {
      c.all += 1;
      c[filterOf(t)] += 1;
    }
    return c;
  }, [tests]);
  const shown = (tests ?? []).filter((t) => inFilter(t, filter));
  const runnable = (tests ?? []).filter((t) => isActive(t.status)).length;
  const current = FILTERS.find((f) => f.value === filter)!;

  return (
    <InspectorSection
      id="host-detail-proposed-tests"
      title="Tests"
      titleHint="Checks proposed for this host by a person or an agent. A test's result is the evidence recorded against it."
      icon={<ClipboardList className="size-4 shrink-0 text-primary" aria-hidden />}
      count={loaded ? counts.active : null}
      actions={ctl.canEdit || ctl.canAskAgent ? (
        <div className="flex shrink-0 items-center gap-xs">
          {ctl.canEdit && (
            <Button size="sm" variant="outline" onClick={() => ctl.openAdd()}>
              <Plus className="size-4" aria-hidden /> Add test
            </Button>
          )}
          {ctl.canAskAgent && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="outline">
                  <Bot className="size-4" aria-hidden /> Ask agent
                  <ChevronDown className="size-3.5" aria-hidden />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => ctl.askAgent(agentInstruction.proposeTests([ctl.hostId]))}>
                  Propose tests for this host
                </DropdownMenuItem>
                {runnable > 0 && (
                  <DropdownMenuItem onSelect={() => ctl.askAgent(agentInstruction.runHostTests(ctl.hostId))}>
                    Run the {runnable === 1 ? 'test' : `${runnable} tests`} to do
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      ) : undefined}
    >
      <div className="flex flex-wrap items-center gap-xs">
        <div role="tablist" aria-label="Which tests to show" className="inline-flex overflow-hidden rounded-control border border-border">
          {FILTERS.map((f) => (
            <button
              key={f.value} type="button" role="tab" aria-selected={filter === f.value}
              disabled={hasDraft && filter !== f.value}
              onClick={() => { ctl.clearStale(); setFilter(f.value); }}
              className={cn(
                'h-7 whitespace-nowrap px-sm text-caption focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
                filter === f.value ? 'bg-accent font-semibold text-foreground' : 'text-muted-foreground hover:bg-accent/40',
              )}
            >
              {f.label} <span className="tabular-nums">{counts[f.value]}</span>
            </button>
          ))}
        </div>
        <Button
          size="sm" variant="ghost" disabled={ctl.loading || hasDraft}
          title={hasDraft ? 'Save or clear what you typed first' : 'Read the tests again (an agent may have proposed more)'}
          onClick={() => void ctl.reload()}
        >
          <RefreshCw className={ctl.loading ? 'size-3.5 animate-spin' : 'size-3.5'} aria-hidden /> Refresh
        </Button>
      </div>

      {ctl.staleNotice && (
        <p role="alert" className="pt-xs text-caption text-warning">
          Someone changed that test since you opened it, so your change was not saved. The list has been
          read again. Check the test and repeat your change.
        </p>
      )}

      {ctl.error && !loaded ? (
        <p role="alert" className="py-sm text-metadata text-destructive">
          {ctl.error} <Button variant="ghost" size="sm" onClick={() => void ctl.reload()}>Retry</Button>
        </p>
      ) : !loaded ? (
        <p role="status" className="py-sm text-metadata text-muted-foreground">Loading tests…</p>
      ) : shown.length === 0 ? (
        <p className="py-sm text-metadata text-muted-foreground">{current.empty}</p>
      ) : (
        <>
          <ul className="mt-xs">
            {shown.map((test) => (
              <HostTestRow
                key={`${test.id}:${test.id === openId ? 'o' : ''}`}
                test={test}
                ctl={ctl}
                defaultOpen={test.id === linkedId || test.id === openId}
                linked={test.id === linkedId}
                onDirty={onDirty}
              />
            ))}
          </ul>
          {ctl.error && <p role="alert" className="text-caption text-destructive">{ctl.error}</p>}
          {ctl.total > HOST_TESTS_LIMIT && (
            <p className="pt-xs text-caption text-muted-foreground">
              Showing the newest {HOST_TESTS_LIMIT} of {ctl.total.toLocaleString()} tests on this host.
            </p>
          )}
        </>
      )}
    </InspectorSection>
  );
};

export interface HostTestsSectionProps {
  /** Used when the section stands alone (no controller provided above it). */
  hostId: number;
  canEdit: boolean;
  userId?: number;
  /** Typed-but-unsaved text in the section (the inspector's leave guard). */
  onDirtyChange?: (dirty: boolean) => void;
  onFindingCreated?: (findingId: number, made?: PromotedEvidence) => void;
  onResultRecorded?: () => void;
}

const Standalone: React.FC<HostTestsSectionProps> = ({ onDirtyChange, ...options }) => {
  const { controller, element } = useHostTestsController(options);
  return (
    <HostTestsProvider value={controller}>
      <SectionBody ctl={controller} onDirtyChange={onDirtyChange} />
      {element}
    </HostTestsProvider>
  );
};

/** Reads the controller the host inspector provides; owns one when rendered
 *  by itself. */
export const HostTestsSection: React.FC<HostTestsSectionProps> = (props) => {
  const provided = useHostTests();
  if (provided) return <SectionBody ctl={provided} onDirtyChange={props.onDirtyChange} />;
  // The controller is one host's: another host starts another.
  return <Standalone key={props.hostId} {...props} />;
};

export default HostTestsSection;
