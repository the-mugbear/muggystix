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
import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Loader2, X } from 'lucide-react';

import {
  decideProposals, getProposalSummary, listProposals, Proposal, ProposalKind, ProposalStatus,
  ProposalSummary,
} from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { useVisibilityPoll } from '../hooks/useVisibilityPoll';
import { formatApiError } from '../utils/apiErrors';
import PostureMeasure from '../components/posture/PostureMeasure';
import PostureSection from '../components/posture/PostureSection';
import ProposalItem from '../components/proposals/ProposalItem';
import { Button } from '../components/ui/button';
import { Label } from '../components/ui/label';
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

const Proposals: React.FC = () => {
  const toast = useToast();
  const { hasPermission } = useAuth();
  const canDecide = hasPermission('analyst');
  const [confirmDialog, confirm] = useConfirm();
  const [params, setParams] = useSearchParams();
  const status = (params.get('status') as ProposalStatus | null) ?? 'pending';
  const kind = (params.get('kind') as ProposalKind | null) ?? undefined;
  const sessionParam = params.get('agent_session_id');
  const sessionId = sessionParam ? Number(sessionParam) : undefined;

  const [items, setItems] = useState<Proposal[] | null>(null);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<ProposalSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);

  const query = useCallback((offset: number) => listProposals({
    status, kind, agent_session_id: sessionId, limit: PAGE, offset,
  }), [status, kind, sessionId]);

  const load = useCallback(async () => {
    try {
      const [res, sum] = await Promise.all([query(0), getProposalSummary()]);
      setItems(res.items);
      setTotal(res.total);
      setSummary(sum);
      setError(null);
    } catch (err) {
      setError(formatApiError(err, 'Could not load the proposals.'));
    }
  }, [query]);

  useEffect(() => { setItems(null); void load(); }, [load]);
  useVisibilityPoll(load, 60_000);

  const more = async () => {
    if (!items) return;
    setLoadingMore(true);
    try {
      const res = await query(items.length);
      setItems([...items, ...res.items]);
      setTotal(res.total);
    } catch (err) {
      toast.error(formatApiError(err, 'Could not load more.'));
    } finally {
      setLoadingMore(false);
    }
  };

  const setParam = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };

  const bulk = async (action: 'accept' | 'reject') => {
    const shown = (items ?? []).filter((p) => p.status === 'pending');
    if (!shown.length) return;
    const ok = await confirm({
      title: action === 'accept' ? `Accept ${shown.length} proposals?` : `Reject ${shown.length} proposals?`,
      body: action === 'accept'
        ? 'Each is applied as you, one by one. Any you may not apply (report text on someone else’s finding) or whose target has changed is left pending and listed.'
        : 'Each is marked rejected. The agent can read the decision.',
      confirmLabel: action === 'accept' ? 'Accept all shown' : 'Reject all shown',
    });
    if (!ok) return;
    setBulkBusy(true);
    try {
      const res = await decideProposals(shown.map((p) => p.id), action);
      if (res.failed.length) {
        toast.warning(`${res.decided.length} decided; ${res.failed.length} left pending (${String(res.failed[0].detail)}).`);
      } else {
        toast.success(`${res.decided.length} ${action === 'accept' ? 'accepted' : 'rejected'}.`);
      }
      await load();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not decide them.'));
    } finally {
      setBulkBusy(false);
    }
  };

  const pending = summary?.pending ?? 0;
  const pendingShown = (items ?? []).filter((p) => p.status === 'pending').length;

  return (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      {confirmDialog}
      <div className="min-w-0">
        <h1 className="text-page-title">Proposals</h1>
        <p className="mt-xxs max-w-4xl text-metadata text-muted-foreground">
          {pending === 0
            ? 'Nothing waiting. When an agent (or an AI draft) proposes a change to what the team has concluded, it lands here for a person to accept or reject.'
            : <>
                <strong className="text-foreground">{pending}</strong> proposed change{pending === 1 ? '' : 's'} to what
                the team has concluded {pending === 1 ? 'is' : 'are'} waiting. Nothing has changed yet: accepting one applies
                it as you.
              </>}
        </p>
      </div>

      {summary && (
        <div className="grid grid-cols-2 gap-y-md lg:grid-cols-5 lg:divide-x lg:divide-border">
          {KINDS.map((k) => (
            <PostureMeasure key={k.kind} label={k.label} value={summary.by_kind[k.kind] ?? 0} info={k.info}
              to={`/proposals?kind=${k.kind}`} toLabel="Show them">
              pending
            </PostureMeasure>
          ))}
        </div>
      )}

      <PostureSection
        title={<span>{status === 'pending' ? 'Waiting for a decision' : `${status[0].toUpperCase()}${status.slice(1)}`}</span>}
        description={sessionId != null ? `From agent session #${sessionId}.` : undefined}
        actions={canDecide && status === 'pending' && pendingShown > 0 ? (
          <>
            <Button size="sm" variant="outline" onClick={() => void bulk('accept')} disabled={bulkBusy}>
              {bulkBusy && <Loader2 className="size-4 animate-spin" aria-hidden />} Accept all shown
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void bulk('reject')} disabled={bulkBusy}>Reject all shown</Button>
          </>
        ) : undefined}
      >
        <div className="mb-sm flex flex-wrap items-center gap-sm">
          <Label htmlFor="proposal-status" className="shrink-0">Status</Label>
          <Select value={status} onValueChange={(v) => setParam('status', v === 'pending' ? undefined : v)}>
            <SelectTrigger id="proposal-status" className="h-8 w-40"><SelectValue /></SelectTrigger>
            <SelectContent>
              {STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
          <Label htmlFor="proposal-kind" className="shrink-0">Kind</Label>
          <Select value={kind ?? 'all'} onValueChange={(v) => setParam('kind', v === 'all' ? undefined : v)}>
            <SelectTrigger id="proposal-kind" className="h-8 w-52"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Every kind</SelectItem>
              {KINDS.map((k) => <SelectItem key={k.kind} value={k.kind}>{k.label}</SelectItem>)}
            </SelectContent>
          </Select>
          {sessionId != null && (
            <Button size="sm" variant="ghost" onClick={() => setParam('agent_session_id', undefined)}>
              Session #{sessionId} <X className="size-4" aria-hidden />
            </Button>
          )}
        </div>

        {error ? (
          <p className="text-caption text-destructive">{error}</p>
        ) : items === null ? (
          <p className="text-caption text-muted-foreground">Loading…</p>
        ) : items.length === 0 ? (
          <p className="text-caption text-muted-foreground">None.</p>
        ) : (
          <>
            {items.map((pr) => (
              <ProposalItem key={pr.id} proposal={pr} canDecide={canDecide} showTarget
                onDecided={() => void load()} />
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
