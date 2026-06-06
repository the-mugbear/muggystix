import { useCallback, useEffect, useState } from 'react';

import {
  resumeReconSession,
  startReconSession,
  StartReconResponse,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';

// sessionStorage namespace.  Survives reloads / tab restore but is
// wiped when the tab closes (which is the right TTL — the key is
// time-limited on the backend anyway, ~24h).
const RECON_KEY_STORAGE_PREFIX = 'recon_session_key_';

const persistResult = (result: StartReconResponse) => {
  try {
    sessionStorage.setItem(
      `${RECON_KEY_STORAGE_PREFIX}${result.recon_session_id}`,
      JSON.stringify(result),
    );
  } catch {
    // sessionStorage quota / private browsing — non-fatal
  }
};

const clearPersistedResult = (recon_session_id: number) => {
  try {
    sessionStorage.removeItem(`${RECON_KEY_STORAGE_PREFIX}${recon_session_id}`);
  } catch { /* ignore */ }
};

export const readPersistedReconResult = (
  recon_session_id: number,
): StartReconResponse | null => {
  try {
    const raw = sessionStorage.getItem(`${RECON_KEY_STORAGE_PREFIX}${recon_session_id}`);
    return raw ? (JSON.parse(raw) as StartReconResponse) : null;
  } catch {
    return null;
  }
};

/**
 * Encapsulates the "agentic recon" dialog lifecycle: picking a scope,
 * starting a ReconSession, tracking loading/error/result, and the two
 * copy-to-clipboard helpers for the API key and instructions blocks.
 *
 * v2.11.0 — renamed from useReconPlan semantically: this no longer
 * creates a test plan.  It starts a recon *session* that uploads
 * scanner output to BlueStick's ingestion pipeline.  Test plan
 * generation is a separate workflow the user triggers later.  File
 * name kept as useReconPlan.ts to avoid a bigger rename diff — the
 * exported hook is still used from the same places.
 */
export function useReconPlan() {
  const toast = useToast();
  const [scopeId, setScopeId] = useState<number | null>(null);
  const [scopeName, setScopeName] = useState('');
  // Non-null when the dialog is in resume mode (re-mint a key for an
  // existing recon session) rather than start mode (create a new one).
  // The `start` action branches on this; nothing else needs to.
  const [resumeSessionId, setResumeSessionId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<StartReconResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedInstr, setCopiedInstr] = useState(false);
  // The user must explicitly confirm they copied the key before the
  // dialog can be dismissed; this prevents the accidental click-
  // outside that previously destroyed the key forever (audit C1).
  const [keyAcknowledged, setKeyAcknowledged] = useState(false);

  const openFor = useCallback((id: number, name: string) => {
    setScopeId(id);
    setScopeName(name);
    setResumeSessionId(null);
    setResult(null);
    setError(null);
    setKeyAcknowledged(false);
  }, []);

  /** Open the dialog in resume mode for an interrupted recon session.
   *  `start()` will call the resume endpoint (re-minting a key for the
   *  same session and revoking the old one) instead of /recon/start. */
  const openForResume = useCallback(
    (id: number, name: string, sessionId: number) => {
      setScopeId(id);
      setScopeName(name);
      setResumeSessionId(sessionId);
      setResult(null);
      setError(null);
      setKeyAcknowledged(false);
    },
    [],
  );

  // Persist any successful result to sessionStorage so a reload mid-
  // session doesn't wipe the key.  The key is time-limited on the
  // backend so we don't need indefinite persistence — sessionStorage
  // (per-tab, wiped on close) is the right TTL.
  useEffect(() => {
    if (result) persistResult(result);
  }, [result]);

  const reset = useCallback(() => {
    // Don't clear sessionStorage here — the user may dismiss the
    // dialog but still want to recover the key from another tab /
    // after a reload.  Storage is cleared only when the user
    // explicitly acknowledges they copied the key.
    setScopeId(null);
    setScopeName('');
    setResumeSessionId(null);
    setResult(null);
    setError(null);
    setLoading(false);
    setCopiedKey(false);
    setCopiedInstr(false);
    setKeyAcknowledged(false);
  }, []);

  const acknowledgeAndReset = useCallback(() => {
    if (result) clearPersistedResult(result.recon_session_id);
    reset();
  }, [result, reset]);

  const start = useCallback(async () => {
    if (scopeId == null) return;
    setLoading(true);
    setError(null);
    try {
      const data =
        resumeSessionId != null
          ? await resumeReconSession(scopeId, resumeSessionId)
          : await startReconSession(scopeId);
      setResult(data);
      toast.success(
        resumeSessionId != null
          ? `Recon session #${resumeSessionId} resumed — fresh agent key minted.`
          : 'Recon session started — agent key created.',
      );
    } catch (err: unknown) {
      setError(
        formatApiError(
          err,
          resumeSessionId != null
            ? 'Failed to resume agentic recon.'
            : 'Failed to start agentic recon.',
        ),
      );
    } finally {
      setLoading(false);
    }
  }, [scopeId, resumeSessionId, toast]);

  const copyKey = useCallback(() => {
    if (!result?.api_key) return;
    navigator.clipboard.writeText(result.api_key).then(
      () => { setCopiedKey(true); setTimeout(() => setCopiedKey(false), 1500); },
      () => { toast.warning('Could not copy.'); },
    );
  }, [result, toast]);

  const copyInstructions = useCallback(() => {
    if (!result?.instructions) return;
    navigator.clipboard.writeText(result.instructions).then(
      () => { setCopiedInstr(true); setTimeout(() => setCopiedInstr(false), 1500); },
      () => { toast.warning('Could not copy.'); },
    );
  }, [result, toast]);

  return {
    scopeId,
    scopeName,
    /** Null in start mode, the session id in resume mode.  Consumers
     *  use it to vary copy and treat the action as destructive (the
     *  resume endpoint revokes the prior key). */
    resumeSessionId,
    loading,
    result,
    error,
    copiedKey,
    copiedInstr,
    keyAcknowledged,
    setKeyAcknowledged,
    openFor,
    openForResume,
    reset,
    acknowledgeAndReset,
    start,
    copyKey,
    copyInstructions,
  };
}
