/**
 * The operator's live assist sessions, with the ability to end one.
 *
 * Rendered above the start form so the answer to "do I already have an agent
 * running?" is in front of the operator at the moment they're about to start
 * another. Renders nothing when there are none — the common case, and a
 * permanent "no active sessions" panel would be noise.
 *
 * Starting a second session is still allowed (an operator may legitimately run
 * one agent per machine), so this informs rather than blocks.
 *
 * 5.312.1 — each session opens its page (`/agent-sessions/:id`: its work,
 * notes, calls, Resume and End), and the panel links to Agent Sessions, which
 * also lists the sessions this panel does not: ones whose key ran out and that
 * wait to be resumed.
 *
 * 5.328.0 — the rows are the session list's own rows (`AgentSessionRow`): a
 * session has one id, so there is nothing to translate before linking to it
 * or ending it.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight, KeyRound, Loader2, PowerOff } from 'lucide-react';

import type { AgentSessionRow } from '../services/api';
import { SESSIONS_LIST_PATH, agentSessionPath } from '../utils/agentRuns';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { useAgentSessionControls } from '../hooks/useAgentSessionControls';
import { formatRelativeTime } from '../utils/relativeTime';

export interface AssistSessionsPanelProps {
  sessions: AgentSessionRow[];
  /** Called when a link leaves for a session's page, so the host dialog closes. */
  onNavigate?: () => void;
}

/** "4 minutes ago" — the long form, and null when there is no timestamp so the
 *  caller renders nothing rather than a placeholder. */
const formatAge = (iso: string | null | undefined): string | null =>
  formatRelativeTime(iso ?? null, { style: 'long', fallback: null });

/** Time remaining on the session's key, as the operator's decision needs it.
 *  Returns null when there is no live key — the caller renders that as a
 *  distinct dead state rather than as "expires in 0 minutes". */
const formatRemaining = (iso: string | null | undefined): { label: string; urgent: boolean } | null => {
  if (!iso) return null;
  const until = new Date(iso).getTime();
  if (Number.isNaN(until)) return null;
  const mins = Math.floor((until - Date.now()) / 60_000);
  if (mins <= 0) return { label: 'key expired', urgent: true };
  if (mins < 60) return { label: `expires in ${mins} min`, urgent: true };
  const hours = Math.floor(mins / 60);
  const rem = mins % 60;
  return {
    label: `expires in ${hours}h${rem ? ` ${rem}m` : ''}`,
    // Under an hour is the point where starting fresh beats relying on it.
    urgent: false,
  };
};

export const AssistSessionsPanel: React.FC<AssistSessionsPanelProps> = ({
  sessions,
  onNavigate,
}) => {
  const controls = useAgentSessionControls();

  if (sessions.length === 0) return null;

  return (
    <div className="flex flex-col gap-xs rounded-control border border-border p-sm">
      {controls.dialogs}
      <div className="flex items-center gap-xs">
        <KeyRound className="size-4 shrink-0 text-warning" aria-hidden />
        <p className="text-metadata font-semibold">
          You have {sessions.length} active agent{' '}
          {sessions.length === 1 ? 'session' : 'sessions'}
        </p>
      </div>
      <p className="text-caption text-muted-foreground">
        Each one is a live agent key. Open a session to see what it is doing, resume
        it or end it. Ending revokes its key immediately; one you leave drops off
        this list when its key expires (it can still be resumed from Agent Sessions).
      </p>

      <ul className="flex flex-col gap-xs">
        {sessions.map((s) => {
          const started = formatAge(s.started_at);
          const active = formatAge(s.last_activity_at);
          const remaining = formatRemaining(s.key_expires_at);
          return (
            <li
              key={s.id}
              className="flex items-start gap-sm border-t border-border pt-xs first:border-t-0 first:pt-0"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-xs">
                  <span className="font-mono text-caption text-muted-foreground">
                    #{s.id}
                  </span>
                  {/* v5.189.0 — was a read-only/can-write badge sourced from
                      the session's capability grant. Grants are gone: a session
                      acts with its operator's own project permissions, so there
                      is no per-session authority to report here. */}
                  {/* v5.203.0 — connection state from observed calls. "Waiting"
                      is the common dead end: key minted, client never
                      connected. A past call proves the client connected, not
                      that it is still running — so this is never a green
                      "live" badge; "last used" beside it is the liveness cue. */}
                  {(s.connection ?? 'none') === 'none' ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge variant="outline" tabIndex={0}>
                          Waiting for client
                        </Badge>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-sm">
                        No authenticated call has reached this session yet. Configure
                        your client, relaunch it, and ask it the verification prompt
                        from the start dialog.
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge variant="outline" tabIndex={0}>
                          {s.connection === 'mcp' ? 'MCP verified' : 'Connected via curl'}
                        </Badge>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-sm">
                        {s.connection === 'mcp'
                          ? 'An authenticated tool call arrived through the MCP transport — the client, the certificate trust, and the key all work.'
                          : 'Calls arrived by direct HTTP (the pasted-prompt path). The key works; no MCP client has used it.'}
                      </TooltipContent>
                    </Tooltip>
                  )}
                  {/* The field that decides "end it or let it lapse". A null
                      expiry means no live key remains, which is a different
                      state from "expires soon" and must not read as one. */}
                  {remaining ? (
                    <Badge variant={remaining.urgent ? 'warning-outline' : 'outline'}>
                      {remaining.label}
                    </Badge>
                  ) : (
                    <Badge variant="muted">No live key</Badge>
                  )}
                </div>
                {s.purpose && (
                  <p className="mt-xxs line-clamp-2 break-words text-metadata text-foreground">
                    {s.purpose}
                  </p>
                )}
                <p className="mt-xxs text-caption text-muted-foreground">
                  {started ? `Started ${started}` : 'Start time unknown'}
                  {active ? ` · last used ${active}` : ' · not used yet'}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-xs">
                <Button variant="outline" size="sm" asChild>
                  <Link
                    to={agentSessionPath(s.id)}
                    onClick={onNavigate}
                    aria-label={`Open agent session ${s.id}`}
                  >
                    Open
                    <ChevronRight className="size-4" aria-hidden />
                  </Link>
                </Button>
                {/* End goes through the one path every session surface uses
                    (`useAgentSessionControls` — the wrap-up prompt while the
                    agent is still connected). */}
                <Button
                  variant="outline"
                  size="sm"
                  disabled={controls.isEnding(s.id)}
                  onClick={() => void controls.requestEnd(s)}
                  aria-label={`End agent session ${s.id}`}
                >
                  {controls.isEnding(s.id) ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <PowerOff className="size-4" aria-hidden />
                  )}
                  End
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      <Link
        to={SESSIONS_LIST_PATH}
        onClick={onNavigate}
        className="self-start text-caption text-primary underline-offset-4 hover:underline"
      >
        All agent sessions — including ones waiting to be resumed
      </Link>
    </div>
  );
};

export default AssistSessionsPanel;
