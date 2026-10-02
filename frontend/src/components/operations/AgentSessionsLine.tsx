/**
 * Agent sessions on Operations: ONE line (5.329.0, design review 2026-10-02).
 *
 * It was a "Runs" section — a status filter offering "Completed" and "Failed"
 * (sessions have neither), an Everyone / Mine switch and ten rows showing the
 * STORED status, so a session whose key ran out read "active" here and
 * "Resumable" on Agent Sessions.  Runs themselves went in 2.442.0.
 *
 * Now: the sentence Agent Sessions leads with (`liveSessionsSummary`), from
 * the same request, as a link to that page.  A failed read says so — it is
 * never "no session is live".
 */
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bot } from 'lucide-react';

import { listAgentSessions } from '../../services/api';
import { LIVE_SESSION_FILTERS, SESSIONS_LIST_PATH, liveSessionsSummary } from '../../utils/agentRuns';
import { cn } from '../../utils/cn';

const AgentSessionsLine: React.FC<{ refreshKey?: number }> = ({ refreshKey = 0 }) => {
  const [text, setText] = useState<string | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    listAgentSessions(LIVE_SESSION_FILTERS, { signal: controller.signal })
      .then((resp) => {
        if (controller.signal.aborted) return;
        const summary = liveSessionsSummary(resp.sessions);
        setText(summary.text);
        setWaiting(summary.waiting);
        setFailed(false);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setFailed(true);
      });
    return () => controller.abort();
  }, [refreshKey]);

  if (text == null && !failed) return null;
  return (
    <p className="flex min-w-0 flex-wrap items-center gap-x-xs gap-y-xxs text-metadata">
      <Bot className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      <span className="font-semibold text-foreground">Agent sessions</span>
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
