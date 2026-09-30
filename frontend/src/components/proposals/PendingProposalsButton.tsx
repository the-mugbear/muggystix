/**
 * The top bar's pending-proposals count (v5.316.0): how many changes an agent
 * or an AI draft proposed that nobody has decided yet.  Renders nothing when
 * there are none, so a project that never uses agents shows no new chrome.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ListChecks } from 'lucide-react';

import { getProposalSummary } from '../../services/api';
import { useProject } from '../../contexts/ProjectContext';
import { useVisibilityPoll } from '../../hooks/useVisibilityPoll';
import { Button } from '../ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

const PendingProposalsButton: React.FC = () => {
  const navigate = useNavigate();
  const { currentProject } = useProject();
  const [pending, setPending] = useState(0);

  const load = useCallback(async () => {
    if (!currentProject) { setPending(0); return; }
    try {
      setPending((await getProposalSummary()).pending);
    } catch {
      // Keep the last count; the Proposals page reports its own failures.
    }
  }, [currentProject]);

  useEffect(() => { void load(); }, [load]);
  useVisibilityPoll(load, 60_000, !!currentProject);

  if (pending <= 0) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="outline" size="sm" onClick={() => navigate('/proposals')} aria-label={`${pending} proposals to review`}>
          <ListChecks className="size-4" aria-hidden />
          <span className="tabular-nums">{pending > 99 ? '99+' : pending}</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent>{pending} proposed change{pending === 1 ? '' : 's'} waiting for a decision</TooltipContent>
    </Tooltip>
  );
};

export default PendingProposalsButton;
