/**
 * The operator's own live assist sessions.
 *
 * An active assist session is a live agent API key sitting on someone's
 * laptop, and until now nothing in the UI acknowledged that it existed: the
 * start dialog minted a key and forgot it. Two consequences an operator hit
 * routinely — starting a second session without realising the first was still
 * valid (there is no one-active-session constraint on assist, unlike
 * execution), and having no way to revoke a key early when they were done.
 *
 * Deliberately scoped to the current user. Colleagues' sessions are not shown:
 * assist reads cost nothing, rate limiting is per-agent, and where an assist
 * agent writes, the note itself carries the operator's name and an "Agent"
 * badge — so a session roster would tell a teammate nothing the artifacts
 * don't already say. Project-wide oversight is an admin concern and belongs on
 * an admin surface, not here.
 *
 * NOTE: ``GET /assist/sessions`` currently returns every session in the
 * project (Viewer-gated, metadata only, no key material), so the self-filter
 * happens here. Narrowing it server-side is tracked separately; doing it there
 * would let this hook drop the filter, not change its contract.
 */
import { useCallback, useEffect, useState } from 'react';

import { listAssistSessions, type AssistSessionRow } from '../services/api';
import { useAuth } from '../contexts/AuthContext';

export interface UseMyAssistSessions {
  /** Active sessions started by the current user, newest first. */
  sessions: AssistSessionRow[];
  loading: boolean;
  /** True when the list could not be loaded — callers render nothing rather
   *  than claiming "no active sessions", which would be a wrong answer. */
  failed: boolean;
  refresh: () => Promise<void>;
}

export const useMyAssistSessions = (
  { enabled = true }: { enabled?: boolean } = {},
): UseMyAssistSessions => {
  const { user } = useAuth();
  const userId = user?.id;
  const [sessions, setSessions] = useState<AssistSessionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const refresh = useCallback(async () => {
    if (!enabled || userId == null) return;
    setLoading(true);
    try {
      const rows = await listAssistSessions();
      setSessions(
        rows.filter((s) => s.status === 'active' && s.started_by_id === userId),
      );
      setFailed(false);
    } catch {
      // A failed lookup must not render as "you have no sessions" — that's
      // the exact wrong answer for a surface about outstanding credentials.
      setFailed(true);
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, [enabled, userId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { sessions, loading, failed, refresh };
};

export default useMyAssistSessions;
