/**
 * The pieces every agent-session surface draws the same way (5.312.0): the
 * state badge, the authority badge, the key / ended line, the work a session
 * opened, and its End / Resume / Open buttons. Agent Sessions and the session
 * page both use them, so a session never reads "active" on one and "ended" on
 * the other again (the Sessions view derived its own status from the detail
 * row and disagreed with Runs).
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, Loader2, RotateCcw, Square } from 'lucide-react';

import type { AgentSessionRow, SessionPhase } from '../../services/api';
import { canEndSession, canResumeSession, type AgentSessionControls } from '../../hooks/useAgentSessionControls';
import {
  PHASE_KIND_LABEL,
  agentSessionPath,
  endedState,
  isOpenPhase,
  keyState,
  phasePath,
  type StateLine,
} from '../../utils/agentRuns';
import { cn } from '../../utils/cn';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

/** Where a session is started: the Operations page's "Start Agent Session"
 *  button — the param opens its dialog on arrival. */
export const START_SESSION_PATH = '/operations?start=agent-session';

/** A timeline row's operator: display name, falling back to the username. */
export const rowOperatorName = (row: AgentSessionRow): string | null =>
  row.user_full_name?.trim() || row.user_username || null;

/** One word for where a project session stands, from its status and its key:
 *  Live (an agent can use it now), Resumable (key ran out inside the session's
 *  lifetime), Expired (past it), Ended. */
export const sessionState = (row: AgentSessionRow): { label: string; variant: 'success' | 'warning' | 'muted' } => {
  if (row.status !== 'active') return { label: 'Ended', variant: 'muted' };
  const ks = keyState(row);
  if (ks?.tone === 'ok') return { label: 'Live', variant: 'success' };
  if (ks?.tone === 'warn') return { label: 'Resumable', variant: 'warning' };
  return { label: 'Expired', variant: 'muted' };
};

export const SessionStateBadge: React.FC<{ row: AgentSessionRow }> = ({ row }) => {
  const s = sessionState(row);
  return <Badge variant={s.variant} className="whitespace-nowrap">{s.label}</Badge>;
};

/** The line under the state: the key's state while active, how it ended after. */
export const sessionStateLine = (row: AgentSessionRow): StateLine | null =>
  keyState(row) ?? endedState(row);

export const StateLineText: React.FC<{ line: StateLine | null; className?: string }> = ({ line, className }) =>
  line ? (
    <span
      className={cn('truncate text-caption', line.tone === 'warn' ? 'text-warning' : 'text-muted-foreground', className)}
      title={line.text}
    >
      {line.text}
    </span>
  ) : null;

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin role',
  analyst: 'Analyst role',
  auditor: 'Auditor role',
  viewer: 'Viewer role',
  global_admin: 'Global admin',
};

/** The authority the session acts with — its operator's PROJECT ROLE, now
 *  (v5.288.0): the role at start is not recorded, and every call is checked
 *  against the current one, so the tooltip says so. */
export const AuthorityBadge: React.FC<{ role: string | null | undefined; operator: string | null }> = ({
  role,
  operator,
}) => {
  const who = operator ?? 'the operator';
  const explanation = !role
    ? `${who} is no longer a member of this project, so any further call with this session's key is refused.`
    : role === 'global_admin'
      ? `${who} is a global admin, which the agent gate treats as full access to every project.`
      : `The session acts with ${who}'s ${role} role on this project.`;
  const label = role ? ROLE_LABELS[role] ?? `${role} role` : 'No project role';
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" tabIndex={0} className="max-w-full overflow-hidden" aria-label={label}>
          <span className="truncate whitespace-nowrap">{label}</span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">
        {explanation} This is the operator&rsquo;s role now — it is checked on every
        call, and the role at the session&rsquo;s start is not recorded.
      </TooltipContent>
    </Tooltip>
  );
};

const phaseStatusClass = (phase: SessionPhase): string =>
  isOpenPhase(phase) ? 'text-warning' : 'text-muted-foreground';

/** The work a session opened, each linking to its own page. `limit` keeps a
 *  table cell to a few; the rest is a count that opens the session. */
export const PhaseLinks: React.FC<{
  row: AgentSessionRow;
  limit?: number;
  className?: string;
}> = ({ row, limit, className }) => {
  const phases = row.phases ?? [];
  if (phases.length === 0) {
    return <span className="text-caption text-muted-foreground">No runs or plans — inventory queries only</span>;
  }
  const shown = limit != null ? phases.slice(0, limit) : phases;
  const more = phases.length - shown.length;
  return (
    <ul className={cn('flex min-w-0 flex-wrap items-center gap-x-sm gap-y-xxs text-caption', className)}>
      {shown.map((phase) => (
        <li key={`${phase.kind}-${phase.id}`} className="min-w-0 max-w-full">
          <Link
            to={phasePath(phase)}
            className="flex min-w-0 items-baseline gap-xxs text-primary underline-offset-4 hover:underline"
            title={[PHASE_KIND_LABEL[phase.kind], phase.label, phase.status].filter(Boolean).join(' · ')}
            onClick={(e) => e.stopPropagation()}
          >
            <span className="whitespace-nowrap">{PHASE_KIND_LABEL[phase.kind]} #{phase.id}</span>
            <span className={cn('whitespace-nowrap', phaseStatusClass(phase))}>
              {phase.status.replace(/_/g, ' ')}
            </span>
          </Link>
        </li>
      ))}
      {more > 0 && (
        <li>
          <Link
            to={agentSessionPath(row.id)}
            className="whitespace-nowrap text-muted-foreground underline-offset-4 hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            +{more} more
          </Link>
        </li>
      )}
    </ul>
  );
};

/** Resume / End / Open for a project session. `labelled` draws words (the
 *  Live section and the session page); otherwise icon buttons (a table row). */
export const SessionActions: React.FC<{
  row: AgentSessionRow;
  controls: AgentSessionControls;
  labelled?: boolean;
  showOpen?: boolean;
}> = ({ row, controls, labelled = false, showOpen = false }) => {
  const ending = controls.isEnding(row.id);
  const resume = canResumeSession(row);
  const end = canEndSession(row);
  if (labelled) {
    return (
      <div className="flex flex-wrap items-center gap-xs">
        {resume && (
          <Button size="sm" variant="outline" onClick={() => controls.requestResume(row)}>
            <RotateCcw className="size-3.5" aria-hidden /> Resume
          </Button>
        )}
        {end && (
          <Button size="sm" variant="outline" onClick={() => void controls.requestEnd(row)} disabled={ending}>
            {ending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Square className="size-3.5 text-warning" aria-hidden />}
            End
          </Button>
        )}
        {showOpen && (
          <Button size="sm" variant="ghost" asChild>
            <Link to={agentSessionPath(row.id)} aria-label={`Open agent session ${row.id}`}>
              Open <ChevronRight className="size-3.5" aria-hidden />
            </Link>
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-xxs">
      {resume && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={(e) => { e.stopPropagation(); controls.requestResume(row); }}
              aria-label={`Resume agent session ${row.id}`}
            >
              <RotateCcw className="size-4 text-primary" aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Resume (reconnect an agent to this session)</TooltipContent>
        </Tooltip>
      )}
      {end && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={(e) => { e.stopPropagation(); void controls.requestEnd(row); }}
              disabled={ending}
              aria-label={`End agent session ${row.id}`}
            >
              {ending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Square className="size-4 text-warning" aria-hidden />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>End session (revokes its key)</TooltipContent>
        </Tooltip>
      )}
    </div>
  );
};
