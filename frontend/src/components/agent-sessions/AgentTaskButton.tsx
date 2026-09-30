/**
 * AgentTaskButton (5.313.0) — the one entry point for "have my agent do this"
 * on an object: a scope's scan, a plan's work, a host selection's
 * plan. It replaces the per-workflow Start scan / Generate with AI / Execute
 * with AI buttons, each of which minted its own key for one workflow.
 *
 * There is one way to start an agent: the operator's project agent session
 * (StartAssistDialog). This button opens that dialog with a one-line task
 * (`agentInstruction` in utils/agentRuns) to copy; when the operator already
 * has a live session the dialog points to it and says to paste the task
 * there instead. The agent opens the run or plan itself — nothing waits on an
 * approval.
 */
import React, { useState } from 'react';
import { Bot } from 'lucide-react';

import { Button, type ButtonProps } from '../ui/button';
import StartAssistDialog from '../StartAssistDialog';
import { useMyAssistSessions } from '../../hooks/useMyAssistSessions';
import { useCanStartAgentSession } from '../../hooks/useCanStartAgentSession';

export interface AgentTaskButtonProps {
  /** The task to give the agent, from `agentInstruction`. */
  instruction: string;
  /** Button text; defaults to "Ask your agent". */
  label?: React.ReactNode;
  variant?: ButtonProps['variant'];
  size?: ButtonProps['size'];
  className?: string;
  disabled?: boolean;
  title?: string;
}

export const AgentTaskButton: React.FC<AgentTaskButtonProps> = ({
  instruction,
  label = 'Ask your agent',
  variant = 'outline',
  size = 'sm',
  className,
  disabled,
  title,
}) => {
  const [open, setOpen] = useState(false);
  // Starting a session needs project auditor; below it there is nothing to offer.
  const allowed = useCanStartAgentSession();
  if (!allowed) return null;
  return (
    <>
      <Button
        variant={variant}
        size={size}
        className={className}
        disabled={disabled}
        title={title ?? instruction}
        onClick={() => setOpen(true)}
      >
        <Bot className="size-4" aria-hidden />
        {label}
      </Button>
      {open && <AgentTaskDialog instruction={instruction} onClose={() => setOpen(false)} />}
    </>
  );
};

/** Mounted only while open, so the operator's sessions are fetched when the
 *  dialog is shown and a page can carry several buttons at no cost. */
const AgentTaskDialog: React.FC<{ instruction: string; onClose: () => void }> = ({
  instruction,
  onClose,
}) => {
  const { sessions, refresh } = useMyAssistSessions();
  return (
    <StartAssistDialog
      open
      onOpenChange={(next) => { if (!next) onClose(); }}
      mySessions={sessions}
      onSessionsChanged={refresh}
      instruction={instruction}
    />
  );
};

export default AgentTaskButton;
