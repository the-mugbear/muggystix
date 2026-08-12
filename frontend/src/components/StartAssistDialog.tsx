/**
 * StartAssistDialog — v4.29.0
 *
 * Project-level dialog for starting an interactive assist agent
 * session.  Mints a read-only, project-scoped agent API key and
 * shows the agent prompt + key for the operator to paste into
 * Claude Code / Codex / etc.
 *
 * Mirrors StartReconDialog's structure but is smaller — assist
 * sessions don't have a resume affordance (the key is short-lived
 * and an operator just starts another session if needed), and they
 * bind to the project rather than to a scope, so no scope picker.
 *
 * Audit C1 (from recon dialog): the key is shown exactly once and
 * the operator must check the "I copied the key" box before the
 * dialog can be dismissed.  Key persists in sessionStorage via the
 * `result` state for the duration of the dialog so a tab reload
 * during the session recovers it.
 */
import React, { useCallback, useState } from 'react';
import { copyToClipboard } from '../utils/clipboard';
import { CheckCircle2, Copy, Loader2, MessageCircleQuestion } from 'lucide-react';
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
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { startAssistSession, type AssistSessionRow, type StartAssistResponse } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import AssistSessionsPanel from './AssistSessionsPanel';

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
  const [canWrite, setCanWrite] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StartAssistResponse | null>(null);
  const [keyAcknowledged, setKeyAcknowledged] = useState(false);
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedInstr, setCopiedInstr] = useState(false);

  const reset = useCallback(() => {
    setPurpose('');
    setCanWrite(false);
    setLoading(false);
    setError(null);
    setResult(null);
    setKeyAcknowledged(false);
    setCopiedKey(false);
    setCopiedInstr(false);
  }, []);

  const handleStart = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await startAssistSession({
        purpose: purpose.trim() || undefined,
        can_write_assigned: canWrite,
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

  // Read back what the server actually granted rather than trusting the
  // checkbox — the two can diverge (e.g. a future policy that refuses the
  // grant), and the operator needs to know which one they got.
  const grantedWrite = (result?.capabilities?.length ?? 0) > 0;

  const copyKey = async () => {
    if (!result) return;
    // copyToClipboard falls back to execCommand on http:// / non-secure
    // contexts where navigator.clipboard is unavailable; the value is also
    // visible on screen if even that fails.
    if (await copyToClipboard(result.api_key)) {
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 1500);
    }
  };
  const copyInstructions = async () => {
    if (!result) return;
    if (await copyToClipboard(result.instructions)) {
      setCopiedInstr(true);
      setTimeout(() => setCopiedInstr(false), 1500);
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
            Start AI Assist Session
          </DialogTitle>
          <DialogDescription>
            {/* v2.65.0 — TTL read from the response so it stays in
                lockstep with backend ASSIST_KEY_DEFAULT_TTL_HOURS
                and respects any AGENT_KEY_TTL_HOURS env override.
                Falls back to "4" before the response lands. */}
            Mints a project-scoped agent API key ({result?.key_ttl_hours ?? 4} h TTL) and shows
            the prompt to paste into Claude Code / Codex / Cursor. The agent
            can read host inventory, scope CIDRs, and scan summaries — it can
            never scan, create plans, or execute tests. The key is shown once;
            copy it before closing.
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
                  Use AI assist when you want to ask interactive questions
                  about the project ("which hosts expose FTP?", "summarize
                  critical findings") without committing to a full recon or
                  test-plan workflow. The agent answers from BlueStick's
                  already-ingested data and hands off to you whenever an
                  action is needed.
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
              <div className="flex flex-col gap-xxs rounded-control border border-border p-sm">
                <label className="flex items-start gap-xs">
                  <Checkbox
                    checked={canWrite}
                    onCheckedChange={(v) => setCanWrite(v === true)}
                    disabled={loading}
                    aria-label="Allow writing to hosts assigned to me"
                  />
                  <span className="flex flex-col gap-xxs">
                    <span className="text-metadata font-semibold">
                      Let the agent write to hosts assigned to me
                    </span>
                    <span className="text-caption text-muted-foreground">
                      Adds host notes and review status only, and only on hosts
                      currently assigned to you. Everything else stays read-only.
                      Notes the agent writes are attributed to you and marked
                      &ldquo;Agent&rdquo;.
                    </span>
                  </span>
                </label>
              </div>
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
                  Assist session <strong>#{result.assist_session_id}</strong>{' '}
                  started for project <strong>{result.project_name}</strong>.
                </AlertDescription>
              </Alert>
              <Alert variant={grantedWrite ? 'warning' : 'info'}>
                <AlertDescription>
                  {grantedWrite ? (
                    <>
                      This session can <strong>add notes and set review status
                      on hosts assigned to you</strong>. It cannot touch any other
                      host, scan, create plans, or execute tests. Notes it writes
                      appear under your name with an &ldquo;Agent&rdquo; badge.
                    </>
                  ) : (
                    <>
                      This session is <strong>read-only</strong>. It can query
                      project data but cannot change anything.
                    </>
                  )}
                </AlertDescription>
              </Alert>
              <div>
                <div className="mb-xxs flex items-center justify-between">
                  <p className="text-metadata font-semibold">Agent API Key (shown once)</p>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={copyKey}
                        aria-label="Copy agent API key"
                      >
                        {copiedKey ? (
                          <CheckCircle2 className="size-4 text-success" aria-hidden />
                        ) : (
                          <Copy className="size-4" aria-hidden />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{copiedKey ? 'Copied!' : 'Copy key'}</TooltipContent>
                  </Tooltip>
                </div>
                <div className="break-all rounded-control border border-border bg-accent p-sm font-mono text-caption">
                  {result.api_key}
                </div>
              </div>
              <div>
                <div className="mb-xxs flex items-center justify-between">
                  <p className="text-metadata font-semibold">Instructions</p>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={copyInstructions}
                        aria-label="Copy assist instructions"
                      >
                        {copiedInstr ? (
                          <CheckCircle2 className="size-4 text-success" aria-hidden />
                        ) : (
                          <Copy className="size-4" aria-hidden />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      {copiedInstr ? 'Copied!' : 'Copy instructions'}
                    </TooltipContent>
                  </Tooltip>
                </div>
                <div className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
                  {result.instructions}
                </div>
              </div>
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
                  I copied the agent API key. ({result?.key_ttl_hours ?? 4} hour TTL — generate another session
                  if it expires.)
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
