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
      {source.source === 'llm_draft' ? (
        // Drafted in BlueStick with a person's LLM provider ("Draft with AI").
        <>an AI draft{source.proposed_by ? ` by ${source.proposed_by}` : ''}</>
      ) : source.agent_session_id != null ? (
        <Link to={`/agent-sessions/${source.agent_session_id}`} className="text-info hover:underline">
          agent session #{source.agent_session_id}
        </Link>
      ) : 'an agent'}
      {source.source === 'llm_draft' ? ', ' : '’s '}proposal #{source.proposal_id}
      {source.agent_model && <> ({source.agent_model})</>}
      {source.accepted_by && <> · accepted by {source.accepted_by}{source.edited_on_accept && !source.changed_since ? ', with an edit' : ''}</>}
      {when && <> · <time dateTime={source.accepted_at ?? undefined} title={formatTimestamp(source.accepted_at)}>{when}</time></>}
    </p>
  );
};

/** Which accepted-proposal sources are said ONCE, above the details, and
 *  which under their own detail (5.432.0).  With every written default
 *  accepted from one agent session the same sentence sat under all six
 *  (browser pass 2026-11-17).  Sources that share a session and were not
 *  edited since are one line naming the details; one alone, or one edited
 *  since, keeps its own line. */
export const groupSources = <K extends string>(
  sources: Array<[K, AcceptedDetailProposal]>,
): { shared: Array<{ sessionId: number; keys: K[]; first: AcceptedDetailProposal }>; own: K[] } => {
  const bySession = new Map<number, K[]>();
  for (const [key, source] of sources) {
    if (source.changed_since || source.agent_session_id == null) continue;
    bySession.set(source.agent_session_id, [...(bySession.get(source.agent_session_id) ?? []), key]);
  }
  const shared = [...bySession.entries()]
    .filter(([, keys]) => keys.length > 1)
    .map(([sessionId, keys]) => ({
      sessionId, keys, first: (sources.find(([k]) => k === keys[0]) as [K, AcceptedDetailProposal])[1],
    }));
  const said = new Set(shared.flatMap((g) => g.keys));
  return { shared, own: sources.map(([key]) => key).filter((key) => !said.has(key)) };
};

/** "Executive summary, System description and Disclaimer were accepted from
 *  agent session #168 (model)" — one line for the details one session wrote. */
export const SharedSourceLine: React.FC<{
  sessionId: number; labels: string[]; first: AcceptedDetailProposal;
}> = ({ sessionId, labels, first }) => {
  const names = labels.length > 1
    ? `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`
    : labels[0];
  return (
    <p className="mt-xs max-w-3xl break-words text-metadata text-muted-foreground" data-testid={`defaults-sources-${sessionId}`}>
      {names} hold text accepted from{' '}
      <Link to={`/agent-sessions/${sessionId}`} className="text-info hover:underline">agent session #{sessionId}</Link>
      ’s proposals{first.agent_model && <> ({first.agent_model})</>}, not edited since.
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
  /** Room above the drafts when a link lands on them (the page's own sticky bar). */
  scrollExtra?: string;
}

const DetailProposalNotes: React.FC<Props> = ({
  field, label, current, source, drafts, canDecide, evidence, scrollExtra,
}) => {
  if (!source && drafts.length === 0) return null;
  return (
    <div className="min-w-0 space-y-xs">
      {source && <AcceptedProposalLine source={source} testId={`defaults-${field}-source`} />}
      {drafts.length > 0 && (
        <FieldDraftsReview field={field} label={label} current={current} drafts={drafts}
          canDecide={canDecide} evidence={evidence} scrollExtra={scrollExtra} />
      )}
    </div>
  );
};

export default DetailProposalNotes;
