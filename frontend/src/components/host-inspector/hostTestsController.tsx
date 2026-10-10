/**
 * One host's tests, held once and shared (5.322.0).
 *
 * A test is shown in two places on the host page: on the weakness it confirms
 * (the Weaknesses section) and in the Tests list. Both read this controller,
 * so a result recorded from either shows in both at once, and there is ONE
 * place a result is recorded — the side panel below, which keeps the command,
 * what counts as a finding and the weakness in view while the analyst pastes
 * what the tool printed.
 *
 * The whole list is loaded (a host carries tens of tests, not thousands), so
 * the status counts and the weakness markers are derived here, never asked
 * for again.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, Loader2 } from 'lucide-react';

import {
  createHostTests,
  listHostTests,
  listProposals,
  recordHostTestResult,
  type HostTest,
  type HostTestCreateBody,
  type HostTestOutcome,
  type HostTestPage,
  type HostTestPriority,
  type HostTestResultBody,
  type PromotedEvidence,
} from '../../services/api';
import { useAgentTask } from '../../hooks/useAgentTask';
import { useOpeningKey } from '../../hooks/useOpeningKey';
import { useProjectId } from '../../hooks/useProjectId';
import { queryErrorText } from '../../lib/query';
import { formatApiError } from '../../utils/apiErrors';
import { copyToClipboard } from '../../utils/clipboard';
import { resolveCommand } from '../../utils/hostTests';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { hostEvidenceKey } from './HostEvidenceSection';
import { openHostTest, type AddTestTarget, type HostTestsController } from './hostTestsContext';
import {
  SideSheet,
  SideSheetBody,
  SideSheetContent,
  SideSheetFooter,
  SideSheetHeader,
  SideSheetTitle,
} from '../ui/side-sheet';

/** A host's tests are read in one request; past this the list says so. */
export const HOST_TESTS_LIMIT = 200;

/** The key of the one read of a host's tests. */
const hostTestsKey = (projectId: number, hostId: number) =>
  ['listHostTests', projectId, { host_id: hostId, limit: HOST_TESTS_LIMIT }] as const;

export {
  HostTestsProvider, OPEN_HOST_TEST_EVENT, openHostTest, useHostTests,
} from './hostTestsContext';
export type { AddTestTarget, HostTestsController } from './hostTestsContext';

const OUTCOMES: Array<{ value: HostTestOutcome; label: string; hint: string }> = [
  { value: 'finding', label: 'Finding', hint: 'It showed the issue. Closes the test; you can promote it next.' },
  { value: 'no_finding', label: 'No finding', hint: 'It ran and the issue was not there. Closes the test.' },
  { value: 'inconclusive', label: 'Inconclusive', hint: 'It ran; the output does not settle it. The test stays open.' },
  { value: 'failed', label: 'Could not run', hint: 'It did not run. The test stays open and the host is not counted as tested.' },
];

const newKey = (): string =>
  (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
    ? crypto.randomUUID()
    : `r-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export const CopyButton: React.FC<{ text: string; label?: string }> = ({ text, label = 'Copy command' }) => {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button" variant="ghost" size="icon" className="size-7 shrink-0"
      aria-label={label} title={copied ? 'Copied' : label}
      onClick={(e) => {
        e.stopPropagation();
        void copyToClipboard(text).then((ok) => {
          if (!ok) return;
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check className="size-3.5 text-success" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
    </Button>
  );
};

interface ResultPanelProps {
  test: HostTest | null;
  onClose: () => void;
  onSaved: (updated: HostTest) => void;
  /** The test changed underneath: re-read and hand back the fresh copy. */
  onStale: (id: number) => Promise<HostTest | null>;
  onDraft: (dirty: boolean) => void;
}

/** The test changed underneath a result: what the list holds for it now. */
type StaleTest = Error & { staleTest: true; fresh: HostTest | null };
const staleTest = (fresh: HostTest | null): StaleTest =>
  Object.assign(new Error('The test changed while it was open.'), { staleTest: true as const, fresh });
const isStaleTest = (err: unknown): err is StaleTest =>
  err instanceof Error && (err as Partial<StaleTest>).staleTest === true;

const ResultPanel: React.FC<ResultPanelProps> = ({ test, onClose, onSaved, onStale, onDraft }) => {
  const projectId = useProjectId();
  const [current, setCurrent] = useState<HostTest | null>(test);
  const [outcome, setOutcome] = useState<HostTestOutcome | ''>('');
  const [summary, setSummary] = useState('');
  const [output, setOutput] = useState('');
  // One key per opening, so a double click stores one record.
  const [requestKey, setRequestKey] = useState(newKey);
  const [error, setError] = useState<string | null>(null);
  // (Each opening — and another test — is a new panel: the controller keys it,
  // `useOpeningKey`.  `current` is the test it was opened for, replaced only
  // by the fresh copy after a stale save; it stays while the panel closes.)

  const dirty = current != null && (summary.trim().length > 0 || output.length > 0);
  useEffect(() => { onDraft(dirty); }, [dirty, onDraft]);

  // One request records the result and moves the test.  A refusal for a stale
  // revision reads the test again before it settles, so Save stays busy until
  // the fresh copy is in hand.
  const record = useMutation({
    mutationFn: async ({ id, body }: { id: number; body: HostTestResultBody }) => {
      try {
        return await recordHostTestResult(projectId, id, body);
      } catch (err) {
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 409) throw staleTest(await onStale(id));
        throw err;
      }
    },
  });
  const saving = record.isPending;

  const save = () => {
    if (!current || !outcome) return;
    setError(null);
    // These answer only while the panel is on screen: it goes with its host's
    // inspector, and a save that answers after that has nothing to update and
    // must not set off the host's re-reads.
    record.mutate({
      id: current.id,
      body: {
        expected_revision: current.revision,
        request_key: requestKey,
        outcome,
        summary: summary.trim(),
        ...(output ? { raw_output: output } : {}),
      },
    }, {
      onSuccess: (res) => {
        onDraft(false);
        onSaved(res.test);
      },
      onError: (err) => {
        if (isStaleTest(err)) {
          // Keep what was typed; take the fresh revision so Save works again.
          if (err.fresh) setCurrent(err.fresh);
          // A new key with the fresh copy: if the first attempt did land (a lost
          // response), what is saved next is a further result, never a silent
          // replay of the old one.
          setRequestKey(newKey());
          setError('Someone changed this test while you had it open. It has been read again — check it below, then save.');
        } else {
          setError(formatApiError(err, 'Could not save the result.'));
        }
      },
    });
  };

  const command = current?.command
    ? resolveCommand(current.command, current.host_ip, current.target_fqdn)
    : null;

  return (
    <SideSheet open={test != null} onOpenChange={(next) => { if (!next && !saving) onClose(); }}>
      <SideSheetContent width="lg" aria-describedby={undefined}>
        <SideSheetHeader>
          <SideSheetTitle>Record result</SideSheetTitle>
        </SideSheetHeader>
        {current && (
          <>
            <SideSheetBody className="flex min-w-0 flex-col gap-md">
              <div className="min-w-0 space-y-xxs">
                {current.issue_title && (
                  <p className="break-words text-caption text-muted-foreground">
                    Confirms: <span className="text-foreground">{current.issue_title}</span>
                  </p>
                )}
                <p className="break-words text-metadata font-semibold">
                  {current.tool ? `${current.tool} · ` : ''}{current.description}
                </p>
                <p className="font-mono text-caption text-muted-foreground">
                  {current.host_ip}{current.target_fqdn ? ` · ${current.target_fqdn}` : ''}
                </p>
              </div>
              {command && (
                <div className="flex min-w-0 items-start gap-xs rounded-control bg-accent p-xs">
                  <pre className="m-0 min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-caption">{command}</pre>
                  <CopyButton text={command} />
                </div>
              )}
              {current.expected_result && (
                <p className="break-words text-metadata text-muted-foreground">
                  <span className="font-semibold text-foreground">What counts as a finding: </span>
                  {current.expected_result}
                </p>
              )}

              <fieldset>
                <legend className="text-metadata font-semibold">What did it show?</legend>
                <div className="mt-xxs flex flex-wrap gap-xs" role="radiogroup" aria-label="Outcome">
                  {OUTCOMES.map((o) => (
                    <Button
                      key={o.value} type="button" size="sm" role="radio" aria-checked={outcome === o.value}
                      variant={outcome === o.value ? 'default' : 'outline'} title={o.hint}
                      onClick={() => setOutcome(o.value)}
                    >
                      {o.label}
                    </Button>
                  ))}
                </div>
                <p className="mt-xxs min-h-5 text-caption text-muted-foreground">
                  {outcome ? OUTCOMES.find((o) => o.value === outcome)?.hint : 'Choose one.'}
                </p>
              </fieldset>
              <div>
                <Label htmlFor="host-test-result-summary">Summary</Label>
                <Input
                  id="host-test-result-summary" value={summary} maxLength={500}
                  onChange={(e) => setSummary(e.target.value)}
                  placeholder="What it showed, in a sentence."
                />
              </div>
              <div className="flex min-h-0 flex-1 flex-col">
                <Label htmlFor="host-test-result-output">Output (optional)</Label>
                <Textarea
                  id="host-test-result-output" value={output} rows={10}
                  onChange={(e) => setOutput(e.target.value)}
                  className="min-h-40 flex-1 font-mono text-caption"
                  placeholder="Paste what the tool printed. It is kept with the result, unchanged."
                />
              </div>
              {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}
            </SideSheetBody>
            <SideSheetFooter>
              <Button variant="outline" disabled={saving} onClick={onClose}>Cancel</Button>
              <Button disabled={saving || !outcome || summary.trim().length === 0} onClick={save}>
                {saving && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Save result
              </Button>
            </SideSheetFooter>
          </>
        )}
      </SideSheetContent>
    </SideSheet>
  );
};

const PRIORITIES: HostTestPriority[] = ['critical', 'high', 'medium', 'low', 'info'];
const asPriority = (value: string | null | undefined): HostTestPriority =>
  (PRIORITIES as string[]).includes((value ?? '').toLowerCase())
    ? ((value as string).toLowerCase() as HostTestPriority)
    : 'medium';

interface AddPanelProps {
  /** Null = closed; `{}` = a test about nothing scanned. */
  target: { confirms?: AddTestTarget } | null;
  hostId: number;
  userId?: number;
  onClose: () => void;
  onSaved: (created: HostTest) => void;
  onDraft: (dirty: boolean) => void;
}

/** A person writes a test for this host — the same row an agent proposes. */
const AddTestPanel: React.FC<AddPanelProps> = ({ target, hostId, userId, onClose, onSaved, onDraft }) => {
  const confirms = target?.confirms;
  const [description, setDescription] = useState('');
  const [tool, setTool] = useState('');
  const [command, setCommand] = useState('');
  const [expected, setExpected] = useState('');
  const [rationale, setRationale] = useState('');
  // (Each opening is a new panel — the controller keys it, `useOpeningKey` —
  // so every field below is simply its initial value.)
  const [priority, setPriority] = useState<HostTestPriority>(() => asPriority(target?.confirms?.severity));
  const [mine, setMine] = useState(true);
  // One key per opening, so a double click stores one test.
  const [requestKey] = useState(newKey);
  const projectId = useProjectId();
  const create = useMutation({ mutationFn: (body: HostTestCreateBody) => createHostTests(projectId, [body]) });
  const saving = create.isPending;
  const error = queryErrorText(create.error, 'Could not add the test.');

  const dirty = target != null
    && [description, tool, command, expected, rationale].some((v) => v.trim().length > 0);
  useEffect(() => { onDraft(dirty); }, [dirty, onDraft]);

  // The weakness is the reason when the test confirms one; otherwise the
  // analyst says why.
  const why = rationale.trim() || (confirms ? `To confirm the scanner observation “${confirms.title}”.` : '');
  const ready = description.trim().length > 0 && tool.trim().length > 0 && why.length > 0;

  const save = () => {
    if (!ready) return;
    create.mutate({
      request_key: requestKey,
      host_id: hostId,
      tool: tool.trim(),
      description: description.trim(),
      rationale: why,
      priority,
      ...(command.trim() ? { command: command.trim() } : {}),
      ...(expected.trim() ? { expected_result: expected.trim() } : {}),
      ...(mine && userId != null ? { assigned_to_id: userId } : {}),
      ...(confirms ? { vulnerability_id: confirms.vulnerabilityId } : {}),
    }, {
      // Answered only while the panel is on screen: if the analyst stepped to
      // another host meanwhile, the test belongs to no list on screen, and
      // must not ask the next host's list to open it.
      onSuccess: (res) => {
        onDraft(false);
        onSaved(res.items[0]);
      },
    });
  };

  return (
    <SideSheet open={target != null} onOpenChange={(next) => { if (!next && !saving) onClose(); }}>
      <SideSheetContent width="lg" aria-describedby={undefined}>
        <SideSheetHeader>
          <SideSheetTitle>Add test</SideSheetTitle>
        </SideSheetHeader>
        <SideSheetBody className="flex min-w-0 flex-col gap-md">
          {confirms ? (
            <p className="break-words text-caption text-muted-foreground">
              Confirms: <span className="text-foreground">{confirms.title}</span>
            </p>
          ) : (
            <p className="text-caption text-muted-foreground">
              A check you intend to run on this host. Its result is recorded on the test afterwards.
            </p>
          )}
          <div>
            <Label htmlFor="host-test-add-description">What to check</Label>
            <Input
              id="host-test-add-description" value={description} maxLength={500}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Anonymous FTP login is allowed"
            />
          </div>
          <div className="flex min-w-0 flex-wrap gap-sm">
            <div className="min-w-0 flex-1">
              <Label htmlFor="host-test-add-tool">Tool</Label>
              <Input
                id="host-test-add-tool" value={tool} maxLength={100}
                onChange={(e) => setTool(e.target.value)}
                placeholder="nmap, curl, by hand…"
              />
            </div>
            <div>
              <Label htmlFor="host-test-add-priority">Priority</Label>
              <Select value={priority} onValueChange={(v) => setPriority(v as HostTestPriority)}>
                <SelectTrigger id="host-test-add-priority" className="h-9 w-32"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PRIORITIES.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <Label htmlFor="host-test-add-command">Command (optional)</Label>
            <Textarea
              id="host-test-add-command" value={command} rows={2} maxLength={10000}
              onChange={(e) => setCommand(e.target.value)}
              className="font-mono text-caption"
              placeholder="{ip} and {fqdn} are filled in with this host's address and name."
            />
          </div>
          <div>
            <Label htmlFor="host-test-add-expected">What counts as a finding (optional)</Label>
            <Input
              id="host-test-add-expected" value={expected} maxLength={500}
              onChange={(e) => setExpected(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="host-test-add-rationale">{confirms ? 'Why (optional)' : 'Why'}</Label>
            <Textarea
              id="host-test-add-rationale" value={rationale} rows={2} maxLength={10000}
              onChange={(e) => setRationale(e.target.value)}
              placeholder={confirms ? 'Left empty, the weakness above is the reason.' : 'What made this worth checking.'}
            />
          </div>
          {userId != null && (
            <label className="flex items-center gap-xs text-metadata">
              <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} />
              Assign it to me
            </label>
          )}
          {error && <p role="alert" className="break-words text-caption text-destructive">{error}</p>}
        </SideSheetBody>
        <SideSheetFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>Cancel</Button>
          <Button disabled={saving || !ready} onClick={save}>
            {saving && <Loader2 className="size-3.5 animate-spin" aria-hidden />} Add test
          </Button>
        </SideSheetFooter>
      </SideSheetContent>
    </SideSheet>
  );
};

export interface HostTestsControllerOptions {
  hostId: number;
  canEdit: boolean;
  userId?: number;
  /** A result was recorded: the host's "tested" fact and other evidence changed. */
  onResultRecorded?: () => void;
  /** A finding was created or joined from a result. */
  onFindingCreated?: (findingId: number, made?: PromotedEvidence) => void;
}

/** Load the host's tests and own the result panel. Returns the controller to
 *  provide and the element to render once (the panel and the agent dialog).
 *
 *  One host for the life of the hook: its owner is keyed by the host (the
 *  inspector, or the standalone section), so `hostId` never changes here. */
export const useHostTestsController = ({
  hostId, canEdit, userId, onResultRecorded, onFindingCreated,
}: HostTestsControllerOptions): { controller: HostTestsController; element: React.ReactNode } => {
  const projectId = useProjectId();
  const queryClient = useQueryClient();
  const [staleNotice, setStaleNotice] = useState(false);
  const [resultFor, setResultFor] = useState<HostTest | null>(null);
  const [resultDraft, setResultDraft] = useState(false);
  const [addFor, setAddFor] = useState<{ confirms?: AddTestTarget } | null>(null);
  const [addDraft, setAddDraft] = useState(false);
  // Each opening of a panel — and, for a result, another test — starts clean.
  const resultOpening = useOpeningKey(resultFor?.id ?? null);
  const addOpening = useOpeningKey(addFor);
  const { give: giveAgent, allowed: canAskAgent, dialog: agentDialog } = useAgentTask();

  // THE read of this host's tests: the Weaknesses rows and the Tests section
  // both get it from the controller, so there is one query for it.
  const testsQuery = useQuery({
    queryKey: hostTestsKey(projectId, hostId),
    queryFn: ({ signal }) => listHostTests(projectId, { host_id: hostId, limit: HOST_TESTS_LIMIT }, signal),
  });
  const tests = testsQuery.data?.items ?? null;
  const total = testsQuery.data?.total ?? 0;
  const loading = testsQuery.isFetching;
  const error = queryErrorText(testsQuery.error, 'Tests could not be loaded.');
  const { refetch: refetchTests } = testsQuery;
  const reload = useCallback(async (): Promise<HostTest[]> => {
    const read = await refetchTests();
    return read.isError ? [] : read.data?.items ?? [];
  }, [refetchTests]);

  // An agent's pending "this is a finding" proposals, by the evidence they
  // cite. A failure here only hides a shortcut to the Proposals page.
  const proposalsKey = useMemo(
    () => ['listProposals', projectId, { host_id: hostId, status: 'pending', kind: 'finding_create', limit: 100 }] as const,
    [projectId, hostId],
  );
  const proposalsQuery = useQuery({
    queryKey: proposalsKey,
    queryFn: ({ signal }) => listProposals(projectId, { host_id: hostId, status: 'pending', kind: 'finding_create', limit: 100 }, signal),
  });
  const pendingProposals = proposalsQuery.isError ? undefined : proposalsQuery.data?.items;
  const proposalByEvidence = useMemo(() => {
    const map: Record<number, number> = {};
    for (const pr of pendingProposals ?? []) for (const id of pr.evidence_ids ?? []) map[id] = pr.id;
    return map;
  }, [pendingProposals]);

  const replace = useCallback((updated: HostTest) => {
    setStaleNotice(false);
    queryClient.setQueryData<HostTestPage>(hostTestsKey(projectId, hostId), (prev) => (
      prev ? { ...prev, items: prev.items.map((t) => (t.id === updated.id ? updated : t)) } : prev
    ));
  }, [queryClient, projectId, hostId]);

  // What else on the host page a result changes: the host's "tested" fact and
  // the scanner row a linked result promotes (the inspector's `getHost`), and
  // the evidence that answers no test.  Nothing is listening when the Tests
  // section stands alone.
  const hostEvidenceChanged = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['getHost', projectId, hostId] });
    void queryClient.invalidateQueries({ queryKey: hostEvidenceKey(projectId, hostId) });
  }, [queryClient, projectId, hostId]);

  const controller = useMemo<HostTestsController>(() => ({
    hostId, canEdit, userId, tests, total, loading, error, reload, replace,
    staleNotice,
    markStale: () => setStaleNotice(true),
    clearStale: () => setStaleNotice(false),
    openResult: (test) => setResultFor(test),
    openAdd: (confirms) => setAddFor({ confirms }),
    proposalByEvidence,
    askAgent: (instruction) => { void giveAgent(instruction); },
    canAskAgent,
    resultDraft: resultDraft || addDraft,
    onFindingCreated: (findingId, made) => {
      onFindingCreated?.(findingId, made);
      // The test now names its finding, the agent's proposal for the same
      // result may be settled, and the host has a finding it did not.
      void queryClient.invalidateQueries({ queryKey: hostTestsKey(projectId, hostId) });
      void queryClient.invalidateQueries({ queryKey: proposalsKey });
      void queryClient.invalidateQueries({ queryKey: ['listFindings'] });
      hostEvidenceChanged();
    },
  }), [
    hostId, canEdit, userId, tests, total, loading, error, reload, replace, staleNotice,
    proposalByEvidence, giveAgent, canAskAgent, resultDraft, addDraft, onFindingCreated,
    queryClient, projectId, proposalsKey, hostEvidenceChanged,
  ]);

  const element = (
    <>
      <ResultPanel
        key={`result-${resultOpening}`}
        test={resultFor}
        onClose={() => { setResultFor(null); setResultDraft(false); }}
        onDraft={setResultDraft}
        onSaved={(updated) => {
          replace(updated);
          setResultFor(null);
          hostEvidenceChanged();
          onResultRecorded?.();
        }}
        onStale={async (id) => (await reload()).find((t) => t.id === id) ?? null}
      />
      <AddTestPanel
        key={`add-${addOpening}`}
        target={addFor}
        hostId={hostId}
        userId={userId}
        onClose={() => { setAddFor(null); setAddDraft(false); }}
        onDraft={setAddDraft}
        onSaved={(created) => {
          setStaleNotice(false);
          const key = hostTestsKey(projectId, hostId);
          if (queryClient.getQueryData<HostTestPage>(key)) {
            queryClient.setQueryData<HostTestPage>(key, (prev) => (prev ? {
              ...prev,
              items: [created, ...prev.items.filter((t) => t.id !== created.id)],
              total: prev.total + 1,
            } : prev));
          } else {
            // The list never loaded: read it, now with the new test in it.
            void queryClient.invalidateQueries({ queryKey: key });
          }
          setAddFor(null);
          openHostTest(created.id);
        }}
      />
      {agentDialog}
    </>
  );

  return { controller, element };
};
