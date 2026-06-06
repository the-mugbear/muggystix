/**
 * Shared "Start Agentic Reconnaissance" dialog.
 *
 * Extracted from Scopes.tsx so ReconRunsList (which now offers a top-
 * level "Start Recon" affordance) can reuse the exact same UI without
 * duplicating ~180 lines of dialog markup. State lives in the
 * `useReconPlan` hook the parent already owns; this component is a
 * pure renderer.
 *
 * Open/close behaviour:
 *   - Open while `recon.scopeId != null`.
 *   - Close attempts are blocked while loading.
 *   - Close attempts after a result is shown are blocked until the
 *     user acknowledges they copied the API key (audit C1). The
 *     in-footer Close + "Open Recon Run" buttons are also disabled
 *     until acknowledged. DialogBody scrolls so neither button gets
 *     pushed off-screen by the multi-KB instructions + InAppAgentPanel.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2, Copy, Loader2, Rocket, RotateCcw } from 'lucide-react';
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
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import InAppAgentPanel from './InAppAgentPanel';
import type { useReconPlan } from '../hooks/useReconPlan';

export interface StartReconDialogProps {
  recon: ReturnType<typeof useReconPlan>;
}

export const StartReconDialog: React.FC<StartReconDialogProps> = ({ recon }) => {
  const navigate = useNavigate();
  const isResume = recon.resumeSessionId != null;

  return (
    <Dialog
      open={recon.scopeId !== null}
      onOpenChange={(next) => {
        if (next) return;
        if (recon.loading) return;
        // If the agent key is on screen, only allow dismissal once the
        // user has explicitly acknowledged they copied it (audit C1).
        // The key persists in sessionStorage for the current tab, so a
        // reload during the session recovers it.
        if (recon.result && !recon.keyAcknowledged) return;
        if (recon.result) {
          recon.acknowledgeAndReset();
        } else {
          recon.reset();
        }
      }}
    >
      {/* v2.44.1 (UX review #1): showClose={false} because once the
          agent key is on screen the onOpenChange handler vetoes close
          until the user acknowledges they stored it.  Rendering the X
          unconditionally would advertise "dismissible" behavior the
          dialog then silently refuses — confusing, reads as broken.
          The footer carries an explicit "I copied the key" affordance. */}
      <DialogContent size="xl" showClose={!recon.result || recon.keyAcknowledged}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            {isResume ? (
              <RotateCcw className="size-5 text-primary" aria-hidden />
            ) : (
              <Rocket className="size-5 text-primary" aria-hidden />
            )}
            {isResume
              ? `Resume Recon Session #${recon.resumeSessionId} — ${recon.scopeName}`
              : `Start Agentic Reconnaissance — ${recon.scopeName}`}
          </DialogTitle>
          <DialogDescription>
            {/* v2.65.0 — read TTL from the response (StartReconResponse
                .key_ttl_hours) so it reflects any AGENT_KEY_TTL_HOURS
                override and stays in lockstep automatically.  Fall
                back to "24" before the response lands so the dialog
                copy doesn't flash empty. */}
            {isResume
              ? `Re-mints a fresh agent key (${recon.result?.key_ttl_hours ?? 24} h TTL) for this interrupted recon session. Prior uploads are preserved; the current key is revoked.`
              : `Mints a scope-bound agent key (${recon.result?.key_ttl_hours ?? 24} h TTL) and shows the recon prompt to copy into your terminal-side agent (Claude Code, Codex, Cursor). The key is shown once — confirm you copied it before closing the dialog.`}
          </DialogDescription>
        </DialogHeader>
        {/* DialogBody so the multi-KB instructions + InAppAgentPanel
            scroll inside the dialog frame instead of pushing the
            footer (with the acknowledgement checkbox + Close button)
            off-screen via DialogContent's overflow-hidden clip. */}
        <DialogBody className="flex flex-col gap-md">
          {!recon.result ? (
            <>
              {isResume ? (
                <Alert variant="warning">
                  <AlertDescription>
                    Resuming will mint a new agent key and <strong>revoke the current
                    one</strong>. Any agent still running on this session will be cut off
                    when it next calls in. Prior uploads (hosts, ports, scans) are
                    preserved — the new agent picks up from <code>/recon/summary</code>
                    and <code>/recon/context</code>. Continue only if you know the session
                    is interrupted (the badge alone is a heuristic, not a guarantee).
                  </AlertDescription>
                </Alert>
              ) : (
                <>
                  <p className="text-metadata text-muted-foreground">
                    Starts an agent-driven reconnaissance session against this scope and mints a
                    time-limited agent API key. The agent runs scanner tools locally (nmap, masscan,
                    etc.) against the subnets in the scope, submits the raw output to BlueStick for
                    parsing, and iterates until the scope is well-characterized. The populated host
                    data appears in the Hosts and Scans pages. Test plan generation is a separate step
                    you trigger after reviewing the recon results.
                  </p>
                  <Alert variant="info">
                    <AlertDescription>
                      The agent will only operate within the subnets registered on this scope and will
                      request approval before every command it runs. You will need to paste the
                      resulting instructions + API key to your terminal agent (Claude Code, Codex,
                      etc.).
                    </AlertDescription>
                  </Alert>
                </>
              )}
              {recon.error && (
                <Alert variant="destructive">
                  <AlertDescription>{recon.error}</AlertDescription>
                </Alert>
              )}
            </>
          ) : (
            <div className="flex flex-col gap-sm">
              <Alert variant="success">
                <AlertDescription>
                  Recon session <strong>#{recon.result.recon_session_id}</strong>{' '}
                  {isResume ? 'resumed' : 'started'} for scope{' '}
                  <strong>{recon.result.scope_name}</strong>.
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
                        onClick={recon.copyKey}
                        aria-label="Copy agent API key"
                      >
                        {recon.copiedKey ? (
                          <CheckCircle2 className="size-4 text-success" aria-hidden />
                        ) : (
                          <Copy className="size-4" aria-hidden />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{recon.copiedKey ? 'Copied!' : 'Copy key'}</TooltipContent>
                  </Tooltip>
                </div>
                <div className="break-all rounded-control border border-border bg-accent p-sm font-mono text-caption">
                  {recon.result.api_key}
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
                        onClick={recon.copyInstructions}
                        aria-label="Copy recon instructions"
                      >
                        {recon.copiedInstr ? (
                          <CheckCircle2 className="size-4 text-success" aria-hidden />
                        ) : (
                          <Copy className="size-4" aria-hidden />
                        )}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      {recon.copiedInstr ? 'Copied!' : 'Copy instructions'}
                    </TooltipContent>
                  </Tooltip>
                </div>
                <div className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
                  {recon.result.instructions}
                </div>
              </div>
              <div>
                <p className="mb-xxs text-metadata font-semibold">Run with In-App Agent</p>
                <InAppAgentPanel
                  prompt={recon.result.instructions}
                  contextLabel={`reconnaissance of ${recon.scopeName}`}
                />
              </div>
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          {!recon.result ? (
            <>
              <Button variant="outline" onClick={recon.reset} disabled={recon.loading}>
                Cancel
              </Button>
              <Button
                onClick={recon.start}
                disabled={recon.loading}
                variant={isResume ? 'destructive' : 'default'}
              >
                {recon.loading ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : isResume ? (
                  <RotateCcw className="size-4" aria-hidden />
                ) : (
                  <Rocket className="size-4" aria-hidden />
                )}
                {isResume ? 'Resume session' : 'Start Recon'}
              </Button>
            </>
          ) : (
            <div className="flex w-full flex-col gap-xs">
              {/* Audit C1: explicit acknowledgement gate before the key
                  dialog can be closed. Mirrors Stripe's one-time-key
                  UX — the key is held in sessionStorage so a reload
                  still recovers it, but the user has to actively click
                  the checkbox to confirm before Close/View becomes
                  enabled. */}
              <label className="flex items-start gap-xs text-metadata">
                <Checkbox
                  checked={recon.keyAcknowledged}
                  onCheckedChange={(v) => recon.setKeyAcknowledged(v === true)}
                  aria-label="I copied the agent API key"
                />
                <span>
                  I copied the agent API key. (It is held in this tab's session storage until
                  close — a reload during this session will recover it.)
                </span>
              </label>
              <div className="flex flex-wrap justify-end gap-xs">
                <Button
                  variant="outline"
                  onClick={recon.acknowledgeAndReset}
                  disabled={!recon.keyAcknowledged}
                >
                  Close
                </Button>
                <Button
                  onClick={() => {
                    const sid = recon.result?.recon_session_id;
                    recon.acknowledgeAndReset();
                    if (sid) navigate(`/recon/runs/${sid}`);
                  }}
                  disabled={!recon.keyAcknowledged}
                >
                  Open Recon Run
                </Button>
              </div>
            </div>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default StartReconDialog;
