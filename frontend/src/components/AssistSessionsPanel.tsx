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
 */
import React, { useState } from 'react';
import { KeyRound, Loader2, PowerOff } from 'lucide-react';

import type { AssistSessionRow } from '../services/api';
import { endAssistSession } from '../services/api';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { useConfirm } from '../hooks/useConfirm';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';

export interface AssistSessionsPanelProps {
  sessions: AssistSessionRow[];
  /** Re-fetch after a session ends, so the list and any count agree. */
  onChanged: () => void | Promise<void>;
}

/** "4 minutes ago" / "2 hours ago". Local to this component, matching
 *  ProvenanceCard — there is no shared relative-time utility yet. */
const formatAge = (iso: string | null): string | null => {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const mins = Math.floor((Date.now() - then) / 60_000);
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 minute ago';
  if (mins < 60) return `${mins} minutes ago`;
  const hours = Math.floor(mins / 60);
  if (hours === 1) return '1 hour ago';
  if (hours < 24) return `${hours} hours ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? '1 day ago' : `${days} days ago`;
};

/** Time remaining on the session's key, as the operator's decision needs it.
 *  Returns null when there is no live key — the caller renders that as a
 *  distinct dead state rather than as "expires in 0 minutes". */
const formatRemaining = (iso: string | null): { label: string; urgent: boolean } | null => {
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
  onChanged,
}) => {
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [endingId, setEndingId] = useState<number | null>(null);

  if (sessions.length === 0) return null;

  const handleEnd = async (session: AssistSessionRow) => {
    const ok = await confirm({
      title: 'End this assist session?',
      severity: 'warning',
      confirmLabel: 'End session',
      // A string body (not JSX) so the shared ConfirmDialog wires it to
      // DialogDescription — it only does that for strings, and a rich body
      // leaves the dialog with no accessible description at all.
      body:
        'The agent\u2019s API key is revoked immediately. Any agent still running '
        + 'against it will start getting 401s mid-conversation. The session '
        + 'record is kept for the audit trail.',
    });
    if (!ok) return;
    setEndingId(session.id);
    try {
      await endAssistSession(session.id);
      toast.success(`Assist session #${session.id} ended — its key is revoked.`);
      await onChanged();
    } catch (err) {
      toast.error(formatApiError(err, 'Could not end the assist session.'));
    } finally {
      setEndingId(null);
    }
  };

  return (
    <div className="flex flex-col gap-xs rounded-control border border-border p-sm">
      {confirmEl}
      <div className="flex items-center gap-xs">
        <KeyRound className="size-4 shrink-0 text-warning" aria-hidden />
        <p className="text-metadata font-semibold">
          You have {sessions.length} active assist{' '}
          {sessions.length === 1 ? 'session' : 'sessions'}
        </p>
      </div>
      <p className="text-caption text-muted-foreground">
        Each one is a live agent key. Ending a session revokes its key
        immediately; one you leave drops off this list when its key expires, so
        there is nothing here to tidy up by hand.
      </p>

      <ul className="flex flex-col gap-xs">
        {sessions.map((s) => {
          const started = formatAge(s.started_at);
          const active = formatAge(s.last_activity_at);
          const canWrite = (s.capabilities?.length ?? 0) > 0;
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
                  {canWrite ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge variant="warning" tabIndex={0}>
                          Can write
                        </Badge>
                      </TooltipTrigger>
                      <TooltipContent>
                        Adds host notes and review status, limited to hosts
                        assigned to you.
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    <Badge variant="outline">Read-only</Badge>
                  )}
                  {/* A session whose agent never connected is worth
                      distinguishing from an idle one — it usually means the
                      key was minted and the prompt never pasted. */}
                  {!s.environment_probed && (
                    <Badge variant="outline">Not yet connected</Badge>
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
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                disabled={endingId === s.id}
                onClick={() => handleEnd(s)}
                aria-label={`End assist session ${s.id}`}
              >
                {endingId === s.id ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <PowerOff className="size-4" aria-hidden />
                )}
                End
              </Button>
            </li>
          );
        })}
      </ul>
    </div>
  );
};

export default AssistSessionsPanel;
