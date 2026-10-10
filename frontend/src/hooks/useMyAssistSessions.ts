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
 *
 * The read is `listAgentSessions`: starting, ending or resuming a session
 * invalidates that name, so there is no `refresh` to call.
 */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { listAgentSessions, type AgentSessionRow } from '../services/api';
import { useAuth } from '../contexts/AuthContext';
import { hasLiveKey, myActiveSessionFilters } from '../utils/agentRuns';
import { useProjectId } from './useProjectId';

const NONE: AgentSessionRow[] = [];

/** THE read of an operator's own active sessions: its key, and how it is
 *  asked.  One definition, so a read made at a click (`useAgentTask`) and the
 *  list a dialog shows are the same cache entry — a Start, End or Resume
 *  reaches both. */
export const myAssistSessionsRead = (projectId: number, userId: number) => ({
  queryKey: ['listAgentSessions', projectId, myActiveSessionFilters(userId)] as const,
  queryFn: ({ signal }: { signal?: AbortSignal }) =>
    listAgentSessions(projectId, myActiveSessionFilters(userId), { signal }),
});

export interface UseMyAssistSessions {
  /** Live sessions started by the current user, newest first. */
  sessions: AgentSessionRow[];
  loading: boolean;
  /** True when the list could not be loaded — callers render nothing rather
   *  than claiming "no active sessions", which would be a wrong answer. */
  failed: boolean;
}

export const useMyAssistSessions = (
  { enabled = true }: { enabled?: boolean } = {},
): UseMyAssistSessions => {
  const { user } = useAuth();
  const userId = user?.id;
  const on = enabled && userId != null;
  const projectId = useProjectId();
  const query = useQuery({
    // (No user: never asked — `on` is false; the key only has to be one.)
    ...myAssistSessionsRead(projectId, userId ?? 0),
    enabled: on,
  });

  // A failed lookup must not render as "you have no sessions" — that's the
  // exact wrong answer for a surface about outstanding credentials.  It does
  // not go on showing the sessions of the last good read either.
  const failed = query.isError;
  const rows = query.data?.sessions;
  // "Live" as of the read: a key that runs out later is found by the next one.
  const readAt = query.dataUpdatedAt;
  const sessions = useMemo(
    () => (rows && !failed ? rows.filter((s) => hasLiveKey(s, readAt)) : NONE),
    [rows, failed, readAt],
  );

  return { sessions, loading: query.isFetching, failed };
};

export default useMyAssistSessions;
