/**
 * One proposal (v5.316.0): what it would change, who proposed it (agent
 * session + model, or an in-app draft), why, and — while pending — Accept,
 * Accept and edit (report text), Reject.  Accepting runs the change as the
 * person clicking; the server decides whether they may (report text: the
 * finding's author or a project admin), and a refusal is shown here.
 *
 * Shared by the finding page and the Proposals page.
 *
 * 5.334.0 — report text is shown against the field's current text
 * (`TextComparison`, always visible), and the pieces — source line, rationale,
 * decision controls — are exported for the finding page, which reviews
 * report-text drafts inside Report text and endpoint changes on their rows.
 * Deciding is `useProposalDecision`, the one decision path.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { Check, Loader2, Pencil, X } from 'lucide-react';

import type { Proposal } from '../../services/api';
import { type OnProposalDecided, ProposalDecision, useProposalDecision } from '../../hooks/useProposalDecision';
import { cn } from '../../utils/cn';
import { formatRelativeTime, formatTimestamp } from '../../utils/relativeTime';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import { Textarea } from '../ui/textarea';
import MarkdownField from '../MarkdownField';
import SafeMarkdown from '../SafeMarkdown';
import TextComparison from './TextComparison';

export const FIELD_LABELS: Record<string, string> = {
  description: 'Description',
  impact: 'Impact',
  recommendation: 'Recommendation',
  steps_to_reproduce: 'Steps to reproduce',
  references: 'References',
  cvss_vector: 'CVSS vector',
};

const ENDPOINT_LABELS: Record<string, string> = {
  open: 'open',
  remediated: 'remediated',
  retest: 'retest',
  false_positive: 'a false positive',
};

/** A short line naming the change — the Proposals page's row title. */
/** What the reject reason is for, in the field itself (the wording of the
 *  endpoints' "Note for the history (optional), e.g. …").  The single reject
 *  and the page's "Reject all shown" share it. */
export const REJECT_NOTE_PLACEHOLDER =
  'Note for the agent (optional), e.g. cite the evidence record, keep the scanner’s severity';

export const describeProposal =(pr: Proposal): string => {
  const payload = pr.payload ?? {};
  switch (pr.kind) {
    case 'finding_text':
      return `${FIELD_LABELS[pr.field ?? ''] ?? pr.field} for “${pr.target.finding_title ?? `finding #${pr.finding_id}`}”`;
    case 'finding_create':
      return `New finding: ${String(payload.title ?? '')}`;
    case 'observation_promote':
      return `Confirm “${pr.target.observation_title ?? 'observation'}” as a finding`;
    case 'observation_dismiss':
      return `Dismiss “${pr.target.observation_title ?? 'observation'}” as a false positive`;
    case 'endpoint_status':
      return `Mark ${pr.target.host_ip ?? 'an endpoint'} ${ENDPOINT_LABELS[String(payload.host_status)] ?? payload.host_status} on “${pr.target.finding_title ?? `finding #${pr.finding_id}`}”`;
    default:
      return pr.kind;
  }
};

export const ProposalSource: React.FC<{ pr: Proposal }> = ({ pr }) => {
  const model = pr.agent_model ? ` · ${pr.agent_model}` : '';
  const when = formatRelativeTime(pr.created_at, { fallback: '' });
  if (pr.source === 'llm_draft') {
    return <span>Drafted in BlueStick by {pr.proposed_by ?? 'someone'}{model}{when && ` · ${when}`}</span>;
  }
  // The client (and its version) is on hover: session, operator, model and
  // age are what a reviewer compares drafts by.
  return (
    <span title={pr.agent_client ? `Client: ${pr.agent_client}` : undefined}>
      {pr.agent_session_id != null ? (
        <Link to={`/agent-sessions/${pr.agent_session_id}`} className="text-info hover:underline">
          Agent session #{pr.agent_session_id}
        </Link>
      ) : 'An agent session'}
      {pr.proposed_by && <> ({pr.proposed_by}&apos;s)</>}
      {model}
      {when && ` · ${when}`}
    </span>
  );
};

/** The base text to warn with, when the field changed after the draft was written. */
export const staleBase = (pr: Proposal): { value: string | null } | null =>
  (pr.changed_since_proposed ? { value: pr.base_value ?? null } : null);

const Body: React.FC<{ pr: Proposal }> = ({ pr }) => {
  const payload = pr.payload ?? {};
  if (pr.kind === 'finding_text') {
    const value = String(payload.accepted_value ?? payload.value ?? '');
    if (pr.status === 'pending') {
      return (
        <TextComparison
          current={pr.current_value}
          proposed={value}
          mono={pr.field === 'cvss_vector'}
          staleBase={staleBase(pr)}
        />
      );
    }
    return pr.field === 'cvss_vector'
      ? <p className="break-all font-mono text-body">{value}</p>
      : <SafeMarkdown text={value} className="text-body" />;
  }
  if (pr.kind === 'finding_create') {
    const hosts = Array.isArray(payload.host_ids) ? payload.host_ids.length : 0;
    const text = (payload.report_text ?? {}) as Record<string, string>;
    return (
      <div className="space-y-xxs text-body">
        <p className="break-words">
          <span className="font-medium">{String(payload.title ?? '')}</span>
          {' · '}{String(payload.severity ?? '')} · {hosts} host{hosts === 1 ? '' : 's'}
          {payload.status === 'confirmed' ? ' · confirmed' : ' · under investigation'}
        </p>
        {Object.entries(text).map(([k, v]) => (
          <div key={k} className="min-w-0">
            <p className="text-caption font-medium text-muted-foreground">{FIELD_LABELS[k] ?? k}</p>
            <SafeMarkdown text={v} className="text-body" />
          </div>
        ))}
      </div>
    );
  }
  if (pr.kind === 'observation_promote' || pr.kind === 'observation_dismiss') {
    const scope = payload.scope ?? (pr.kind === 'observation_dismiss' ? 'host' : 'issue');
    return (
      <p className="break-words text-body">
        {pr.target.host_id != null
          ? <Link to={`/hosts/${pr.target.host_id}`} className="text-info hover:underline">{pr.target.host_ip}</Link>
          : 'Host'}
        {' · '}{scope === 'host' ? 'this host only' : 'every host carrying the issue'}
        {payload.severity ? ` · severity ${String(payload.severity)}` : ''}
        {payload.summary ? <> · <span className="text-muted-foreground">{String(payload.summary)}</span></> : null}
      </p>
    );
  }
  return null;
};

interface Props {
  proposal: Proposal;
  /** Analyst+ — the server still decides each accept. */
  canDecide: boolean;
  /** What the page does on its own screen once it is decided — the reads it
   *  changed are asked again by the decision itself (`useProposalDecision`). */
  onDecided?: OnProposalDecided;
  /** Show the target line (the Proposals page; the finding page knows it). */
  showTarget?: boolean;
  /** Link the target line to its finding — off on the finding's own page. */
  linkTarget?: boolean;
  /** The list cursor's marker and highlight (`useListCursor().cursorRowProps`). */
  rowProps?: { className?: string; 'data-list-cursor'?: 'true' };
}

/** Where a decided proposal's change now lives (B14): the finding an accepted
 *  "new finding" made, or the endpoint row an endpoint-status one is about.
 *  Null when the proposal's own target line already links there. */
export const proposalOutcomeLink = (pr: Proposal): { to: string; label: string } | null => {
  if (pr.kind === 'finding_create' && pr.status === 'accepted' && pr.result_finding_id != null) {
    return { to: `/findings/${pr.result_finding_id}`, label: 'Open the finding it created' };
  }
  if (pr.kind === 'endpoint_status' && pr.finding_id != null && pr.finding_host_id != null) {
    return {
      to: `/findings/${pr.finding_id}?endpoint=${pr.finding_host_id}#endpoints`,
      label: pr.target.host_ip ? `Show ${pr.target.host_ip} on the finding` : 'Show the endpoint on the finding',
    };
  }
  return null;
};

/** "Why: …" and the evidence it cites — one muted block under the change. */
export const ProposalReasons: React.FC<{ pr: Proposal }> = ({ pr }) => (
  <>
    {pr.rationale && <p className="break-words text-caption text-muted-foreground">Why: {pr.rationale}</p>}
    {pr.evidence_ids.length > 0 && (
      <p className="text-caption text-muted-foreground">
        Cites evidence {pr.evidence_ids.map((id) => `#${id}`).join(', ')}
        {pr.target.host_id != null && (
          <> — <Link to={`/hosts/${pr.target.host_id}#evidence`} className="text-info hover:underline">on the host</Link></>
        )}
      </p>
    )}
  </>
);

interface ControlsProps {
  pr: Proposal;
  canDecide: boolean;
  decision: ProposalDecision;
  /** Shown as the edit box's label. */
  fieldLabel?: string;
  /** The field's text now — "Accept and edit" may start from it. */
  current?: string | null;
  /** Smaller buttons on one line (an endpoint row). */
  compact?: boolean;
}

/** The pending proposal's controls: Accept, Accept and edit (report text),
 *  Reject… with its optional note, and the error of the last attempt. */
export const ProposalDecisionControls: React.FC<ControlsProps> = ({
  pr, canDecide, decision, fieldLabel, current = null, compact = false,
}) => {
  const { busy, error, editing, setEditing, rejecting, setRejecting, accept, reject } = decision;
  const pending = pr.status === 'pending';
  const label = fieldLabel ?? FIELD_LABELS[pr.field ?? ''] ?? 'Text';
  const draftText = String(pr.payload?.value ?? '');
  const hasCurrent = !!(current ?? '').trim();
  return (
    <>
      {pending && pr.error && <p className="break-words text-caption text-warning">Last attempt failed: {pr.error}</p>}
      {error && <p className="break-words text-caption text-destructive">{error}</p>}
      {pending && canDecide && editing !== null && (
        <div className="space-y-xs">
          <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-xs">
            <Label htmlFor={`proposal-${pr.id}`}>{label}</Label>
            {/* Start from either side: the draft is the default, but a reviewer
                who wants one sentence of it keeps the current text instead. */}
            {hasCurrent && (
              <span className="flex flex-wrap gap-xs text-caption">
                <Button type="button" variant="link" size="sm" className="h-auto p-0" disabled={busy !== null}
                  onClick={() => setEditing(draftText)}>Start from the draft</Button>
                <Button type="button" variant="link" size="sm" className="h-auto p-0" disabled={busy !== null}
                  onClick={() => setEditing(current ?? '')}>Start from the current text</Button>
              </span>
            )}
          </div>
          <MarkdownField id={`proposal-${pr.id}`} label={label} rows={5}
            maxLength={32768} value={editing} onChange={setEditing} disabled={busy !== null} />
          <div className="flex flex-wrap gap-xs">
            <Button size="sm" onClick={() => void accept(editing)} disabled={busy !== null || !editing.trim()}>
              {busy === 'accept' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Check className="size-4" aria-hidden />}
              Accept with my edit
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)} disabled={busy !== null}>Cancel</Button>
          </div>
        </div>
      )}
      {pending && canDecide && rejecting !== null && (
        <div className="space-y-xs">
          <Label htmlFor={`reject-${pr.id}`}>Why reject it? (optional)</Label>
          <p id={`reject-${pr.id}-hint`} className="text-caption text-muted-foreground">
            The agent that proposed it reads this — say what to change so its next proposal can follow it.
          </p>
          <Textarea id={`reject-${pr.id}`} rows={2} maxLength={2000} value={rejecting} autoFocus
            aria-describedby={`reject-${pr.id}-hint`} placeholder={REJECT_NOTE_PLACEHOLDER}
            onChange={(e) => setRejecting(e.target.value)} disabled={busy !== null} />
          <div className="flex flex-wrap gap-xs">
            <Button size="sm" variant="outline" onClick={() => void reject(rejecting)} disabled={busy !== null}>
              {busy === 'reject' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <X className="size-4" aria-hidden />}
              Reject
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejecting(null)} disabled={busy !== null}>Cancel</Button>
          </div>
        </div>
      )}
      {pending && canDecide && editing === null && rejecting === null && (
        <div className="flex flex-wrap gap-xs">
          <Button size="sm" variant="outline" data-proposal-action="accept" className={cn(compact && 'h-7')}
            onClick={() => void accept()} disabled={busy !== null}>
            {busy === 'accept' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Check className="size-4" aria-hidden />}
            Accept
          </Button>
          {pr.kind === 'finding_text' && (
            <Button size="sm" variant="ghost" onClick={() => setEditing(draftText)} disabled={busy !== null}>
              <Pencil className="size-4" aria-hidden /> Accept and edit
            </Button>
          )}
          <Button size="sm" variant="ghost" data-proposal-action="reject" className={cn(compact && 'h-7')}
            onClick={() => setRejecting('')} disabled={busy !== null}>
            <X className="size-4" aria-hidden /> Reject…
          </Button>
        </div>
      )}
    </>
  );
};

const ProposalItem: React.FC<Props> = ({
  proposal: pr, canDecide, onDecided, showTarget = false, linkTarget = true, rowProps,
}) => {
  const decision = useProposalDecision(pr, onDecided);
  const pending = pr.status === 'pending';
  const outcome = proposalOutcomeLink(pr);
  const decidedWhen = formatRelativeTime(pr.decided_at, { fallback: '' });

  return (
    <article
      data-list-cursor={rowProps?.['data-list-cursor']}
      className={cn('min-w-0 space-y-xs border-b border-border py-sm last:border-b-0', rowProps && 'px-xs', rowProps?.className)}
      data-proposal={pr.id}
    >
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-sm gap-y-xxs">
        {showTarget && (
          pr.finding_id != null && linkTarget
            ? <Link to={`/findings/${pr.finding_id}#proposals`} className="min-w-0 truncate font-medium text-info hover:underline"
                title={describeProposal(pr)}>{describeProposal(pr)}</Link>
            : <span className="min-w-0 truncate font-medium" title={describeProposal(pr)}>{describeProposal(pr)}</span>
        )}
        {!pending && (
          <Badge variant={pr.status === 'accepted' ? 'success' : 'muted'} className="shrink-0">{pr.status}</Badge>
        )}
        <span className="min-w-0 text-caption text-muted-foreground"><ProposalSource pr={pr} /></span>
      </div>
      {/* Kept while editing, so both texts stay in view (5.334.1). */}
      <Body pr={pr} />
      <ProposalReasons pr={pr} />
      {!pending && (pr.decided_by || pr.decision_note || decidedWhen) && (
        <p className="break-words text-caption text-muted-foreground">
          {pr.decided_by
            ? `${pr.status === 'superseded' ? 'Superseded' : 'Decided'} by ${pr.decided_by}`
            : pr.status === 'superseded' ? 'Superseded' : 'Decided'}
          {/* B14 — when, not only who (the absolute time on hover). */}
          {decidedWhen && <> · <time dateTime={pr.decided_at ?? undefined} title={formatTimestamp(pr.decided_at)}>{decidedWhen}</time></>}
          {pr.decision_note ? ` — ${pr.decision_note}` : ''}
        </p>
      )}
      {outcome && (
        <p className="min-w-0 text-caption">
          <Link to={outcome.to} className="break-words text-info hover:underline">{outcome.label}</Link>
        </p>
      )}
      <ProposalDecisionControls pr={pr} canDecide={canDecide} decision={decision} current={pr.current_value} />
    </article>
  );
};

export default ProposalItem;
