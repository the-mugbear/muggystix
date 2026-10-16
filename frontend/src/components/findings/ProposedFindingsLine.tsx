/**
 * "N proposed findings are waiting for review" on the Findings page (5.403.0).
 *
 * A finding an agent proposes is not a finding until a person accepts it, so
 * it is on no Findings list — and the operator who asked their agent for it
 * looked here, found nothing, and read the call as failed.  The number is the
 * project's pending `finding_create` proposals from the Proposals summary (the
 * query the top bar's count and the Proposals page read: one request, one
 * figure), and the link opens exactly those.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { getProposalSummary } from '../../services/api';
import { useProjectId } from '../../hooks/useProjectId';
import { pollEvery } from '../../lib/query';

export const PROPOSED_FINDINGS_PATH = '/proposals?kind=finding_create&scope=all';

const ProposedFindingsLine: React.FC = () => {
  const projectId = useProjectId();
  const summary = useQuery({
    queryKey: ['getProposalSummary', projectId],
    queryFn: ({ signal }) => getProposalSummary(projectId, signal),
    ...pollEvery(60_000),
  });
  // Nothing waiting, or not known: the page says nothing (the Proposals page
  // reports its own failure).
  const waiting = summary.data?.by_kind.finding_create ?? 0;
  if (waiting <= 0) return null;
  return (
    <p className="mt-xs text-metadata text-muted-foreground" data-testid="proposed-findings-line">
      <Link to={PROPOSED_FINDINGS_PATH} className="font-medium text-primary underline-offset-4 hover:underline">
        {waiting.toLocaleString()} proposed finding{waiting === 1 ? ' is' : 's are'} waiting for review
      </Link>
      {' '}— not on this list until someone accepts {waiting === 1 ? 'it' : 'them'}.
    </p>
  );
};

export default ProposedFindingsLine;
