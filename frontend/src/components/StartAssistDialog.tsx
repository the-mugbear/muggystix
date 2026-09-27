/**
 * StartAssistDialog — v4.29.0; unified session v2.337.0
 *
 * Project-level dialog for starting an agent session.  Mints one
 * project-scoped agent API key and shows the prompt + key to paste
 * into Claude Code / Codex / etc.  Since v2.337.0 the key is NOT
 * read-only or assist-only: the same session queries the inventory
 * and can open a reconnaissance, plan-generation, or execution phase,
 * all within the operator's own permissions.  (The component keeps
 * its name for now; the endpoint it calls is still /assist/start.)
 *
 * No scope picker — a session binds to the project and picks its
 * scope/plan when it opens a phase.  Resume lives on Agent Activity
 * (ResumeAgentSessionDialog, v5.214.0), not here: it acts on a row.
 *
 * Audit C1 (from recon dialog): the key is shown exactly once, so the
 * dialog does not close by accident while it is on screen. Since 5.309.0
 * that is the Done footer (KeyHandoffFooter) rather than an "I copied the
 * key" checkbox: copying anything that holds the key clears it; otherwise
 * Done warns once. 5.309.0 also cut the dialog from ~770 words that
 * scrolled to one sentence, one field, and one copy for the chosen client.
 */
import React, { useCallback, useState } from 'react';
import { Loader2, MessageCircleQuestion } from 'lucide-react';
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
import { startAssistSession, type AssistSessionRow, type StartAssistResponse } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import AssistSessionsPanel from './AssistSessionsPanel';
import AgentSessionCredentials, { KeyHandoffFooter } from './AgentSessionCredentials';

export interface StartAssistDialogProps {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  /** Optional callback fired AFTER the dialog closes with an active session result. */
  onSessionStarted?: (sessionId: number) => void;
  /** The operator's own active sessions, shown above the start form so they
   *  can see (and revoke) a key they already hold. Omit to hide the panel. */
  mySessions?: AssistSessionRow[];
  /** Re-fetch `mySessions` after this dialog starts or ends one. */
  onSessionsChanged?: () => void | Promise<void>;
}

export const StartAssistDialog: React.FC<StartAssistDialogProps> = ({
  open,
  onOpenChange,
  onSessionStarted,
  mySessions = [],
  onSessionsChanged,
}) => {
  const [purpose, setPurpose] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StartAssistResponse | null>(null);
  const [keyCopied, setKeyCopied] = useState(false);

  const reset = useCallback(() => {
    setPurpose('');
    setLoading(false);
    setError(null);
    setResult(null);
    setKeyCopied(false);
  }, []);

  const handleStart = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await startAssistSession({
        purpose: purpose.trim() || undefined,
      });
      setResult(resp);
      // The new key is live the moment this returns — reflect it wherever the
      // operator's session count is shown.
      await onSessionsChanged?.();
    } catch (err) {
      setError(formatApiError(err, 'Could not start assist session.'));
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    const sid = result?.assist_session_id;
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
            {result ? `Connect your agent — session #${result.assist_session_id}` : 'Start Agent Session'}
          </DialogTitle>
          <DialogDescription>
            {/* 5.309.0 — one sentence. It was said four times across the two
                steps, and the TTL shown before starting was a hard-coded 4 h
                while the server issued 24: the TTL now comes only from the
                response. */}
            {result ? (
              <>
                Copy the setup for your agent — the key is in it. It is valid for{' '}
                {result.key_ttl_hours} hours and the agent can renew it.
              </>
            ) : (
              <>
                Connect Claude Code, Codex or VS Code Copilot to this project. The agent
                works with your permissions, and a plan still needs your approval before it runs.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-md">
          {!result ? (
            <>
              <AssistSessionsPanel
                sessions={mySessions}
                onChanged={() => onSessionsChanged?.() ?? Promise.resolve()}
              />
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
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
            </>
          ) : (
            // v5.214.0 — shared with the resume dialog on Agent Activity.
            <AgentSessionCredentials
              apiKey={result.api_key}
              instructions={result.instructions}
              mcpClients={result.mcp_clients ?? []}
              onCopied={() => setKeyCopied(true)}
            />
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
              <Button onClick={handleStart} disabled={loading}>
                {loading ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <MessageCircleQuestion className="size-4" aria-hidden />
                )}
                Start session
              </Button>
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
