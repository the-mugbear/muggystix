/**
 * ResumeAgentSessionDialog — v5.214.0
 *
 * The other button beside End on an active project session in Agent
 * Activity.  The usual reason a session is still "active" with nothing
 * happening is that the client driving it died mid-tool (an editor agent
 * session timed out while a scan ran): the session, its open phases and its
 * key are all fine — only the process holding the key is gone.
 *
 * Two paths, and the operator picks:
 *
 *  1. The client still has the key configured (MCP server entry, or the
 *     pasted prompt in a chat that can be reopened).  Nothing needs minting
 *     — reopen the client and tell the agent to resume.  The dialog states
 *     the key's expiry and the session's renewal deadline so the operator
 *     knows whether that path is still open (an expired key renews itself
 *     on the agent's first 401, while the session is under its cap).
 *  2. The key is lost, or the old client must be cut off.  Rotate: the
 *     backend mints a replacement on the SAME session (previous key
 *     revoked), and hands back the prompt with the resumed notice plus the
 *     MCP setup — rendered by the same panel the start dialog uses.
 *
 * Owner only: the key acts under the starting operator's name, so the
 * backend refuses anyone else with 403 and the page hides the button.
 */
import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, RotateCcw } from 'lucide-react';
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
import { resumeAgentSession, type AgentSessionRow } from '../services/api';
import { useProjectId } from '../hooks/useProjectId';
import { SECRET_MUTATION, invalidateReads, queryErrorText } from '../lib/query';
import { AGENT_SESSION_READS } from '../utils/agentRuns';
import { formatTimestamp } from '../utils/relativeTime';
import AgentSessionCredentials, { KeyHandoffFooter } from './AgentSessionCredentials';

export interface ResumeAgentSessionDialogProps {
  /** The active project session to resume; null keeps the dialog closed. */
  session: AgentSessionRow | null;
  onOpenChange: (next: boolean) => void;
}

const fmtTime = (iso?: string | null): string => formatTimestamp(iso);

/** The line the operator gives a reconnected agent. Short on purpose: the
 *  agent already holds the full prompt or the MCP tools; it needs to know
 *  which session and that work may be half-done. */
export const resumePromptLine = (sessionId: number): string =>
  `Resume BlueStick agent session #${sessionId}. Call GET /agent/identity ` +
  `(MCP agent_identity), read the tests this session proposed ` +
  `(host_tests_list with agent_session_id=${sessionId}) and the evidence it recorded, ` +
  `check the working directory for output a previous run left behind and upload ` +
  `anything that never landed, then continue from where the record stops rather ` +
  `than repeating work.`;

export const ResumeAgentSessionDialog: React.FC<ResumeAgentSessionDialogProps> = ({
  session,
  onOpenChange,
}) => {
  const queryClient = useQueryClient();
  const projectId = useProjectId();
  // The replacement key is shown once: it is this dialog's mutation result
  // and nothing else's — never a query, and dropped with the dialog
  // (`reset`, `gcTime: 0`).
  const rotate = useMutation({
    mutationFn: (sessionId: number) => resumeAgentSession(projectId, sessionId),
    ...SECRET_MUTATION,
    onSuccess: () => {
      // Every read of sessions is out of date (the session's page and its
      // key expiry, the list, the rail, the Operations line).  A re-read
      // that fails does not fail the rotation: the key in its answer is
      // shown once.
      void invalidateReads(queryClient, ...AGENT_SESSION_READS);
    },
  });
  const loading = rotate.isPending;
  const error = queryErrorText(rotate.error, 'Could not resume the agent session.');
  const result = rotate.data ?? null;
  const [keyCopied, setKeyCopied] = useState(false);

  const reset = () => {
    rotate.reset();
    setKeyCopied(false);
  };

  const now = Date.now();
  const keyExpiry = session?.key_expires_at ? new Date(session.key_expires_at).getTime() : null;
  const renewDeadline = session?.renewable_until ? new Date(session.renewable_until).getTime() : null;
  const keyLive = keyExpiry != null && keyExpiry > now;
  // No live key row at all means it was revoked — the only way back is a
  // rotation.  An expired-but-renewable key still works after the agent's
  // first 401 (it renews itself), so that path stays open.
  const keyRevoked = keyExpiry == null;
  const pastCap = renewDeadline != null && renewDeadline <= now;

  const handleRotate = () => {
    if (!session) return;
    rotate.mutate(session.id);
  };

  const handleClose = () => {
    reset();
    onOpenChange(false);
  };

  return (
    <Dialog
      open={session != null}
      onOpenChange={(next) => {
        if (next) {
          onOpenChange(true);
          return;
        }
        if (loading) return;
        // Same rule as the start dialog: no accidental close while the key is
        // on screen and nothing holding it was copied — Done says why.
        if (result && !keyCopied) return;
        handleClose();
      }}
    >
      <DialogContent size="xl" showClose={!result || keyCopied}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <RotateCcw className="size-5 text-primary" aria-hidden />
            Resume agent session #{session?.id ?? ''}
          </DialogTitle>
          <DialogDescription>
            The session, the tests it proposed and its audit trail continue. Only
            the agent process is gone.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-md">
          {!result ? (
            <>
              {pastCap ? (
                <Alert variant="warning">
                  <AlertDescription>
                    This session is past its maximum lifetime and can no longer be
                    renewed or resumed. End it and start a new session.
                  </AlertDescription>
                </Alert>
              ) : (
                <>
                  <div className="flex flex-col gap-xxs">
                    <p className="text-metadata font-semibold">
                      1. If the client that ran this session still has the key
                    </p>
                    <p className="text-caption text-muted-foreground">
                      {keyRevoked ? (
                        <>The previous key was revoked, so this path is closed — rotate below.</>
                      ) : keyLive ? (
                        <>
                          The key is valid until <strong>{fmtTime(session?.key_expires_at)}</strong>
                          {renewDeadline != null && (
                            <> and the agent can renew it until <strong>{fmtTime(session?.renewable_until)}</strong></>
                          )}
                          . Nothing needs minting: reopen the client (the MCP server entry or
                          the pasted prompt is unchanged) and give the agent this line:
                        </>
                      ) : (
                        <>
                          The key expired at <strong>{fmtTime(session?.key_expires_at)}</strong>,
                          but the session can still be renewed until{' '}
                          <strong>{fmtTime(session?.renewable_until)}</strong>. The agent renews
                          it on its first 401 without a new key, so reopen the client and give
                          the agent this line:
                        </>
                      )}
                    </p>
                    {!keyRevoked && session && (
                      <div className="whitespace-pre-wrap break-words rounded-control border border-border bg-accent p-sm font-mono text-caption">
                        {resumePromptLine(session.id)}
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col gap-xxs">
                    <p className="text-metadata font-semibold">
                      2. If the key is lost, or the old client must be cut off
                    </p>
                    <p className="text-caption text-muted-foreground">
                      Rotate the key. A replacement is minted on this same session and the
                      previous key stops working immediately; you get the prompt and the
                      MCP setup again, with a resumed-session notice so the agent checks
                      what was already done before continuing.
                    </p>
                  </div>
                </>
              )}
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
                  Key rotated on agent session <strong>#{result.session_id}</strong> for
                  project <strong>{result.project_name}</strong>. The previous key is
                  revoked.
                </AlertDescription>
              </Alert>
              <AgentSessionCredentials
                apiKey={result.api_key}
                instructions={result.instructions}
                mcpClients={result.mcp_clients ?? []}
                keyLabel="The replacement key on its own"
                onCopied={() => setKeyCopied(true)}
              />
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          {!result ? (
            <>
              <Button variant="outline" onClick={handleClose} disabled={loading}>
                Close
              </Button>
              {!pastCap && (
                <Button onClick={handleRotate} disabled={loading}>
                  {loading ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <RotateCcw className="size-4" aria-hidden />
                  )}
                  Rotate key and get the prompt
                </Button>
              )}
            </>
          ) : (
            <KeyHandoffFooter
              copied={keyCopied}
              onDone={handleClose}
              note={<>Valid {result.key_ttl_hours} h; renewable until {fmtTime(result.renewable_until)}.</>}
            />
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ResumeAgentSessionDialog;
