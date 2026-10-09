/**
 * `/assist-sessions/:id` — the old address of a session's review page.
 *
 * Until 2.449.0 / 5.328.0 a session had a second id (its `assist_sessions`
 * row: detail #52 = session #72) and this path was keyed by it. A session has
 * one id now and one page, `/agent-sessions/:sessionId`. Notes, feedback rows
 * and bookmarks from before still carry the old id, so this asks the server
 * which session had it and redirects. A session started since has no such
 * id; a bare `/assist-sessions` goes to the list.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, useParams } from 'react-router-dom';
import { ArrowLeft, Loader2 } from 'lucide-react';

import { getAgentSessionByLegacyAssistId } from '../services/api';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { SESSIONS_LIST_PATH, agentSessionPath } from '../utils/agentRuns';

const AssistSessions: React.FC = () => {
  const { sessionId } = useParams<{ sessionId?: string }>();
  const legacyId = sessionId ? Number(sessionId) : null;
  const known = legacyId != null && Number.isFinite(legacyId);
  const projectId = useProjectId();
  const session = useQuery({
    queryKey: ['getAgentSessionByLegacyAssistId', projectId, legacyId],
    queryFn: ({ signal }) => getAgentSessionByLegacyAssistId(projectId, legacyId as number, signal),
    enabled: known,
  });
  const error = queryErrorText(
    session.error,
    'No agent session has this older link’s number. Find it under Agent Sessions.',
  );

  if (!known) {
    return <Navigate to={SESSIONS_LIST_PATH} replace />;
  }
  if (session.data) return <Navigate to={agentSessionPath(session.data.id)} replace />;

  return (
    <div className="p-md md:p-lg">
      <Button variant="ghost" size="sm" className="mb-xs px-0" asChild>
        <Link to={SESSIONS_LIST_PATH}>
          <ArrowLeft className="size-4" aria-hidden />
          All agent sessions
        </Link>
      </Button>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : (
        <p className="flex items-center gap-sm text-metadata text-muted-foreground" role="status">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Opening session…
        </p>
      )}
    </div>
  );
};

export default AssistSessions;
