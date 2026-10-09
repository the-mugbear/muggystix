/**
 * Deciding one proposal (5.334.0, lifted out of ProposalItem): accept, accept
 * with the reviewer's edit, reject with an optional note — and the busy,
 * editing, rejecting and error state around them.  Every place a proposal is
 * decided (the Proposals page, a report-text field's drafts, an endpoint row)
 * uses this, so there is one decision path.
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { acceptProposal, Proposal, rejectProposal } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { invalidateReads } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { useProjectId } from './useProjectId';

/** What a decision puts out of date, by API function: the lists of proposals
 *  (the Proposals page, a finding's own), the pending counts (the top bar),
 *  and — an accepted one was applied — the finding, its history and its
 *  images.  One list, for a single decision and for "all shown". */
export const PROPOSAL_DECISION_READS = [
  'listProposals', 'getProposalSummary', 'getFinding', 'getFindingHistory', 'getFindingImages',
] as const;

export interface ProposalDecision {
  busy: 'accept' | 'reject' | null;
  error: string | null;
  /** The edit box's text while "Accept and edit" is open; null otherwise. */
  editing: string | null;
  setEditing: (v: string | null) => void;
  /** The reject reason while the reject box is open; null otherwise. */
  rejecting: string | null;
  setRejecting: (v: string | null) => void;
  accept: (editedValue?: string) => Promise<void>;
  reject: (note?: string) => Promise<void>;
}

type Decision =
  | { action: 'accept'; editedValue?: string }
  | { action: 'reject'; note?: string };

/**
 * What a caller does on its own screen once a proposal is decided (a
 * message, a cursor) — never reading again: the decision says itself which
 * reads are out of date.  `reread` settles when those on screen have been
 * read again (it does not reject; a read that failed says so in its query).
 */
export type OnProposalDecided = (updated: Proposal, reread: Promise<void>) => void;

export const useProposalDecision = (pr: Proposal, onDecided?: OnProposalDecided): ProposalDecision => {
  const toast = useToast();
  const projectId = useProjectId();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);

  const deciding = useMutation({
    mutationFn: (decision: Decision) => (decision.action === 'accept'
      ? acceptProposal(projectId, pr.id, decision.editedValue !== undefined ? { editedValue: decision.editedValue } : {})
      : rejectProposal(projectId, pr.id,decision.note?.trim() || undefined)),
    onSuccess: (updated, { action }) => {
      setEditing(null);
      setRejecting(null);
      // (Its own statement: `onDecided?.(…)` would not evaluate its arguments
      // for a caller that passes no callback, and nothing would be read again.)
      const reread = invalidateReads(queryClient, ...PROPOSAL_DECISION_READS);
      onDecided?.(updated, reread);
      toast.success(action === 'accept' ? 'Accepted — applied as you.' : 'Rejected.');
    },
  });
  // The failure is said beside the proposal (`error`), so it is not thrown at the caller.
  const decide = async (decision: Decision) => { await deciding.mutateAsync(decision).catch(() => undefined); };

  return {
    busy: deciding.isPending ? deciding.variables.action : null,
    error: deciding.error
      ? formatApiError(deciding.error, deciding.variables?.action === 'reject' ? 'Could not reject it.' : 'Could not accept it.')
      : null,
    editing, setEditing, rejecting, setRejecting,
    accept: (editedValue) => decide({ action: 'accept', editedValue }),
    reject: (note) => decide({ action: 'reject', note }),
  };
};
