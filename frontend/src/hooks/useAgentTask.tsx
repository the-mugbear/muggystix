/**
 * Hand a one-line task to the operator's agent (5.322.0).
 *
 * With a session already live, the task is copied and a toast says where to
 * paste it — opening the Start Agent Session dialog to show one line was a
 * detour. With none, that dialog opens with the task, as before. A caller
 * renders `dialog` once and calls `give(task)` from as many controls as it has.
 *
 * 5.363.0 (owner decision 2026-10-10) — whether to copy or to open the dialog
 * is still decided by a read made AT THE CLICK, never by a list that may be a
 * minute old.  That read is the same cache entry as "your sessions"
 * (`myAssistSessionsRead`), and the dialog shows that list itself: a Start, an
 * End or a Resume reaches it like every other reader.  (The hook kept the
 * click's answer in state, and the dialog and its panel each had a callback
 * whose only job was to tell it to ask again.)
 */
import React, { useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import StartAssistDialog from '../components/StartAssistDialog';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import type { AgentSessionRow } from '../services/api';
import { hasLiveKey } from '../utils/agentRuns';
import { copyToClipboard } from '../utils/clipboard';
import { useCanStartAgentSession } from './useCanStartAgentSession';
import { myAssistSessionsRead, useMyAssistSessions } from './useMyAssistSessions';
import { useProjectId } from './useProjectId';

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
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  const [instruction, setInstruction] = useState<string | null>(null);
  // The dialog's "your sessions": read only while the dialog is open.
  const { sessions } = useMyAssistSessions({ enabled: instruction != null });

  /** This operator's sessions whose key still works, as of now. */
  const mine = useCallback(async (): Promise<AgentSessionRow[]> => {
    if (user?.id == null) return [];
    try {
      const { sessions: rows } = await queryClient.fetchQuery({
        ...myAssistSessionsRead(projectId, user.id),
        staleTime: 0,
      });
      const now = Date.now();
      return rows.filter((s) => hasLiveKey(s, now));
    } catch {
      return [];
    }
  }, [user?.id, projectId, queryClient]);

  const give = useCallback(async (task: string) => {
    const live = await mine();
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
      instruction={instruction}
    />
  ) : null;

  return { allowed, give, dialog };
};

export default useAgentTask;
