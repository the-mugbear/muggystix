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
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { startAssistSession, type AssistSessionRow, type StartAssistResponse } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import AssistSessionsPanel from './AssistSessionsPanel';
import McpConnectPanel from './McpConnectPanel';

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
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedInstr, setCopiedInstr] = useState(false);

  const reset = useCallback(() => {
    setPurpose('');
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

  // A backend that predates the per-client MCP setup returns none; the prompt
  // tab then stands alone rather than opening on an empty tab.
  const hasMcp = (result?.mcp_clients?.length ?? 0) > 0;

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
                  Assist session <strong>#{result.assist_session_id}</strong>{' '}
                  started for project <strong>{result.project_name}</strong>.
                </AlertDescription>
              </Alert>
              <Alert variant="info">
                <AlertDescription>
                  This session acts with <strong>your permissions</strong> on
                  this project. It can query project data and make the changes
                  you can make — notes it writes appear under your name with an
                  &ldquo;Agent&rdquo; badge. It cannot create plans or execute
                  tests from here, and it cannot reach other projects.
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
              {/* Two ways to hand this session to an agent, as tabs rather
                  than stacked (v5.172.0). Stacked, the multi-KB prompt sat
                  between the operator and the MCP config, so the path we
                  recommend was the one they had to scroll past the other to
                  find. They pick one — showing both at once only makes the
                  dialog long enough to hide its own footer. */}
              <Tabs defaultValue={hasMcp ? 'mcp' : 'prompt'}>
                <TabsList className="mb-xs">
                  {hasMcp && <TabsTrigger value="mcp">Connect via MCP</TabsTrigger>}
                  <TabsTrigger value="prompt">Paste the prompt</TabsTrigger>
                </TabsList>
                {hasMcp && (
                  <TabsContent value="mcp">
                    <McpConnectPanel
                      clients={result.mcp_clients ?? []}
                      blurb={
                        'The tools appear natively in your client, and the read tools can be ' +
                        'marked “always allow” so queries run without a prompt. Each client ' +
                        'wants a different shape, so pick yours:'
                      }
                    />
                  </TabsContent>
                )}
                <TabsContent value="prompt">
                  <div className="mb-xxs flex items-center justify-between">
                    <p className="text-metadata text-muted-foreground">
                      Paste this into a terminal agent — it drives the same session with curl.
                    </p>
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
                </TabsContent>
              </Tabs>
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
