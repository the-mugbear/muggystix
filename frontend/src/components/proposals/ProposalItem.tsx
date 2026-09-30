/**
 * One proposal (v5.316.0): what it would change, who proposed it (agent
 * session + model, or an in-app draft), why, and — while pending — Accept,
 * Accept and edit (report text), Reject.  Accepting runs the change as the
 * person clicking; the server decides whether they may (report text: the
 * finding's author or a project admin), and a refusal is shown here.
 *
 * Shared by the finding page's Proposals section and the Proposals page.
 */
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { Check, Loader2, Pencil, X } from 'lucide-react';

import { acceptProposal, Proposal, rejectProposal } from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { formatApiError } from '../../utils/apiErrors';
import { formatRelativeTime } from '../../utils/relativeTime';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import MarkdownField from '../MarkdownField';
import SafeMarkdown from '../SafeMarkdown';

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
export const describeProposal = (pr: Proposal): string => {
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

const Source: React.FC<{ pr: Proposal }> = ({ pr }) => {
  const model = pr.agent_model ? ` · ${pr.agent_model}` : '';
  const when = formatRelativeTime(pr.created_at, { fallback: '' });
  if (pr.source === 'llm_draft') {
    return <span>Drafted in BlueStick by {pr.proposed_by ?? 'someone'}{model}{when && ` · ${when}`}</span>;
  }
  return (
    <span>
      {pr.agent_session_id != null ? (
        <Link to={`/agent-sessions/${pr.agent_session_id}`} className="text-info hover:underline">
          Agent session #{pr.agent_session_id}
        </Link>
      ) : 'An agent session'}
      {pr.proposed_by && <> ({pr.proposed_by}&apos;s)</>}
      {model}{pr.agent_client ? ` · ${pr.agent_client}` : ''}{when && ` · ${when}`}
    </span>
  );
};

const Body: React.FC<{ pr: Proposal }> = ({ pr }) => {
  const payload = pr.payload ?? {};
  if (pr.kind === 'finding_text') {
    const value = String(payload.accepted_value ?? payload.value ?? '');
    return (
      <div className="space-y-xs">
        {pr.field === 'cvss_vector'
          ? <p className="break-all font-mono text-body">{value}</p>
          : <SafeMarkdown text={value} className="text-body" />}
        {pr.status === 'pending' && (
          <details className="text-caption text-muted-foreground">
            <summary className="cursor-pointer select-none">Current text</summary>
            <div className="mt-xxs border-l-2 border-border pl-sm">
              {(pr.current_value ?? '').trim()
                ? <SafeMarkdown text={pr.current_value ?? ''} className="text-body text-muted-foreground" />
                : <p>Empty.</p>}
            </div>
          </details>
        )}
      </div>
    );
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
  onDecided: (updated: Proposal) => void;
  /** Show the target line (the Proposals page; the finding page knows it). */
  showTarget?: boolean;
}

const ProposalItem: React.FC<Props> = ({ proposal: pr, canDecide, onDecided, showTarget = false }) => {
  const toast = useToast();
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = pr.status === 'pending';

  const decide = async (action: 'accept' | 'reject', editedValue?: string) => {
    setBusy(action);
    setError(null);
    try {
      const updated = action === 'accept'
        ? await acceptProposal(pr.id, editedValue !== undefined ? { editedValue } : {})
        : await rejectProposal(pr.id);
      setEditing(null);
      onDecided(updated);
      toast.success(action === 'accept' ? 'Accepted — applied as you.' : 'Rejected.');
    } catch (err) {
      setError(formatApiError(err, action === 'accept' ? 'Could not accept it.' : 'Could not reject it.'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className="min-w-0 space-y-xs border-b border-border py-sm last:border-b-0" data-proposal={pr.id}>
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-sm gap-y-xxs">
        {showTarget && (
          pr.finding_id != null
            ? <Link to={`/findings/${pr.finding_id}#proposals`} className="min-w-0 truncate font-medium text-info hover:underline"
                title={describeProposal(pr)}>{describeProposal(pr)}</Link>
            : <span className="min-w-0 truncate font-medium" title={describeProposal(pr)}>{describeProposal(pr)}</span>
        )}
        {!pending && (
          <Badge variant={pr.status === 'accepted' ? 'success' : 'muted'} className="shrink-0">{pr.status}</Badge>
        )}
        <span className="min-w-0 text-caption text-muted-foreground"><Source pr={pr} /></span>
      </div>
      {pr.rationale && <p className="break-words text-caption text-muted-foreground">Why: {pr.rationale}</p>}
      {editing !== null ? (
        <div className="space-y-xs">
          <Label htmlFor={`proposal-${pr.id}`}>{FIELD_LABELS[pr.field ?? ''] ?? 'Text'}</Label>
          <MarkdownField id={`proposal-${pr.id}`} label={FIELD_LABELS[pr.field ?? ''] ?? 'Text'} rows={5}
            maxLength={32768} value={editing} onChange={setEditing} disabled={busy !== null} />
          <div className="flex flex-wrap gap-xs">
            <Button size="sm" onClick={() => void decide('accept', editing)} disabled={busy !== null || !editing.trim()}>
              {busy === 'accept' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Check className="size-4" aria-hidden />}
              Accept with my edit
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)} disabled={busy !== null}>Cancel</Button>
          </div>
        </div>
      ) : <Body pr={pr} />}
      {pr.evidence_ids.length > 0 && (
        <p className="text-caption text-muted-foreground">
          Cites evidence {pr.evidence_ids.map((id) => `#${id}`).join(', ')}
          {pr.target.host_id != null && (
            <> — <Link to={`/hosts/${pr.target.host_id}#evidence`} className="text-info hover:underline">on the host</Link></>
          )}
        </p>
      )}
      {pending && pr.error && <p className="break-words text-caption text-warning">Last attempt failed: {pr.error}</p>}
      {!pending && (pr.decided_by || pr.decision_note) && (
        <p className="break-words text-caption text-muted-foreground">
          {pr.decided_by ? `${pr.status === 'superseded' ? 'Superseded' : 'Decided'} by ${pr.decided_by}` : 'Superseded'}
          {pr.decision_note ? ` — ${pr.decision_note}` : ''}
        </p>
      )}
      {error && <p className="break-words text-caption text-destructive">{error}</p>}
      {pending && canDecide && editing === null && (
        <div className="flex flex-wrap gap-xs">
          <Button size="sm" variant="outline" onClick={() => void decide('accept')} disabled={busy !== null}>
            {busy === 'accept' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Check className="size-4" aria-hidden />}
            Accept
          </Button>
          {pr.kind === 'finding_text' && (
            <Button size="sm" variant="ghost" onClick={() => setEditing(String(pr.payload?.value ?? ''))} disabled={busy !== null}>
              <Pencil className="size-4" aria-hidden /> Accept and edit
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => void decide('reject')} disabled={busy !== null}>
            {busy === 'reject' ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <X className="size-4" aria-hidden />}
            Reject
          </Button>
        </div>
      )}
    </article>
  );
};

export default ProposalItem;
