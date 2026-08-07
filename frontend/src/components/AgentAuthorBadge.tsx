/**
 * AgentAuthorBadge — marks content an AI agent wrote (v5.132.0).
 *
 * Notes written through the agent API are attributed to the operator whose
 * assist session produced them (`author_id` is the human), so nothing else in
 * the UI distinguishes a machine-written note from a hand-typed one. Since
 * notes feed findings and client-facing reports, "did a person assert this?"
 * has to be answerable at a glance.
 *
 * Renders nothing for `actor_type: 'user'`, so call sites can drop it in
 * unconditionally next to the author name.
 */
import React from 'react';
import { Bot } from 'lucide-react';
import { Badge } from './ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';

export interface AgentAuthorBadgeProps {
  actorType?: 'user' | 'agent' | null;
  className?: string;
}

export const AgentAuthorBadge: React.FC<AgentAuthorBadgeProps> = ({
  actorType,
  className,
}) => {
  if (actorType !== 'agent') return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="info-outline" className={className}>
          <Bot className="size-3 shrink-0" aria-hidden />
          Agent
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        Written by an AI assist session acting for this user — not typed by them.
      </TooltipContent>
    </Tooltip>
  );
};

export default AgentAuthorBadge;
