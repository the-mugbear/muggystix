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
 * Audit C1 (from recon dialog): the key is shown exactly once and
 * the operator must check the "I copied the key" box before the
 * dialog can be dismissed.  Key persists in sessionStorage via the
 * `result` state for the duration of the dialog so a tab reload
 * during the session recovers it.
 */
import React, { useCallback, useState } from 'react';
import { Loader2, MessageCircleQuestion } from 'lucide-react';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Checkbox } from './ui/checkbox';
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
import AgentSessionCredentials from './AgentSessionCredentials';

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
  const [keyAcknowledged, setKeyAcknowledged] = useState(false);

  const reset = useCallback(() => {
    setPurpose('');
    setLoading(false);
    setError(null);
    setResult(null);
    setKeyAcknowledged(false);
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
        // Veto close while the key is on screen unacknowledged (audit C1).
        if (result && !keyAcknowledged) return;
        if (result) {
          handleClose();
        } else {
          reset();
          onOpenChange(false);
        }
      }}
    >
      <DialogContent size="xl" showClose={!result || keyAcknowledged}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <MessageCircleQuestion className="size-5 text-primary" aria-hidden />
            Start Agent Session
          </DialogTitle>
          <DialogDescription>
            {/* v2.337.0 — one project session does every kind of work; the
                key is no longer read-only or assist-only. TTL read from the
                response so it stays in lockstep with the backend. */}
            Mints one project-scoped agent API key ({result?.key_ttl_hours ?? 4} h TTL) and shows
            the prompt to paste into Claude Code / Codex / Cursor. The same key
            answers questions about the inventory and can open a reconnaissance,
            plan-generation, or execution phase — always within your own
            permissions, and a plan still needs human approval before it runs.
            The key is shown once; copy it before closing.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-md">
          {!result ? (
            <>
              <AssistSessionsPanel
                sessions={mySessions}
                onChanged={() => onSessionsChanged?.() ?? Promise.resolve()}
              />
              <Alert variant="info">
                <AlertDescription>
                  Start a session to work the project with an agent: ask
                  interactive questions ("which hosts expose FTP?", "summarize
                  critical findings"), or have it open a reconnaissance run on a
                  scope, draft a test plan, and execute an approved one — all
                  with this one key. It answers from BlueStick's data and asks
                  before anything that runs against a host or changes the plan.
                </AlertDescription>
              </Alert>
              <div className="flex flex-col gap-xxs">
                <Label htmlFor="assist-purpose">
                  Purpose <span className="text-muted-foreground">(optional, surfaced on the audit log)</span>
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
              {/* v5.189.0 — the write-access checkbox is gone with the
                  capability system. The session acts with your own permissions
                  on this project, so there is nothing to opt into. */}
              <Alert>
                <AlertDescription className="text-caption">
                  The session acts with <strong>your</strong> permissions on this
                  project, re-checked on every call. Anything you can change, it
                  can change; anything you cannot, it cannot. Notes it writes are
                  attributed to you and marked &ldquo;Agent&rdquo;.
                </AlertDescription>
              </Alert>
              {error && (
                <Alert variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
            </>
          ) : (
            <div className="flex flex-col gap-sm">
              <Alert variant="success">
                <AlertDescription>
                  Agent session <strong>#{result.assist_session_id}</strong>{' '}
                  started for project <strong>{result.project_name}</strong>.
                </AlertDescription>
              </Alert>
              <Alert variant="info">
                <AlertDescription>
                  This session acts with <strong>your permissions</strong> on
                  this project, re-checked on every call. The one key can query,
                  open a reconnaissance run, draft a plan, and execute an
                  approved one — notes it writes appear under your name with an
                  &ldquo;Agent&rdquo; badge. A plan still needs human approval
                  before it runs, and the session cannot reach other projects.
                </AlertDescription>
              </Alert>
              {/* v5.214.0 — the key + connect tabs are shared with the resume
                  dialog on Agent Activity (AgentSessionCredentials). */}
              <AgentSessionCredentials
                apiKey={result.api_key}
                instructions={result.instructions}
                mcpClients={result.mcp_clients ?? []}
              />
            </div>
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
            <div className="flex w-full flex-col gap-xs">
              <label className="flex items-start gap-xs text-metadata">
                <Checkbox
                  checked={keyAcknowledged}
                  onCheckedChange={(v) => setKeyAcknowledged(v === true)}
                  aria-label="I copied the agent API key"
                />
                <span>
                  I copied the agent API key. ({result?.key_ttl_hours ?? 4} hour TTL — the agent can renew
                  it, and you can resume the session from Agent Activity if the agent
                  process dies.)
                </span>
              </label>
              <div className="flex flex-wrap justify-end gap-xs">
                <Button
                  variant="outline"
                  onClick={handleClose}
                  disabled={!keyAcknowledged}
                >
                  Close
                </Button>
              </div>
            </div>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default StartAssistDialog;
