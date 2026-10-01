/**
 * Hand a one-line task to the operator's agent (5.322.0).
 *
 * With a session already live, the task is copied and a toast says where to
 * paste it — opening the Start Agent Session dialog to show one line was a
 * detour. With none, that dialog opens with the task, as before. A caller
 * renders `dialog` once and calls `give(task)` from as many controls as it has.
 */
import React, { useCallback, useState } from 'react';

import StartAssistDialog from '../components/StartAssistDialog';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { listAgentSessions, type AgentSessionRow } from '../services/api';
import { hasLiveKey, myActiveSessionFilters } from '../utils/agentRuns';
import { copyToClipboard } from '../utils/clipboard';
import { useCanStartAgentSession } from './useCanStartAgentSession';

export interface AgentTask {
  /** False below project auditor: there is nothing to offer. */
  allowed: boolean;
  give: (instruction: string) => Promise<void>;
  dialog: React.ReactNode;
}

export const useAgentTask = (): AgentTask => {
  const allowed = useCanStartAgentSession();
  const { user } = useAuth();
  const toast = useToast();
  const [instruction, setInstruction] = useState<string | null>(null);
  const [sessions, setSessions] = useState<AgentSessionRow[]>([]);

  /** This operator's sessions whose key still works (as `useMyAssistSessions`). */
  const mine = useCallback(async (): Promise<AgentSessionRow[]> => {
    if (user?.id == null) return [];
    try {
      const { sessions: rows } = await listAgentSessions(myActiveSessionFilters(user.id));
      const now = Date.now();
      return rows.filter((s) => hasLiveKey(s, now));
    } catch {
      return [];
    }
  }, [user?.id]);

  const give = useCallback(async (task: string) => {
    const live = await mine();
    setSessions(live);
    if (live.length > 0 && await copyToClipboard(task)) {
      toast.success(`Task copied — paste it to your agent (session #${live[0].id} is live).`, { autoHideMs: 6000 });
      return;
    }
    setInstruction(task);
  }, [mine, toast]);

  const dialog = instruction != null ? (
    <StartAssistDialog
      open
      onOpenChange={(next) => { if (!next) setInstruction(null); }}
      mySessions={sessions}
      onSessionsChanged={async () => { setSessions(await mine()); }}
      instruction={instruction}
    />
  ) : null;

  return { allowed, give, dialog };
};

export default useAgentTask;
