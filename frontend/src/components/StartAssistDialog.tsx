/**
 * StartAssistDialog — v4.29.0; unified session v2.337.0
 *
 * Project-level dialog for starting an agent session — THE way an agent
 * starts. Mints one project-scoped agent API key and shows the prompt + key
 * to paste into Claude Code / Codex / etc. The same session queries the
 * inventory, uploads its scans, proposes tests on hosts and records evidence,
 * all within the operator's own project role; nothing waits on an approval.
 * (The component keeps its name; the endpoint it calls is /assist/start.)
 *
 * `instruction` (5.313.0) — the per-object entry points (a scope's scan, a
 * plan's work, a host selection) open this dialog with a one-line task for
 * the agent. It is shown to copy before and after starting, and when the
 * operator already has a live session the dialog says to paste it there
 * instead of starting another (AgentTaskButton).
 *
 * No scope picker — a session binds to the project and picks its
 * scope/plan when it opens a phase.  Resume lives on Agent Sessions and each
 * session's page (ResumeAgentSessionDialog, v5.214.0), not here: it acts on a
 * row.  5.312.1 — the panel's sessions link to their pages, and the dialog
 * links to Agent Sessions, so a session shown here is one click from its
 * controls.
 *
 * Audit C1: the key is shown exactly once, so the
 * dialog does not close by accident while it is on screen. Since 5.309.0
 * that is the Done footer (KeyHandoffFooter) rather than an "I copied the
 * key" checkbox: copying anything that holds the key clears it; otherwise
 * Done warns once. 5.309.0 also cut the dialog from ~770 words that
 * scrolled to one sentence, one field, and one copy for the chosen client.
 */
import React, { useCallback, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Check, Copy, Loader2, MessageCircleQuestion } from 'lucide-react';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { startAssistSession, type AgentSessionRow } from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
import { SECRET_MUTATION, invalidateReads, queryErrorText } from '../lib/query';
import AssistSessionsPanel from './AssistSessionsPanel';
import AgentSessionCredentials, { KeyHandoffFooter } from './AgentSessionCredentials';
import { CodeBlock } from './ui/code-block';
import { SESSIONS_LIST_PATH, agentSessionPath, hasLiveKey } from '../utils/agentRuns';
import { copyToClipboard } from '../utils/clipboard';

export interface StartAssistDialogProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  /** Optional callback fired AFTER the dialog closes with an active session result. */
  onSessionStarted?: (sessionId: number) => void;
  /** The operator's own active sessions, shown above the start form so they
   *  can see (and revoke) a key they already hold. Omit to hide the panel. */
  mySessions?: AgentSessionRow[];
  /** A one-line task to give the agent (e.g. "Propose tests in BlueStick
   *  for these hosts only…"), shown to copy before and after the session
   *  starts. */
  instruction?: string;
}

const InstructionBlock: React.FC<{ text: string; lead: React.ReactNode }> = ({ text, lead }) => (
  <div className="flex min-w-0 flex-col gap-xxs">
    <p className="text-metadata font-semibold">{lead}</p>
    <CodeBlock text={text} label="agent instruction" className="whitespace-pre-wrap break-words" />
  </div>
);

export const StartAssistDialog: React.FC<StartAssistDialogProps> = ({
  open,
  onOpenChange,
  onSessionStarted,
  mySessions = [],
  instruction,
}) => {
  const liveSession = instruction ? mySessions.find((s) => hasLiveKey(s, Date.now())) : undefined;
  const [purpose, setPurpose] = useState('');
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  // The key is shown once: it is this dialog's mutation result and nothing
  // else's — never a query, and dropped with the dialog (`reset`, `gcTime: 0`).
  const start = useMutation({
    mutationFn: (stated: string | undefined) => startAssistSession(projectId, { purpose: stated }),
    ...SECRET_MUTATION,
    onSuccess: () => {
      // The new key is live the moment this returns — reflect it wherever the
      // operator's sessions are shown: every read of sessions (this dialog's
      // own list, the rail, the Operations line).  A re-read that fails does
      // not fail the start: the key in its answer is shown once.
      void invalidateReads(queryClient, 'listAgentSessions');
    },
  });
  const loading = start.isPending;
  const error = queryErrorText(start.error, 'Could not start assist session.');
  const result = start.data ?? null;
  const { reset: resetStart } = start;
  const [keyCopied, setKeyCopied] = useState(false);
  // With a session already live the task is the point: copying it is the
  // primary action, and starting another session is asked for explicitly.
  const [startAnother, setStartAnother] = useState(false);
  const [taskCopied, setTaskCopied] = useState(false);
  const continuing = liveSession != null && !startAnother;

  const reset = useCallback(() => {
    setPurpose('');
    resetStart();
    setKeyCopied(false);
    setStartAnother(false);
    setTaskCopied(false);
  }, [resetStart]);

  const handleStart = () => start.mutate(purpose.trim() || undefined);

  const handleClose = () => {
    const sid = result?.agent_session_id;
    reset();
    onOpenChange(false);
    if (sid && onSessionStarted) onSessionStarted(sid);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          onOpenChange(true);
          return;
        }
        // Veto close while in-flight.
        if (loading) return;
        // Veto an accidental close while the key is on screen and nothing
        // holding it was copied (audit C1) — Done says why.
        if (result && !keyCopied) return;
        if (result) {
          handleClose();
        } else {
          reset();
          onOpenChange(false);
        }
      }}
    >
      <DialogContent size="lg" showClose={!result || keyCopied}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <MessageCircleQuestion className="size-5 text-primary" aria-hidden />
            {result
              ? `Connect your agent — session #${result.agent_session_id}`
              : 'Start Agent Session'}
          </DialogTitle>
          <DialogDescription>
            {/* 5.309.0 — one sentence. It was said four times across the two
                steps, and the TTL shown before starting was a hard-coded 4 h
                while the server issued 24: the TTL now comes only from the
                response. */}
            {result ? (
              <>
                Copy the setup for your agent — the key is in it. It is valid for{' '}
                {result.key_ttl_hours} hours and the agent can renew it. The session then
                shows under <strong>Agents → Agent Sessions</strong>, where you can follow,
                resume or end it.
              </>
            ) : (
              <>
                Connect Claude Code, Codex or VS Code Copilot to this project. The agent
                works with your permissions and shows you every command it runs.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-md">
          {!result ? (
            <>
              {instruction && (
                <InstructionBlock
                  text={instruction}
                  lead={liveSession ? (
                    <>
                      Your agent session{' '}
                      <Link
                        to={agentSessionPath(liveSession.id)}
                        onClick={() => { reset(); onOpenChange(false); }}
                        className="text-primary underline-offset-4 hover:underline"
                      >
                        #{liveSession.id}
                      </Link>{' '}
                      is live — paste this to its agent.
                    </>
                  ) : 'Start a session, connect your agent, then give it this:'}
                />
              )}
              <AssistSessionsPanel
                sessions={mySessions}
                onNavigate={() => { reset(); onOpenChange(false); }}
              />
              {mySessions.length === 0 && (
                // 5.312.1 — with no live key the panel is hidden, but a session
                // whose key ran out may still be resumable; say where it is.
                <p className="text-caption text-muted-foreground">
                  Continuing an earlier session?{' '}
                  <Link
                    to={SESSIONS_LIST_PATH}
                    onClick={() => { reset(); onOpenChange(false); }}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    Resume it from Agent Sessions
                  </Link>{' '}
                  instead of starting a new one.
                </p>
              )}
              {!continuing && (
              <div className="flex flex-col gap-xxs">
                <Label htmlFor="assist-purpose">
                  What is it for? <span className="text-muted-foreground">(optional — shown in the audit log)</span>
                </Label>
                <Input
                  id="assist-purpose"
                  placeholder="e.g. Looking for FTP exposure across all scopes"
                  value={purpose}
                  onChange={(e) => setPurpose(e.target.value)}
                  maxLength={400}
                  disabled={loading}
                />
              </div>
              )}
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
            </>
          ) : (
            // v5.214.0 — shared with the resume dialog on Agent Activity.
            <>
              <AgentSessionCredentials
                apiKey={result.api_key}
                instructions={result.instructions}
                mcpClients={result.mcp_clients ?? []}
                onCopied={() => setKeyCopied(true)}
              />
              {instruction && (
                <InstructionBlock text={instruction} lead="Once it is connected, give your agent this:" />
              )}
            </>
          )}
        </DialogBody>
        <DialogFooter>
          {!result ? (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  reset();
                  onOpenChange(false);
                }}
                disabled={loading}
              >
                Cancel
              </Button>
              {continuing ? (
                <>
                  <Button variant="outline" onClick={() => setStartAnother(true)}>
                    Start another session
                  </Button>
                  <Button
                    onClick={async () => {
                      if (instruction && await copyToClipboard(instruction)) setTaskCopied(true);
                    }}
                  >
                    {taskCopied ? (
                      <Check className="size-4" aria-hidden />
                    ) : (
                      <Copy className="size-4" aria-hidden />
                    )}
                    {taskCopied ? 'Copied — paste it to your agent' : 'Copy task'}
                  </Button>
                </>
              ) : (
                <Button onClick={handleStart} disabled={loading}>
                  {loading ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <MessageCircleQuestion className="size-4" aria-hidden />
                  )}
                  Start session
                </Button>
              )}
            </>
          ) : (
            <KeyHandoffFooter
              copied={keyCopied}
              onDone={handleClose}
              note="Resume from Agent Activity if the agent process dies."
            />
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default StartAssistDialog;
