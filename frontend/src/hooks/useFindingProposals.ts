/**
 * A finding's pending proposals, read once for the whole page (5.334.0).
 *
 * The page shows each proposal where it applies — report-text drafts in
 * Report text, endpoint changes on their row, the rest in the Proposals
 * summary — so the list is loaded here and handed to each, rather than each
 * section fetching its own.  Re-read every 30 s while visible, and whenever a
 * write says `listProposals` is out of date (a decision, an AI draft).
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { listProposals, Proposal } from '../services/api';
import { pollEvery, queryErrorText } from '../lib/query';

export interface FindingProposals {
  items: Proposal[] | null;
  error: string | null;
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

export const useFindingProposals = (findingId: number | null): FindingProposals => {
  const query = useQuery({
    queryKey: ['listProposals', { finding_id: findingId, status: 'pending', limit: 200 }],
    queryFn: () => listProposals({ finding_id: findingId as number, status: 'pending', limit: 200 }),
    enabled: findingId != null,
    ...pollEvery(30_000),
  });
  const items = query.data?.items ?? null;

  const groups = useMemo(() => groupFindingProposals(items ?? []), [items]);
  return { items, error: queryErrorText(query.error, 'Proposals unavailable.'), ...groups };
};
