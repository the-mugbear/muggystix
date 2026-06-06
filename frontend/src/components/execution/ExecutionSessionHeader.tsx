import React from 'react';
import { ExecutionSessionSummary } from '../../services/api/test-plans';
import { Badge } from '../ui/badge';
import { Card, CardContent } from '../ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';

/** Inactivity window after which an `active` session is treated as
 *  likely interrupted rather than still running. */
export const SESSION_STALE_MINUTES = 15;
const STALE_MS = SESSION_STALE_MINUTES * 60 * 1000;

/**
 * An `active` session with no agent API activity for {@link SESSION_STALE_MINUTES}+
 * minutes is most likely interrupted (the operator's host crashed) rather
 * than still running.  Heuristic — shared by the header badge and the
 * Resume confirmation so both judge "interrupted" the same way.
 *
 * Prefers the server's ``is_stale`` boolean when present: the server
 * computes elapsed against its own clock, so client/server clock skew
 * cannot push the threshold crossing minutes off the real elapsed time.
 * The client-side fallback clamps a negative delta to 0 so a future-
 * dated timestamp (operator clock behind the server) does not silently
 * suppress the badge.
 */
export const isExecutionSessionStale = (session: ExecutionSessionSummary): boolean => {
  if (typeof session.is_stale === 'boolean') return session.is_stale;
  if (session.status !== 'active') return false;
  const ts = session.last_activity_at ?? session.started_at;
  if (!ts) return false;
  const elapsed = Math.max(0, Date.now() - new Date(ts).getTime());
  return elapsed > STALE_MS;
};

export interface ExecutionSessionHeaderProps {
  session: ExecutionSessionSummary;
  totalSessionCount?: number;
  actions?: React.ReactNode;
  title?: string;
}

export const ExecutionSessionHeader: React.FC<ExecutionSessionHeaderProps> = ({
  session,
  totalSessionCount = 1,
  actions,
  title,
}) => {
  const hasMultiple = totalSessionCount > 1;
  const resolvedTitle = title ?? (hasMultiple ? 'Execution sessions' : 'Execution session');
  const hasAttribution =
    Boolean(session.generated_by_model) || Boolean(session.generated_by_tool);

  const isStale = isExecutionSessionStale(session);

  return (
    <Card>
      <CardContent className="flex flex-col gap-sm p-sm md:flex-row md:items-center">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-xs">
            <h1 className="text-subheading font-semibold">{resolvedTitle}</h1>
            <Badge variant={session.status === 'active' ? 'success' : 'muted'}>
              {session.status}
            </Badge>
            {isStale && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="warning" className="cursor-help">
                    Possibly interrupted
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  No agent API activity for {SESSION_STALE_MINUTES}+ minutes — this run
                  may have been interrupted. Use Resume to re-issue a key and continue.
                </TooltipContent>
              </Tooltip>
            )}
            {session.mode && <Badge variant="outline">{session.mode}</Badge>}
            {hasMultiple && (
              <Badge variant="outline" className="border-primary/40 text-primary">
                {totalSessionCount} runs
              </Badge>
            )}
          </div>
          <p className="text-caption text-muted-foreground">
            Viewing #{session.id}
            {session.started_by_username && <> · by {session.started_by_username}</>}
            {session.started_at && (
              <> · started {new Date(session.started_at).toLocaleString()}</>
            )}
            {session.completed_at && (
              <> · completed {new Date(session.completed_at).toLocaleString()}</>
            )}
            {session.last_activity_at && (
              <> · last agent activity {new Date(session.last_activity_at).toLocaleString()}</>
            )}
          </p>
          {hasAttribution && (
            <p className="mt-xxs text-caption text-muted-foreground">
              Executed by <strong>{session.generated_by_model || 'unknown model'}</strong>
              {session.generated_by_tool && ` via ${session.generated_by_tool}`}
              {session.prompt_version && ` (prompt ${session.prompt_version})`}
            </p>
          )}
          {session.environment_os_family && (
            <p className="text-caption text-muted-foreground">
              Operator host: <strong>{session.environment_os_family}</strong>
              {session.environment_shell && ` (${session.environment_shell})`}
            </p>
          )}
        </div>
        {actions && <div className="flex flex-wrap gap-xs">{actions}</div>}
      </CardContent>
    </Card>
  );
};
