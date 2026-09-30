/**
 * `/assist-sessions/:id` — the old address of a session's review page, keyed
 * by its detail row's id (a second id sequence: detail #52 = session #72).
 *
 * v5.312.0 — the session has one page, `/agent-sessions/:sessionId`, keyed by
 * the session id everything else uses. Notes, feedback and older bookmarks
 * still link here, so this resolves the detail row to its session and
 * redirects. A bare `/assist-sessions` goes to the list.
 */
import React, { useEffect, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { ArrowLeft, Loader2 } from 'lucide-react';

import { getAssistSession } from '../services/api';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Button } from '../components/ui/button';
import { formatApiError } from '../utils/apiErrors';
import { SESSIONS_LIST_PATH, agentSessionPath } from '../utils/agentRuns';

const AssistSessions: React.FC = () => {
  const { sessionId } = useParams<{ sessionId?: string }>();
  const detailId = sessionId ? Number(sessionId) : null;
  const [target, setTarget] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (detailId == null || !Number.isFinite(detailId)) return;
    let cancelled = false;
    getAssistSession(detailId)
      .then((detail) => {
        if (cancelled) return;
        if (detail.agent_session_id != null) setTarget(agentSessionPath(detail.agent_session_id));
        else setError('This session predates the unified sessions and has no session page.');
      })
      .catch((e) => {
        if (!cancelled) setError(formatApiError(e, 'Could not find this agent session.'));
      });
    return () => { cancelled = true; };
  }, [detailId]);

  if (detailId == null || !Number.isFinite(detailId)) {
    return <Navigate to={SESSIONS_LIST_PATH} replace />;
  }
  if (target) return <Navigate to={target} replace />;

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
