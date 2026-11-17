/**
 * How many of the project's pending proposals are of one kind — for a page
 * that is not the Proposals page but should say that something for it is
 * waiting there (a proposed finding is on no Findings list; proposed text for
 * the report defaults is not in them until accepted).
 *
 * The number is the Proposals summary's (the query the top bar's count and
 * the Proposals page read: one request, one figure).  0 while it is not
 * known or the read failed: the page then says nothing, and the Proposals
 * page reports its own failure.
 */
import { useQuery } from '@tanstack/react-query';

import { getProposalSummary, type ProposalKind } from '../services/api';
import { pollEvery } from '../lib/query';
import { useProjectId } from './useProjectId';

export const usePendingProposalCount = (kind: ProposalKind): number => {
  const projectId = useProjectId();
  const summary = useQuery({
    queryKey: ['getProposalSummary', projectId],
    queryFn: ({ signal }) => getProposalSummary(projectId, signal),
    ...pollEvery(60_000),
  });
  return summary.data?.by_kind[kind] ?? 0;
};
