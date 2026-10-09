/**
 * End and Resume for a project agent session — the dialogs and the handlers,
 * shared by Agent Sessions and the session page (5.312.0) so the two surfaces
 * offer the same controls with the same wording.
 *
 * Which buttons to offer comes from the row's `can_end` / `can_resume` — the
 * caller's rights as the backend computes them (owner or project admin may
 * end; only the owner may resume). The page used to guess from the global
 * role, so a project admin was never offered End.
 *
 * An End or a Resume says itself which reads are out of date
 * (`AGENT_SESSION_READS`): a caller passes no re-read callback.
 */
import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import ResumeAgentSessionDialog from '../components/ResumeAgentSessionDialog';
import { CodeBlock } from '../components/ui/code-block';
import { useToast } from '../contexts/ToastContext';
import { endAgentSession, type AgentSessionRow } from '../services/api';
import { invalidateReads } from '../lib/query';
import { formatApiError } from '../utils/apiErrors';
import { AGENT_SESSION_READS, WRAP_UP_PROMPT, agentConnected } from '../utils/agentRuns';
import { useConfirm } from './useConfirm';
import { useProjectId } from './useProjectId';

export interface AgentSessionControls {
  /** Render once: the confirm dialog and the resume dialog. */
  dialogs: React.ReactNode;
  /** Resolves true when the session was ended (false: not confirmed, or it
   *  failed and was said).  The reads of sessions are asked again by the End
   *  itself; the answer is for a caller whose list is not a query. */
  requestEnd: (row: AgentSessionRow) => Promise<boolean>;
  requestResume: (row: AgentSessionRow) => void;
  /** Whether an End is in flight for this session. Several can be: ending A
   *  then B before A returns must keep both disabled until each settles. */
  isEnding: (sessionId: number) => boolean;
}

export const canEndSession = (row: AgentSessionRow): boolean =>
  row.kind === 'project' && row.status === 'active' && row.can_end === true;

export const canResumeSession = (row: AgentSessionRow): boolean =>
  row.kind === 'project' && row.status === 'active' && row.can_resume === true;

export function useAgentSessionControls(): AgentSessionControls {
  const toast = useToast();
  const [confirmEl, confirm] = useConfirm();
  const [endingIds, setEndingIds] = useState<ReadonlySet<number>>(() => new Set());
  const [resumeRow, setResumeRow] = useState<AgentSessionRow | null>(null);
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  // Several Ends can be in flight (one per session), so which rows are busy
  // is `endingIds`, not this mutation's one `isPending`.
  const { mutateAsync: end } = useMutation({
    mutationFn: (sessionId: number) => endAgentSession(projectId, sessionId),
  });

  const requestEnd = async (row: AgentSessionRow): Promise<boolean> => {
    // v5.219.0 — the wrap-up handoff. Sessions end because the human stops
    // typing, and nobody tells the agent it is done, so the feedback and the
    // clean exit never happen. If the agent is still reachable, the operator
    // can paste this first; End underneath remains the fallback.
    const ok = await confirm({
      title: `End agent session #${row.id}?`,
      severity: 'warning',
      confirmLabel: 'End session',
      body: (
        <div className="flex flex-col gap-sm">
          <p>
            The agent’s API key is revoked immediately; any agent still running against it
            gets 401s from its next call. The tests it proposed and the evidence it recorded
            stay — another session, or a person, carries them on. The session record stays
            for the audit trail.
          </p>
          {agentConnected(row) && (
            <div>
              <p className="mb-xxs text-metadata font-semibold">
                Agent still connected? Paste this to it first
              </p>
              <p className="mb-xxs text-caption text-muted-foreground">
                It files the feedback we ask every session for and ends the session cleanly
                (<span className="font-mono">end_reason: agent</span>). Ending from here is the
                fallback for an agent that is gone.
              </p>
              <CodeBlock
                text={WRAP_UP_PROMPT}
                label="wrap-up prompt"
                className="max-h-40 whitespace-pre-wrap break-words"
              />
            </div>
          )}
        </div>
      ),
    });
    if (!ok) return false;
    setEndingIds((prev) => new Set(prev).add(row.id));
    try {
      await end(row.id);
      toast.success(`Agent session #${row.id} ended — its key is revoked.`);
      // Every reader of the sessions: the page this was asked from, the top
      // bar, "your sessions".
      void invalidateReads(queryClient, ...AGENT_SESSION_READS);
      return true;
    } catch (err) {
      toast.error(formatApiError(err, 'Could not end the agent session.'));
      return false;
    } finally {
      // Only this session's flag: another End may still be in flight.
      setEndingIds((prev) => {
        const next = new Set(prev);
        next.delete(row.id);
        return next;
      });
    }
  };

  const dialogs = (
    <>
      {confirmEl}
      <ResumeAgentSessionDialog
        session={resumeRow}
        onOpenChange={(next) => { if (!next) setResumeRow(null); }}
      />
    </>
  );

  return {
    dialogs,
    requestEnd,
    requestResume: setResumeRow,
    isEnding: (sessionId) => endingIds.has(sessionId),
  };
}
