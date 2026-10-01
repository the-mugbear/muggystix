/**
 * The operator's own live agent sessions.
 *
 * An active session is a live agent API key sitting on someone's laptop, and
 * for a long time nothing in the UI acknowledged that it existed: the start
 * dialog minted a key and forgot it. Two consequences an operator hit
 * routinely — starting a second session without realising the first was still
 * valid (there is no one-active-session constraint), and having no way to
 * revoke a key early when they were done.
 *
 * Deliberately scoped to the current user. Colleagues' sessions are not shown:
 * reads cost nothing, rate limiting is per-agent, and where an agent writes,
 * the note itself carries the operator's name and an "Agent" badge — so a
 * session roster would tell a teammate nothing the artifacts don't already
 * say. Every session in the project is on Agent Sessions.
 *
 * 5.328.0 — reads the one session list (`GET /agent-sessions`, narrowed on the
 * server to this operator's active project sessions) and keeps the ones whose
 * key still works (`hasLiveKey`). It read `GET /assist/sessions`, a second
 * list keyed by a second id, and filtered the whole project's rows here. The
 * hook keeps its name: it is what the Start Agent Session ("assist") dialog
 * and its callers use.
 */
import { useCallback, useEffect, useState } from 'react';

import { listAgentSessions, type AgentSessionRow } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { hasLiveKey, myActiveSessionFilters } from '../utils/agentRuns';

export interface UseMyAssistSessions {
  /** Live sessions started by the current user, newest first. */
  sessions: AgentSessionRow[];
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
  const [sessions, setSessions] = useState<AgentSessionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  const refresh = useCallback(async () => {
    if (!enabled || userId == null) return;
    setLoading(true);
    try {
      const { sessions: rows } = await listAgentSessions(myActiveSessionFilters(userId));
      const now = Date.now();
      setSessions(rows.filter((s) => hasLiveKey(s, now)));
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
