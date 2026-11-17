/**
 * Under a written detail of the Report defaults (5.431.0): where its text
 * came from, and the drafts waiting for it.
 *
 * - The defaults keep no edit history, so nothing said an agent wrote a text
 *   once its proposal was accepted (browser pass 2026-11-17).  The line is
 *   the server's (`ReportProfile.from_proposals`): the accepted proposal, who
 *   accepted it, and whether a person has edited the text since.
 * - The detail's pending drafts are reviewed HERE, beside the text they
 *   would replace, several of them side by side (`FieldDraftsReview` — the
 *   finding page's review of a section's drafts).
 */
import React from 'react';
import { Link } from 'react-router-dom';

import type { AcceptedDetailProposal, Proposal } from '../../services/api';
import type { EvidenceResolver } from '../../utils/reportImages';
import { formatRelativeTime, formatTimestamp } from '../../utils/relativeTime';
import FieldDraftsReview from '../proposals/FieldDraftsReview';

export const AcceptedProposalLine: React.FC<{ source: AcceptedDetailProposal; testId?: string }> = ({ source, testId }) => {
  const when = formatRelativeTime(source.accepted_at, { fallback: '' });
  return (
    <p className="break-words text-caption text-muted-foreground" data-testid={testId}>
      {source.changed_since ? 'Edited since it was accepted from ' : 'Text accepted from '}
      {source.agent_session_id != null ? (
        <Link to={`/agent-sessions/${source.agent_session_id}`} className="text-info hover:underline">
          agent session #{source.agent_session_id}
        </Link>
      ) : 'an agent'}
      ’s proposal #{source.proposal_id}
      {source.agent_model && <> ({source.agent_model})</>}
      {source.accepted_by && <> · accepted by {source.accepted_by}{source.edited_on_accept && !source.changed_since ? ', with an edit' : ''}</>}
      {when && <> · <time dateTime={source.accepted_at ?? undefined} title={formatTimestamp(source.accepted_at)}>{when}</time></>}
    </p>
  );
};

interface Props {
  field: string;
  label: string;
  /** The default's saved text (what an accept replaces). */
  current: string | null | undefined;
  source?: AcceptedDetailProposal;
  drafts: Proposal[];
  canDecide: boolean;
  evidence?: EvidenceResolver;
}

const DetailProposalNotes: React.FC<Props> = ({ field, label, current, source, drafts, canDecide, evidence }) => {
  if (!source && drafts.length === 0) return null;
  return (
    <div className="min-w-0 space-y-xs">
      {source && <AcceptedProposalLine source={source} testId={`defaults-${field}-source`} />}
      {drafts.length > 0 && (
        <FieldDraftsReview field={field} label={label} current={current} drafts={drafts}
          canDecide={canDecide} evidence={evidence} />
      )}
    </div>
  );
};

export default DetailProposalNotes;
