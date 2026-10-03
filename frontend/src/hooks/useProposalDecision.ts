/**
 * Deciding one proposal (5.334.0, lifted out of ProposalItem): accept, accept
 * with the reviewer's edit, reject with an optional note — and the busy,
 * editing, rejecting and error state around them.  Every place a proposal is
 * decided (the Proposals page, a report-text field's drafts, an endpoint row)
 * uses this, so there is one decision path.
 */
import { useState } from 'react';

import { acceptProposal, Proposal, rejectProposal } from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { announceProposalsChanged } from '../utils/proposalEvents';

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

export const useProposalDecision = (
  pr: Proposal, onDecided: (updated: Proposal) => void,
): ProposalDecision => {
  const toast = useToast();
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decide = async (action: 'accept' | 'reject', editedValue?: string, note?: string) => {
    setBusy(action);
    setError(null);
    try {
      const updated = action === 'accept'
        ? await acceptProposal(pr.id, editedValue !== undefined ? { editedValue } : {})
        : await rejectProposal(pr.id, note?.trim() || undefined);
      setEditing(null);
      setRejecting(null);
      onDecided(updated);
      announceProposalsChanged();
      toast.success(action === 'accept' ? 'Accepted — applied as you.' : 'Rejected.');
    } catch (err) {
      setError(formatApiError(err, action === 'accept' ? 'Could not accept it.' : 'Could not reject it.'));
    } finally {
      setBusy(null);
    }
  };

  return {
    busy, error, editing, setEditing, rejecting, setRejecting,
    accept: (editedValue) => decide('accept', editedValue),
    reject: (note) => decide('reject', undefined, note),
  };
};
