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
import { Loader2, RotateCcw } from 'lucide-react';
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
import {
  resumeAgentSession,
  type AgentSessionRow,
  type ResumeAgentSessionResponse,
} from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import AgentSessionCredentials from './AgentSessionCredentials';

export interface ResumeAgentSessionDialogProps {
  /** The active project session to resume; null keeps the dialog closed. */
  session: AgentSessionRow | null;
  onOpenChange: (next: boolean) => void;
  /** Fired after a key rotation so the timeline re-reads key expiry. */
  onResumed?: () => void;
}

const fmtTime = (iso?: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
};

/** The line the operator gives a reconnected agent. Short on purpose: the
 *  agent already holds the full prompt or the MCP tools; it needs to know
 *  which session and that work may be half-done. */
export const resumePromptLine = (sessionId: number): string =>
  `Resume BlueStick agent session #${sessionId}. Call GET /agent/identity ` +
  `(MCP agent_identity), read open_phases, check the working directory for ` +
  `output a previous run left behind and upload anything that never landed, ` +
  `then continue from where the record stops rather than repeating work.`;

export const ResumeAgentSessionDialog: React.FC<ResumeAgentSessionDialogProps> = ({
  session,
  onOpenChange,
  onResumed,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ResumeAgentSessionResponse | null>(null);
  const [keyAcknowledged, setKeyAcknowledged] = useState(false);

  const reset = () => {
    setLoading(false);
    setError(null);
    setResult(null);
    setKeyAcknowledged(false);
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

  const handleRotate = async () => {
    if (!session) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await resumeAgentSession(session.id);
      setResult(resp);
      onResumed?.();
    } catch (err) {
      setError(formatApiError(err, 'Could not resume the agent session.'));
    } finally {
      setLoading(false);
    }
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
        // Same rule as the start dialog: a key on screen must be acknowledged
        // before the dialog can go away.
        if (result && !keyAcknowledged) return;
        handleClose();
      }}
    >
      <DialogContent size="xl" showClose={!result || keyAcknowledged}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-xs">
            <RotateCcw className="size-5 text-primary" aria-hidden />
            Resume agent session #{session?.id ?? ''}
          </DialogTitle>
          <DialogDescription>
            The session, its open phases and its audit trail continue. Only the
            agent process is gone.
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
                  {(result.active_recon_session_ids.length > 0
                    || result.active_execution_session_ids.length > 0) && (
                    <>
                      {' '}Still open:
                      {result.active_recon_session_ids.length > 0 && (
                        <> recon run{result.active_recon_session_ids.length > 1 ? 's' : ''}{' '}
                        {result.active_recon_session_ids.map((id) => `#${id}`).join(', ')}</>
                      )}
                      {result.active_execution_session_ids.length > 0 && (
                        <>{result.active_recon_session_ids.length > 0 ? ';' : ''} execution run
                        {result.active_execution_session_ids.length > 1 ? 's' : ''}{' '}
                        {result.active_execution_session_ids.map((id) => `#${id}`).join(', ')}</>
                      )}
                      . The prompt tells the agent to read their progress first.
                    </>
                  )}
                </AlertDescription>
              </Alert>
              <AgentSessionCredentials
                apiKey={result.api_key}
                instructions={result.instructions}
                mcpClients={result.mcp_clients ?? []}
                keyLabel="Replacement agent API key (shown once)"
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
            <div className="flex w-full flex-col gap-xs">
              <label className="flex items-start gap-xs text-metadata">
                <Checkbox
                  checked={keyAcknowledged}
                  onCheckedChange={(v) => setKeyAcknowledged(v === true)}
                  aria-label="I copied the replacement agent API key"
                />
                <span>
                  I copied the replacement key. ({result.key_ttl_hours} hour TTL; renewable
                  until {fmtTime(result.renewable_until)}.)
                </span>
              </label>
              <div className="flex flex-wrap justify-end gap-xs">
                <Button variant="outline" onClick={handleClose} disabled={!keyAcknowledged}>
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

export default ResumeAgentSessionDialog;
