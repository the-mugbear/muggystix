/**
 * A finding's pending proposals, read once for the whole page (5.334.0).
 *
 * The page shows each proposal where it applies — report-text drafts in
 * Report text, endpoint changes on their row, the rest in the Proposals
 * summary — so the list is loaded here and handed to each, rather than each
 * section fetching its own.  Re-read on `reloadKey`, every 30 s while
 * visible, and when any proposal is decided elsewhere on the page.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import { listProposals, Proposal } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { PROPOSALS_CHANGED_EVENT } from '../utils/proposalEvents';
import { useLatestRequest } from './useLatestRequest';
import { useVisibilityPoll } from './useVisibilityPoll';

export interface FindingProposals {
  items: Proposal[] | null;
  error: string | null;
  reload: () => Promise<void>;
  /** finding_text drafts by field, oldest first (so letters stay put). */
  textByField: Map<string, Proposal[]>;
  /** endpoint_status proposals by finding_host_id. */
  byEndpoint: Map<number, Proposal[]>;
}

const byCreated = (a: Proposal, b: Proposal) =>
  (a.created_at ?? '').localeCompare(b.created_at ?? '') || a.id - b.id;

export const groupFindingProposals = (items: Proposal[]) => {
  const textByField = new Map<string, Proposal[]>();
  const byEndpoint = new Map<number, Proposal[]>();
  for (const pr of [...items].sort(byCreated)) {
    if (pr.kind === 'finding_text' && pr.field) {
      textByField.set(pr.field, [...(textByField.get(pr.field) ?? []), pr]);
    } else if (pr.kind === 'endpoint_status' && pr.finding_host_id != null) {
      byEndpoint.set(pr.finding_host_id, [...(byEndpoint.get(pr.finding_host_id) ?? []), pr]);
    }
  }
  return { textByField, byEndpoint };
};

export const useFindingProposals = (findingId: number | null, reloadKey = 0): FindingProposals => {
  const [items, setItems] = useState<Proposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useLatestRequest();

  const load = useCallback(async () => {
    if (findingId == null) return;
    const r = await run(() => listProposals({ finding_id: findingId, status: 'pending', limit: 200 }));
    if (r.stale) return;
    if (r.ok) { setItems(r.value.items); setError(null); }
    else setError(formatApiError(r.error, 'Proposals unavailable.'));
  }, [findingId, run]);

  useEffect(() => { setItems(null); setError(null); }, [findingId]);
  useEffect(() => { void load(); }, [load, reloadKey]);
  useVisibilityPoll(load, 30_000);
  useEffect(() => {
    const on = () => { void load(); };
    window.addEventListener(PROPOSALS_CHANGED_EVENT, on);
    return () => window.removeEventListener(PROPOSALS_CHANGED_EVENT, on);
  }, [load]);

  const groups = useMemo(() => groupFindingProposals(items ?? []), [items]);
  return { items, error, reload: load, ...groups };
};
