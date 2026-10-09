/**
 * The top bar's pending-proposals count (v5.316.0): how many changes an agent
 * or an AI draft proposed that nobody has decided yet.  Renders nothing when
 * there are none, so a project that never uses agents shows no new chrome.
 *
 * The count is the `getProposalSummary` query: re-read every minute while the
 * tab is visible, and at once when a decision anywhere (a finding page, the
 * Proposals page, a draft) invalidates it.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ListChecks } from 'lucide-react';

import { getProposalSummary } from '../../services/api';
import { useProject } from '../../contexts/ProjectContext';
import { pollEvery } from '../../lib/query';
import { Button } from '../ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

const PendingProposalsButton: React.FC = () => {
  const navigate = useNavigate();
  const { currentProject } = useProject();
  // A failed read keeps the last count; the Proposals page reports its own failures.
  const { data: summary } = useQuery({
    queryKey: ['getProposalSummary'],
    queryFn: () => getProposalSummary(),
    enabled: !!currentProject,
    ...pollEvery(60_000),
  });
  // 5.318.0 — your own (proposals about findings you authored or own); a
  // project admin, who may accept any report text, sees the project's.  An
  // admin's review of every finding used to put the whole run in every
  // member's top bar.
  const all = !!summary?.viewer_is_project_admin;
  const scope: 'mine' | 'all' = all ? 'all' : 'mine';
  const pending = !currentProject || !summary ? 0 : all ? summary.pending : summary.pending_mine;

  if (pending <= 0) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="outline" size="sm" onClick={() => navigate(`/proposals?scope=${scope}`)} aria-label={`${pending} proposals to review`}>
          <ListChecks className="size-4" aria-hidden />
          <span className="tabular-nums">{pending > 99 ? '99+' : pending}</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent>
        {pending} proposed change{pending === 1 ? '' : 's'}
        {scope === 'mine' ? ' to your findings' : ' in this project'} waiting for a decision
      </TooltipContent>
    </Tooltip>
  );
};

export default PendingProposalsButton;
