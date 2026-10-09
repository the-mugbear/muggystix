/**
 * Proposals (v5.316.0) — every change an agent (or an AI draft) proposed on
 * this project, for a person to accept or reject: report text, new findings,
 * promoting or dismissing scanner observations, endpoint status.  Accepting
 * applies the change as you; the server decides whether you may (report text
 * needs the finding's author or a project admin) and says so if not.
 *
 * Sections, not cards (UI_STYLE_GUIDE §7): a lead sentence, one strip of
 * measures (pending by kind — each opens its list), then the list.
 */
import React, { useEffect, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, X } from 'lucide-react';

import {
  decideProposals, getProposalSummary, listProposals, Proposal, PROPOSAL_BULK_MAX, ProposalKind,
  ProposalStatus,
} from '../services/api';
import { useProjectRole } from '../hooks/useProjectRole';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { useListCursor } from '../hooks/useListCursor';
import { useListQuery } from '../hooks/useListQuery';
import { PROPOSAL_DECISION_READS } from '../hooks/useProposalDecision';
import { invalidateReads, pollEvery, queryErrorText } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { isPageShortcutEvent } from '../utils/keyboard';
import { FilterChips } from '../components/operations/QueueParts';
import PostureSection from '../components/posture/PostureSection';
import ProposalItem, { REJECT_NOTE_PLACEHOLDER } from '../components/proposals/ProposalItem';
import { Button } from '../components/ui/button';
import { Label } from '../components/ui/label';
import { Textarea } from '../components/ui/textarea';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '../components/ui/select';

const PAGE = 50;

const KINDS: Array<{ kind: ProposalKind; label: string; info: string }> = [
  { kind: 'finding_text', label: 'Report text', info: 'A finding’s description, impact, recommendation, steps, references or CVSS vector.' },
  { kind: 'finding_create', label: 'New findings', info: 'A finding an agent proposes, usually citing its evidence records.' },
  { kind: 'observation_promote', label: 'Confirm observations', info: 'A scanner observation an agent judged real: promote it to a confirmed finding.' },
  { kind: 'observation_dismiss', label: 'Dismiss observations', info: 'A scanner observation an agent judged a false positive (on one host, or every host).' },
  { kind: 'endpoint_status', label: 'Endpoint status', info: 'One affected endpoint marked open, remediated, retest or false positive.' },
];

const STATUSES: ProposalStatus[] = ['pending', 'accepted', 'rejected', 'superseded'];

type Scope = 'mine' | 'all';

/** Where Enter takes the reviewer from a proposal: the finding it is about
 *  (or made), else the host its observation sits on. */
export const proposalDestination = (pr: Proposal): string | null => {
  const findingId = pr.finding_id ?? pr.result_finding_id;
  if (findingId != null) {
    return pr.kind === 'endpoint_status' && pr.finding_host_id != null
      ? `/findings/${findingId}?endpoint=${pr.finding_host_id}#endpoints`
      : `/findings/${findingId}${pr.status === 'pending' ? '#proposals' : ''}`;
  }
  return pr.target.host_id != null ? `/hosts/${pr.target.host_id}` : null;
};

const Proposals: React.FC = () => {
  const toast = useToast();
  const { canWrite: canDecide } = useProjectRole();
  const [confirmDialog, confirm] = useConfirm();
  const [params, setParams] = useSearchParams();
  // A value the page does not know (`?status=all`, a typo, an old link) is the
  // default — pending, every kind — never a request the API refuses: that read
  // "Could not load the proposals." under an empty Status select.  The effect
  // below takes the unknown value out of the address.
  const statusParam = params.get('status');
  const kindParam = params.get('kind');
  const statusKnown = statusParam == null || (STATUSES as string[]).includes(statusParam);
  const kindKnown = kindParam == null || KINDS.some((k) => k.kind === kindParam);
  const status: ProposalStatus = statusParam != null && statusKnown ? (statusParam as ProposalStatus) : 'pending';
  const kind: ProposalKind | undefined = kindParam != null && kindKnown ? (kindParam as ProposalKind) : undefined;
  // The same for the session: `?agent_session_id=abc` was sent to the API as
  // NaN and shown as "session #NaN".  Only a positive whole number is an id.
  const sessionParam = params.get('agent_session_id');
  const sessionKnown = sessionParam == null || /^[1-9]\d*$/.test(sessionParam);
  const sessionId = sessionParam != null && sessionKnown ? Number(sessionParam) : undefined;
  useEffect(() => {
    if (statusKnown && kindKnown && sessionKnown) return;
    const next = new URLSearchParams(params);
    if (!statusKnown) next.delete('status');
    if (!kindKnown) next.delete('kind');
    if (!sessionKnown) next.delete('agent_session_id');
    setParams(next, { replace: true });
  }, [statusKnown, kindKnown, sessionKnown, params, setParams]);
  // 5.318.0 — whose findings: `mine` (authored or owned — what you were
  // notified about) or `all`.  Unset, a project admin sees all and everyone
  // else their own; the server says which (the summary's flag).
  const scopeParam = params.get('scope');
  // The pending counts — the query the top bar's count reads too, so the two
  // are one request and cannot disagree.  It does not depend on the filter:
  // the measures keep their value while another list loads.
  const summaryQuery = useQuery({
    queryKey: ['getProposalSummary'],
    queryFn: ({ signal }) => getProposalSummary(signal),
    ...pollEvery(60_000),
  });
  const summary = summaryQuery.data ?? null;
  // A summary that could not be read is not an admin's: their own.
  const defaultScope: Scope | null = summary
    ? (summary.viewer_is_project_admin ? 'all' : 'mine')
    : summaryQuery.isError ? 'mine' : null;
  const scope: Scope | null = scopeParam === 'mine' || scopeParam === 'all' ? scopeParam : defaultScope;

  const bulkNote = useRef('');
  // The filter is the query's key (R33): a response for an earlier filter
  // never lands.  A slow "pending" response used to replace the "Accepted"
  // list, Accept buttons included.  A re-read keeps every row "Show more" had
  // loaded.
  const filter = { status, kind, agent_session_id: sessionId, mine: scope === 'mine' ? true : undefined };
  const list = useListQuery<Proposal>(
    'listProposals',
    ({ offset, limit, signal }) => listProposals({ ...filter, limit, offset }, signal),
    [filter],
    {
      pageSize: PAGE, poll: 60_000,
      enabled: scope !== null,  // the default is still being read
      errorMessage: 'Could not load the proposals.',
    },
  );
  const { rows: items, total, loadingMore } = list;
  const error = list.error ?? queryErrorText(summaryQuery.error, 'Could not load the proposals.');

  // A decision changes the lists of proposals, the pending counts and the
  // finding it was about: every read of those, here and in the top bar.  A
  // row's own decision says so itself (`useProposalDecision`); this is for
  // "all shown", which is one request of its own.
  const queryClient = useQueryClient();
  const afterDecision = () => invalidateReads(queryClient, ...PROPOSAL_DECISION_READS);

  // "Accept / Reject all shown": ONE request for the whole batch, never
  // several (see `bulk`).
  const bulkDecide = useMutation({
    mutationFn: (v: { ids: number[]; action: 'accept' | 'reject'; note?: string }) =>
      decideProposals(v.ids, v.action, v.note),
    onSuccess: (res, v) => {
      if (res.failed.length) {
        // 5.317.2 — "left pending" was wrong for one already decided or
        // superseded meanwhile; the server's reason says what happened.
        toast.warning(`${res.decided.length} decided; ${res.failed.length} not (${String(res.failed[0].detail)}).`);
      } else {
        toast.success(`${res.decided.length} ${v.action === 'accept' ? 'accepted' : 'rejected'}.`);
      }
      // Busy until the list has been read again.
      return afterDecision();
    },
    onError: (err) => toast.error(formatApiError(err, 'Could not decide them.')),
  });
  const bulkBusy = bulkDecide.isPending;

  const more = async () => {
    try {
      await list.loadMore();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not load more.'));
    }
  };

  // B16 — keyboard review: j/k (↓/↑) move, Enter opens the finding, and on a
  // pending row `a` accepts and `r` opens the reject reason.  The keys press
  // the row's own buttons, so a busy or already-decided row ignores them and
  // there is one code path for a decision.
  const navigate = useNavigate();
  //
  // The cursor is anchored to the PROPOSAL, not to a row number (S2): the list
  // is newest first and re-reads every 60 s, so a proposal arriving shifted
  // every row and the highlight — and `a` — landed on a different one.
  const { cursorId: cursorKey, cursorRowProps } = useListCursor(
    items?.length ?? 0,
    (i) => {
      const to = items ? proposalDestination(items[i]) : null;
      if (to) navigate(to);
    },
    { resetKey: `${status}|${kind ?? ''}|${sessionId ?? ''}|${scope ?? ''}`, getId: (i) => items?.[i]?.id },
  );
  const cursorId = typeof cursorKey === 'number' ? cursorKey : null;
  useEffect(() => {
    if (!canDecide || cursorId === null) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'a' && e.key !== 'r') return;
      // Not while a Select or a menu has the key (their typeahead does not
      // stop it), not on auto-repeat — holding `a` accepted one proposal per
      // repeat — and not while a dialog is open (S1).
      if (!isPageShortcutEvent(e)) return;
      // One decision at a time: while any is in flight (a row's, or "all
      // shown") the list is about to change under the cursor.
      if (bulkBusy || document.querySelector('[data-proposal-action]:disabled')) return;
      const button = document.querySelector<HTMLButtonElement>(
        `[data-proposal="${cursorId}"] [data-proposal-action="${e.key === 'a' ? 'accept' : 'reject'}"]`,
      );
      if (!button || button.disabled) return;
      e.preventDefault();
      button.click();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [canDecide, cursorId, bulkBusy]);

  const setParam = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };

  const bulk = async (action: 'accept' | 'reject') => {
    const shown = (items ?? []).filter((p) => p.status === 'pending');
    if (!shown.length) return;
    // The route decides at most PROPOSAL_BULK_MAX in one request, and refuses
    // a longer list whole (review 2026-10-02 H4).  With more shown, the action
    // takes the FIRST ones in the list's order and says so — one request,
    // never several: the server's competing-drafts rule has to see the whole
    // set it decides.  The list reloads afterwards, so the rest can follow.
    const batch = shown.slice(0, PROPOSAL_BULK_MAX);
    const partial = batch.length < shown.length;
    const verb = action === 'accept' ? 'Accept' : 'Reject';
    const limitNote = partial
      ? `One action decides at most ${PROPOSAL_BULK_MAX} proposals: these are the first ${batch.length} of the ${shown.length} shown, in the list’s order. The other ${shown.length - batch.length} stay pending, and the list reloads so they can follow.`
      : null;
    // 5.317.1 — an optional reason for a bulk rejection (each proposal's
    // decision_note; the proposing agents read it back).
    bulkNote.current = '';
    const ok = await confirm({
      title: partial
        ? `${verb} the first ${batch.length} of ${shown.length} proposals shown?`
        : `${verb} ${shown.length} proposals?`,
      body: action === 'accept'
        ? (
          <div className="space-y-xs">
            <p>Each is applied as you, one by one. Any you may not apply (report text on someone else’s finding), whose target has changed, or that is one of several drafts of the same section (choose those on the finding) is not applied, and the reason is shown.</p>
            {limitNote && <p>{limitNote}</p>}
          </div>
        )
        : (
          <div className="space-y-xs">
            <p id="bulk-reject-note-hint">Each is marked rejected. The agents that proposed them read the decision and this reason.</p>
            {limitNote && <p>{limitNote}</p>}
            <Label htmlFor="bulk-reject-note">Why reject them? (optional)</Label>
            <Textarea id="bulk-reject-note" rows={2} maxLength={2000}
              aria-describedby="bulk-reject-note-hint" placeholder={REJECT_NOTE_PLACEHOLDER}
              onChange={(e) => { bulkNote.current = e.target.value; }} />
          </div>
        ),
      confirmLabel: partial ? `${verb} the first ${batch.length}` : `${verb} all shown`,
    });
    if (!ok) return;
    const note = action === 'reject' ? bulkNote.current.trim() || undefined : undefined;
    bulkDecide.mutate({ ids: batch.map((p) => p.id), action, note });
  };

  const pending = (scope === 'mine' ? summary?.pending_mine : summary?.pending) ?? 0;
  const byKind = (scope === 'mine' ? summary?.by_kind_mine : summary?.by_kind) ?? {};
  const pendingShown = (items ?? []).filter((p) => p.status === 'pending').length;
  const kindChips = status === 'pending' && summary != null && sessionId == null;
  // What the bulk buttons will act on, said on the buttons themselves.
  const bulkScope = pendingShown > PROPOSAL_BULK_MAX
    ? `the first ${PROPOSAL_BULK_MAX} of ${pendingShown} shown`
    : 'all shown';

  return (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      {confirmDialog}
      <div className="min-w-0">
        <h1 className="text-page-title">Proposals</h1>
        <p className="mt-xxs max-w-4xl text-metadata text-muted-foreground">
          {pending === 0
            ? 'Nothing waiting. When an agent (or an AI draft) proposes a change to what the team has concluded, it lands here for a person to accept or reject.'
            : <>
                <strong className="text-foreground">{pending}</strong> proposed change{pending === 1 ? '' : 's'} to
                {scope === 'mine' ? ' your findings' : ' what the team has concluded'} {pending === 1 ? 'is' : 'are'} waiting.
                Nothing has changed yet: accepting one applies it as you.
              </>}
        </p>
      </div>

      <PostureSection
        title={<span>{status === 'pending' ? 'Waiting for a decision' : `${status[0].toUpperCase()}${status.slice(1)}`}</span>}
        description={sessionId != null ? `From agent session #${sessionId}.` : undefined}
        actions={canDecide && status === 'pending' && pendingShown > 0 ? (
          <>
            <Button size="sm" variant="outline" onClick={() => void bulk('accept')} disabled={bulkBusy}>
              {bulkBusy && <Loader2 className="size-4 animate-spin" aria-hidden />} Accept {bulkScope}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void bulk('reject')} disabled={bulkBusy}>Reject {bulkScope}</Button>
          </>
        ) : undefined}
      >
        <div className="mb-sm flex flex-wrap items-center gap-sm">
          <Label htmlFor="proposal-scope" className="shrink-0">Findings</Label>
          <Select value={scope ?? 'mine'} onValueChange={(v) => setParam('scope', v)}>
            <SelectTrigger id="proposal-scope" className="h-8 w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="mine">Mine (authored or owned)</SelectItem>
              <SelectItem value="all">Everyone&apos;s</SelectItem>
            </SelectContent>
          </Select>
          <Label htmlFor="proposal-status" className="shrink-0">Status</Label>
          <Select value={status} onValueChange={(v) => setParam('status', v === 'pending' ? undefined : v)}>
            <SelectTrigger id="proposal-status" className="h-8 w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              {STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
          {/* The pending counts are known per kind, so there the kinds are
              chips with their counts (they were five measures above a Kind
              select saying the same thing); another status has no counts. */}
          {!kindChips && (
            <>
              <Label htmlFor="proposal-kind" className="shrink-0">Kind</Label>
              <Select value={kind ?? 'all'} onValueChange={(v) => setParam('kind', v === 'all' ? undefined : v)}>
                <SelectTrigger id="proposal-kind" className="h-8 w-52"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Every kind</SelectItem>
                  {KINDS.map((k) => <SelectItem key={k.kind} value={k.kind}>{k.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </>
          )}
          {sessionId != null && (
            <Button size="sm" variant="ghost" onClick={() => setParam('agent_session_id', undefined)}>
              Session #{sessionId} <X className="size-4" aria-hidden />
            </Button>
          )}
        </div>

        {kindChips && (
          <FilterChips<ProposalKind>
            label="Filter by kind"
            allLabel="All"
            allCount={KINDS.reduce((sum, k) => sum + (byKind[k.kind] ?? 0), 0)}
            chips={KINDS.map((k) => ({ key: k.kind, label: k.label, count: byKind[k.kind] ?? 0, title: k.info }))}
            selected={kind ?? null}
            onSelect={(k) => setParam('kind', k ?? undefined)}
          />
        )}

        {error && items !== null && (
          // A failed re-read keeps the rows it could not refresh.
          <p role="alert" className="mb-xs break-words text-caption text-destructive">{error} The rows below are from the last successful load.</p>
        )}
        {error && items === null ? (
          <p role="alert" className="break-words text-caption text-destructive">{error}</p>
        ) : items === null ? (
          <p className="text-caption text-muted-foreground">Loading…</p>
        ) : items.length === 0 ? (
          <p className="text-caption text-muted-foreground">None.</p>
        ) : (
          <>
            <p className="mb-xs text-caption text-muted-foreground">
              <kbd className="font-mono">j</kbd> / <kbd className="font-mono">k</kbd> move, <kbd className="font-mono">Enter</kbd> opens the finding
              {canDecide && status === 'pending' && <>, <kbd className="font-mono">a</kbd> accepts, <kbd className="font-mono">r</kbd> rejects</>}.
            </p>
            {items.map((pr, i) => (
              <ProposalItem key={pr.id} proposal={pr} canDecide={canDecide} showTarget
                rowProps={cursorRowProps(i)} />
            ))}
            {items.length < total && (
              <Button variant="ghost" size="sm" className="mt-xs" onClick={() => void more()} disabled={loadingMore}>
                {loadingMore && <Loader2 className="size-4 animate-spin" aria-hidden />}
                Show more ({total - items.length} left)
              </Button>
            )}
          </>
        )}
      </PostureSection>
    </div>
  );
};

export default Proposals;
