/**
 * One agent session — what it was for, the authority it acted with, and what it
 * produced (notes, API calls).
 * (Titled "AI Assist Sessions" until the screenshot review of 2026-09-23: since
 * v2.337.0 one session does every kind of agent work. The route and API keep
 * the old `assist` name.)
 *
 * v5.173.0 gave assist a list and a detail page. v5.294.0 (UX review) — the
 * list is now the "Sessions" view of Agent Runs (`/agent-activity?view=sessions`,
 * components/agent-sessions/AgentSessionsList): the two pages listed the same
 * sessions in two formats with two sets of controls. A bare `/assist-sessions`
 * lands there; the detail keeps its route, since notes and API calls link to it.
 * Notes come first in the detail because they are the session's only durable
 * output — the API feed below them is the read trail.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Loader2, StickyNote } from 'lucide-react';

import { AssistSessionDetail, getAssistSession } from '../services/api';
import AgentActivityLog from '../components/AgentActivityLog';
import {
  AuthorityBadge,
  SessionStatusBadge,
  operatorName,
} from '../components/agent-sessions/AgentSessionsList';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { formatApiError } from '../utils/apiErrors';
import { formatTimestamp } from '../utils/relativeTime';
import { safeFallback } from '../utils/uiStyles';

/** Where the session list lives now. */
export const SESSIONS_VIEW_PATH = '/agent-activity?view=sessions';

/** How long the session ran, which is the shape of the work rather than a
 *  timestamp pair the reader has to subtract. */
const formatDuration = (from: string | null, to: string | null): string => {
  if (!from) return '—';
  const start = new Date(from).getTime();
  const end = to ? new Date(to).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—';
  const mins = Math.round((end - start) / 60_000);
  if (mins < 1) return 'under a minute';
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${hours}h ${rem}m` : `${hours}h`;
};

// ---------------------------------------------------------------------------
// Detail
// ---------------------------------------------------------------------------

const SessionDetail: React.FC<{ sessionId: number }> = ({ sessionId }) => {
  const [session, setSession] = useState<AssistSessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSession(await getAssistSession(sessionId));
      setError(null);
    } catch (e) {
      setError(formatApiError(e, 'Could not load this agent session.'));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !session) {
    return (
      <p className="flex items-center gap-sm text-metadata text-muted-foreground" role="status">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        Loading session…
      </p>
    );
  }
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!session) return null;

  const env = (session.environment ?? {}) as Record<string, unknown>;
  const envLine = [env.os_family, env.os_release, env.shell]
    .filter((v) => typeof v === 'string' && v)
    .join(' · ');

  return (
    <div className="flex flex-col gap-md">
      {/* Sections, not cards (§7): the summary sits on the page over a rule. */}
      <section className="flex flex-col gap-sm border-b border-border pb-md">
        <div className="flex flex-wrap items-center gap-xs">
          <SessionStatusBadge status={session.status} />
          <AuthorityBadge role={session.operator_role} operator={operatorName(session)} />
          {/* v5.203.0 — from observed calls, not the environment probe; the
              probe is optional and proves nothing about the transport. */}
          {session.connection === 'none' ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="outline" tabIndex={0}>
                  Never connected
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm">
                No authenticated call ever reached this session — the key was minted
                and no client used it, so there is nothing to review here.
              </TooltipContent>
            </Tooltip>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="outline" tabIndex={0}>
                  {session.connection === 'mcp' ? 'MCP verified' : 'Connected via curl'}
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm">
                {session.connection === 'mcp'
                  ? 'At least one authenticated tool call arrived through the MCP transport.'
                  : 'Calls arrived by direct HTTP (the pasted-prompt path); no MCP client used this key.'}
              </TooltipContent>
            </Tooltip>
          )}
        </div>
        <p className="break-words text-metadata text-foreground">
          {safeFallback(session.purpose, 'No stated purpose')}
        </p>
        <dl className="grid grid-cols-2 gap-x-md gap-y-xs text-caption md:grid-cols-4">
          <div className="min-w-0">
            <dt className="text-muted-foreground">Started by</dt>
            <dd className="truncate text-foreground" title={session.started_by_username ?? undefined}>
              {safeFallback(operatorName(session), 'unknown')}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-muted-foreground">Started</dt>
            <dd className="truncate text-foreground">{formatTimestamp(session.started_at)}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-muted-foreground">Ran for</dt>
            <dd className="truncate text-foreground">
              {formatDuration(session.started_at, session.ended_at)}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-muted-foreground">Operator&rsquo;s machine</dt>
            <dd className="truncate text-foreground" title={envLine || undefined}>
              {envLine || '—'}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-muted-foreground">Agent</dt>
            <dd className="truncate text-foreground">
              {[session.agent_model, session.agent_tool].filter(Boolean).join(' · ') || '—'}
            </dd>
          </div>
          <div className="min-w-0">
            <dt className="text-muted-foreground">Prompt version</dt>
            <dd className="truncate text-foreground">{safeFallback(session.prompt_version, '—')}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-muted-foreground">API calls</dt>
            <dd className="text-foreground">{session.call_count}</dd>
          </div>
          <div className="min-w-0">
            <dt className="text-muted-foreground">Feedback left</dt>
            <dd className="text-foreground">{session.feedback_count}</dd>
          </div>
        </dl>
      </section>

      <div>
        <div className="mb-xs flex items-center gap-xs">
          <StickyNote className="size-4 text-primary" aria-hidden />
          <h2 className="text-section-title">Notes written</h2>
          <Badge variant="secondary">{session.note_count}</Badge>
        </div>
        <p className="mb-xs max-w-4xl text-caption text-muted-foreground">
          The session&rsquo;s durable output — everything else it did was a read.
          These appear on the host under the operator&rsquo;s name with an agent
          badge, so this is the answer to &ldquo;what did it put my name on?&rdquo;
        </p>
        {session.notes.length === 0 ? (
          <p className="text-metadata text-muted-foreground">This session wrote no notes.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[18%]">Host</TableHead>
                <TableHead className="w-[54%]">Note</TableHead>
                <TableHead className="w-[12%]">Status</TableHead>
                <TableHead className="w-[16%]">Written</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {session.notes.map((note) => (
                <TableRow key={note.id}>
                  <TableCell className="align-top">
                    {note.host_id ? (
                      <Link
                        to={`/hosts/${note.host_id}#note-${note.id}`}
                        className="block truncate text-primary underline-offset-4 hover:underline"
                        title={note.hostname || note.host_ip || undefined}
                      >
                        {note.hostname || note.host_ip || `Host #${note.host_id}`}
                      </Link>
                    ) : (
                      <span className="text-caption text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="align-top">
                    <span className="line-clamp-3 break-words text-caption text-foreground">
                      {note.body}
                    </span>
                  </TableCell>
                  <TableCell className="align-top">
                    <Badge variant="outline">{safeFallback(note.status, 'open')}</Badge>
                  </TableCell>
                  <TableCell className="align-top text-caption text-muted-foreground">
                    {formatTimestamp(note.created_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {session.note_count > session.notes.length && (
          <p className="mt-xxs text-caption text-muted-foreground">
            Showing the {session.notes.length} most recent of {session.note_count}.
          </p>
        )}
      </div>

      <AgentActivityLog
        source={{ kind: 'assist', assistSessionId: session.id }}
        title="API activity"
        subtitle="Every request this session's agent made, in order. Filter by host or IP to answer 'did it look at the right things?'"
        defaultMineOnly={false}
      />
    </div>
  );
};

const AssistSessions: React.FC = () => {
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const selectedId = sessionId ? Number(sessionId) : null;

  if (selectedId == null || !Number.isFinite(selectedId)) {
    return <Navigate to={SESSIONS_VIEW_PATH} replace />;
  }

  return (
    <div className="p-md md:p-lg">
      <Button
        variant="ghost"
        size="sm"
        className="mb-xs px-0"
        onClick={() => navigate(SESSIONS_VIEW_PATH)}
      >
        <ArrowLeft className="size-4" aria-hidden />
        All agent sessions
      </Button>
      <h1 className="mb-md text-page-title">Agent session #{selectedId}</h1>
      <SessionDetail sessionId={selectedId} />
    </div>
  );
};

export default AssistSessions;
