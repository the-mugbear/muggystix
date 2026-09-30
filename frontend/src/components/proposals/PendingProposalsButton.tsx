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
import { PROPOSALS_CHANGED_EVENT } from '../../utils/proposalEvents';
import { Button } from '../ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

const PendingProposalsButton: React.FC = () => {
  const navigate = useNavigate();
  const { currentProject } = useProject();
  const [pending, setPending] = useState(0);
  // 5.318.0 — your own (proposals about findings you authored or own); a
  // project admin, who may accept any report text, sees the project's.  An
  // admin's review of every finding used to put the whole run in every
  // member's top bar.
  const [scope, setScope] = useState<'mine' | 'all'>('mine');

  const load = useCallback(async () => {
    if (!currentProject) { setPending(0); return; }
    try {
      const s = await getProposalSummary();
      const all = s.viewer_is_project_admin;
      setScope(all ? 'all' : 'mine');
      setPending(all ? s.pending : s.pending_mine);
    } catch {
      // Keep the last count; the Proposals page reports its own failures.
    }
  }, [currentProject]);

  useEffect(() => { void load(); }, [load]);
  // A decision anywhere (a finding page, the Proposals page, a draft) re-reads at once.
  useEffect(() => {
    const onChange = () => { void load(); };
    window.addEventListener(PROPOSALS_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(PROPOSALS_CHANGED_EVENT, onChange);
  }, [load]);
  useVisibilityPoll(load, 60_000, !!currentProject);

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
