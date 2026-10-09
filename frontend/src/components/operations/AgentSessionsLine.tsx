/**
 * Agent sessions on Operations: ONE line (5.329.0, design review 2026-10-02),
 * about the READER'S OWN sessions (5.330.0 — Operations is the reader's page;
 * the line counted every teammate's).
 *
 * It was a "Runs" section — a status filter offering "Completed" and "Failed"
 * (sessions have neither), an Everyone / Mine switch and ten rows showing the
 * STORED status, so a session whose key ran out read "active" here and
 * "Resumable" on Agent Sessions.  Runs themselves went in 2.442.0.
 *
 * Now: Agent Sessions' own sentence (`liveSessionsSummary`), over the rows
 * the server narrows to this operator (`myActiveSessionFilters`), as a link
 * to that page — where every session of the project is listed.  A failed read
 * says so — it is never "no session is live".
 */
import React, { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Bot } from 'lucide-react';

import { listAgentSessions } from '../../services/api';
import { useAuth } from '../../contexts/AuthContext';
import { useProjectId } from '../../hooks/useProjectId';
import { SESSIONS_LIST_PATH, liveSessionsSummary, myActiveSessionFilters } from '../../utils/agentRuns';
import { cn } from '../../utils/cn';

/** How long an answer another reader on the page just got is used as it is. */
const FRESH_FOR_MS = 30_000;

const AgentSessionsLine: React.FC = () => {
  const { user } = useAuth();
  const userId = user?.id;
  const projectId = useProjectId();
  const sessions = useQuery({
    queryKey: ['listAgentSessions', projectId, userId == null ? null : myActiveSessionFilters(userId)],
    queryFn: ({ signal }) => listAgentSessions(projectId, myActiveSessionFilters(userId as number), { signal }),
    // Without the reader's id there is no "mine" to ask for — and the
    // project-wide list is not this page's to show.
    enabled: userId != null,
    // This query's own lifecycle (lib/query): the page already holds this
    // read — `useMyAssistSessions`, the same key, mounted with Operations —
    // and the line appears a moment later, once the page knows it has hosts.
    // An answer that fresh is not asked for a second time because the line
    // mounted; Refresh, and a session started, ended or resumed, still ask.
    staleTime: FRESH_FOR_MS,
  });
  const rows = sessions.data?.sessions;
  // The sentence is as of the read it was made from (`dataUpdatedAt`).
  const summary = useMemo(
    () => (rows ? liveSessionsSummary(rows, sessions.dataUpdatedAt, { mine: true }) : null),
    [rows, sessions.dataUpdatedAt],
  );
  const text = summary?.text ?? null;
  const waiting = summary?.waiting ?? false;
  const failed = sessions.isError;

  if (text == null && !failed) return null;
  return (
    <p className="flex min-w-0 flex-wrap items-center gap-x-xs gap-y-xxs text-metadata">
      <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="font-semibold text-foreground">Your agent sessions</span>
      {failed && text == null ? (
        <span role="status" className="text-muted-foreground">
          could not be checked — this is not a confirmation that none is live.{' '}
          <Link to={SESSIONS_LIST_PATH} className="text-info hover:underline">Open Agent Sessions</Link>
        </span>
      ) : (
        <Link
          to={SESSIONS_LIST_PATH}
          className={cn(
            'min-w-0 rounded hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring',
            waiting ? 'text-warning' : 'text-info',
          )}
        >
          {text}
        </Link>
      )}
    </p>
  );
};

export default AgentSessionsLine;
