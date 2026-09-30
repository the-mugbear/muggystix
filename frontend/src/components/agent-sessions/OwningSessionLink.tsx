/**
 * OwningSessionLink (5.313.0) — where a plan or execution run's
 * agent can be resumed or ended: its agent session. Replaces the per-run and
 * per-plan Resume / Regenerate-key controls, which minted keys for one
 * workflow; resuming and rotating are session-level only (Agent Sessions and
 * each session's page, hooks/useAgentSessionControls).
 *
 * Without a session id (a pre-consolidation run, or a response that does not
 * carry it) it links to the Agent Sessions list.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { Bot } from 'lucide-react';

import { Button } from '../ui/button';
import { SESSIONS_LIST_PATH, agentSessionPath } from '../../utils/agentRuns';

export const OwningSessionLink: React.FC<{ agentSessionId?: number | null }> = ({ agentSessionId }) => (
  <Button asChild size="sm" variant="outline">
    <Link
      to={agentSessionId != null ? agentSessionPath(agentSessionId) : SESSIONS_LIST_PATH}
      title="Resume, renew or end the agent from its session"
    >
      <Bot className="size-4" aria-hidden />
      {agentSessionId != null ? `Agent session #${agentSessionId}` : 'Agent Sessions'}
    </Link>
  </Button>
);

export default OwningSessionLink;
