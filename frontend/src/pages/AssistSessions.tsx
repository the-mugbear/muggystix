/**
 * Agent Sessions — the review surface for project-scoped agent sessions.
 * (Titled "AI Assist Sessions" until the screenshot review of 2026-09-23: since
 * v2.337.0 one session does every kind of agent work, and the nav already said
 * "Agent Sessions". The route and API keep the old `assist` name.)
 *
 * v5.173.0. Recon runs and test plans each have a list and a detail page;
 * assist had a start dialog and nothing else. That is backwards for the one
 * workflow an operator runs conversationally — and the only one that can write
 * notes under their own name. Every call was already being audited; nothing
 * read them back, so "what did that agent actually do for me?" was unanswerable
 * once the dialog closed.
 *
 * Two views in one route: the list (which sessions exist, and which did
 * anything) and the detail (what one session produced). Notes come first in the
 * detail because they are the session's only durable output — the API feed
 * below them is the read trail.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Bot,
  KeyRound,
  Loader2,
  MessageCircleQuestion,
  RefreshCw,
  StickyNote,
} from 'lucide-react';

import {
  AssistSessionDetail,
  AssistSessionRow,
  getAssistSession,
  listAssistSessions,
} from '../services/api';
import AgentActivityLog from '../components/AgentActivityLog';
import PostureEmpty from '../components/posture/PostureEmpty';
import { NavigableTableCell, NavigableTableRow } from '../components/NavigableTableRow';
import { TableSkeleton } from '../components/PageSkeleton';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';

type StatusFilter = '' | 'active' | 'ended';

/** One page. Matches the API's own default; the page appends rather than
 *  raising it, so a project with thousands of sessions stays responsive. */
const PAGE_SIZE = 100;

const STATUS_OPTIONS: Array<{ value: StatusFilter; label: string }> = [
  { value: '', label: 'All sessions' },
  { value: 'active', label: 'Active' },
  { value: 'ended', label: 'Ended' },
];

const formatWhen = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString() : '—';

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

/** Where a session is started: the Operations page's "Start Agent Session"
 *  button — the param opens its dialog on arrival. */
const START_SESSION_PATH = '/operations?start=agent-session';

/** The operator's display name, falling back to the username. */
const operatorName = (
  row: Pick<AssistSessionRow, 'started_by_full_name' | 'started_by_username'>,
): string | null => row.started_by_full_name?.trim() || row.started_by_username || null;

const StatusBadge: React.FC<{ status: string }> = ({ status }) =>
  status === 'active' ? (
    <Badge variant="success">Active</Badge>
  ) : (
    <Badge variant="muted">Ended</Badge>
  );

/** Who the session acted for, which is the fact a reviewer checks first: its
 *  output carries that person's name, and its authority was theirs.
 *
 *  v5.189.0 — this was a read-only/could-write badge sourced from the session's
 *  capability grant. Grants are gone: a session does what its operator may do,
 *  so the operator IS the authority statement. */
const AuthorityBadge: React.FC<{ operator: string | null }> = ({ operator }) => {
  if (!operator) return <Badge variant="outline">Unknown operator</Badge>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" tabIndex={0}>
          as {operator}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">
        This session acted with {operator}&rsquo;s permissions on the project,
        checked on every call.
      </TooltipContent>
    </Tooltip>
  );
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
      <Card>
        <CardContent className="flex items-center gap-sm p-lg text-metadata text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Loading session…
        </CardContent>
      </Card>
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
      <Card>
        <CardContent className="flex flex-col gap-sm p-md">
          <div className="flex flex-wrap items-center gap-xs">
            <StatusBadge status={session.status} />
            <AuthorityBadge operator={operatorName(session)} />
            {/* v5.203.0 — from observed calls, not the environment probe; the
                probe is optional and proves nothing about the transport. Same
                vocabulary as the start dialog's live-sessions panel so the two
                never disagree about one session again. */}
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
              <dd
                className="truncate text-foreground"
                title={session.started_by_username ?? undefined}
              >
                {safeFallback(operatorName(session), 'unknown')}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-muted-foreground">Started</dt>
              <dd className="truncate text-foreground">{formatWhen(session.started_at)}</dd>
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
              <dd className="truncate text-foreground">
                {safeFallback(session.prompt_version, '—')}
              </dd>
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
        </CardContent>
      </Card>

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
          <Card>
            <CardContent className="p-md text-metadata text-muted-foreground">
              This session wrote no notes.
            </CardContent>
          </Card>
        ) : (
          <div className="overflow-x-auto rounded-panel border border-border">
            <Table style={{ tableLayout: 'fixed' }}>
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
                      {formatWhen(note.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
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

// ---------------------------------------------------------------------------
// List
// ---------------------------------------------------------------------------

const AssistSessions: React.FC = () => {
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const [rows, setRows] = useState<AssistSessionRow[]>([]);
  const [status, setStatus] = useState<StatusFilter>('');
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The API caps a page (default 100). Asking for a page and appending is the
  // difference between "these are your sessions" and "these are your newest 100
  // sessions" — the second is a lie the page has no way to signal otherwise, and
  // silent truncation on a review surface means a session someone is looking for
  // simply isn't there.
  const load = useCallback(
    async (append = false) => {
      setLoading(true);
      try {
        const offset = append ? rows.length : 0;
        const page = await listAssistSessions({
          ...(status ? { status } : {}),
          limit: PAGE_SIZE,
          offset,
        });
        setRows((prev) => (append ? [...prev, ...page] : page));
        // A short page means the end; a full one means there may be more.
        setHasMore(page.length === PAGE_SIZE);
        setError(null);
      } catch (e) {
        setError(formatApiError(e, 'Could not load agent sessions.'));
      } finally {
        setLoading(false);
      }
    },
    [status, rows.length],
  );

  useEffect(() => {
    // Deliberately keyed on the filter, not on `load` — `load` closes over
    // rows.length so it changes every append, and depending on it here would
    // re-fetch page one the moment more rows arrive.
    if (!sessionId) void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, status]);

  const selectedId = sessionId ? Number(sessionId) : null;
  const activeCount = useMemo(
    () => rows.filter((r) => r.status === 'active').length,
    [rows],
  );

  if (selectedId != null && Number.isFinite(selectedId)) {
    return (
      <div className="p-md md:p-lg">
        <Button
          variant="ghost"
          size="sm"
          className="mb-xs px-0"
          onClick={() => navigate('/assist-sessions')}
        >
          <ArrowLeft className="size-4" aria-hidden />
          All agent sessions
        </Button>
        <h1 className="mb-md text-page-title">Agent session #{selectedId}</h1>
        <SessionDetail sessionId={selectedId} />
      </div>
    );
  }

  return (
    <div className="p-md md:p-lg">
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <div className="min-w-0 flex-1">
          <h1 className="text-page-title">Agent Sessions</h1>
          <p className="text-metadata text-muted-foreground">
            Every agent session in this project — what each one was for, whose
            permissions it acted with, and what it produced. A session can query
            the inventory and open recon, plan or execution work.{' '}
            <Link
              to={START_SESSION_PATH}
              className="text-primary underline-offset-4 hover:underline"
            >
              Start a session on Operations
            </Link>
            .
          </p>
        </div>
        <Select value={status || 'all'} onValueChange={(v) => setStatus(v === 'all' ? '' : (v as StatusFilter))}>
          <SelectTrigger className="w-44" aria-label="Filter by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((o) => (
              <SelectItem key={o.value || 'all'} value={o.value || 'all'}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" onClick={() => void load(false)} disabled={loading}>
          {loading ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="size-4" aria-hidden />
          )}
          Refresh
        </Button>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-md">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {activeCount > 0 && (
        <Alert variant="info" className="mb-md">
          <AlertDescription>
            <KeyRound className="mr-xxs inline size-4 align-text-bottom" aria-hidden />
            {activeCount} session{activeCount === 1 ? '' : 's'} still hold a live
            agent key. Sessions lapse on their own when the key expires; end one
            early from the{' '}
            <Link
              to={START_SESSION_PATH}
              className="text-primary underline-offset-4 hover:underline"
            >
              Start Agent Session
            </Link>{' '}
            dialog to revoke it now.
          </AlertDescription>
        </Alert>
      )}

      {loading && rows.length === 0 ? (
        <TableSkeleton />
      ) : rows.length === 0 ? (
        <PostureEmpty
          Icon={MessageCircleQuestion}
          title="No agent sessions yet"
          action={{ to: START_SESSION_PATH, label: 'Start Agent Session' }}
        >
          An agent session lets an agent query this project&rsquo;s data and open
          recon, plan or execution work, with your permissions. It will show up
          here with everything it did.
        </PostureEmpty>
      ) : (
        // Sections, not cards (§7): the table sits on the page, no bordered box.
        <div className="overflow-x-auto">
          <Table style={{ tableLayout: 'fixed' }} className="min-w-[900px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[6%]">#</TableHead>
                <TableHead className="w-[28%]">Purpose</TableHead>
                <TableHead className="w-[12%]">Status</TableHead>
                <TableHead className="w-[12%]">Authority</TableHead>
                <TableHead className="w-[14%]">Started by</TableHead>
                <TableHead className="w-[16%]">Started</TableHead>
                <TableHead className="w-[12%] text-right">Calls · notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <NavigableTableRow key={row.id}>
                  <TableCell className="font-mono text-caption text-muted-foreground">
                    {row.id}
                  </TableCell>
                  {/* Purpose is the primary cell: it carries the link, so the
                      row is navigable by Tab + Enter rather than by a click
                      handler on a <tr> that no screen reader announces. */}
                  <NavigableTableCell
                    to={`/assist-sessions/${row.id}`}
                    ariaLabel={`Open agent session ${row.id}`}
                  >
                    {/* Most sessions state no purpose; the fallback is quiet so
                        a column of it does not read as content. */}
                    {row.purpose?.trim() ? (
                      <span className="line-clamp-2 break-words text-metadata">
                        {row.purpose}
                      </span>
                    ) : (
                      <span className="text-caption text-muted-foreground">
                        No stated purpose
                      </span>
                    )}
                  </NavigableTableCell>
                  <TableCell>
                    <StatusBadge status={row.status} />
                  </TableCell>
                  <TableCell>
                    <AuthorityBadge operator={operatorName(row)} />
                  </TableCell>
                  <TableCell
                    className="truncate text-metadata text-foreground"
                    title={row.started_by_username ?? undefined}
                  >
                    {safeFallback(operatorName(row), 'unknown')}
                  </TableCell>
                  <TableCell className="truncate text-caption text-muted-foreground">
                    {formatWhen(row.started_at)}
                  </TableCell>
                  <TableCell className="text-right text-caption">
                    {/* A session that made no calls is the common dead end — key
                        minted, prompt never pasted. Saying so here stops a
                        reviewer opening it to find out. */}
                    {row.call_count === 0 ? (
                      <span className="text-muted-foreground">not used</span>
                    ) : (
                      <span className="text-foreground">
                        <Bot className="mr-xxs inline size-3 align-text-bottom" aria-hidden />
                        {row.call_count} · {row.note_count}
                      </span>
                    )}
                  </TableCell>
                </NavigableTableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {rows.length > 0 && (
        <div className="mt-sm flex items-center gap-sm">
          {hasMore ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void load(true)}
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : null}
              Load more
            </Button>
          ) : null}
          {/* Say what is on screen either way. A review surface that shows the
              newest N with no marker reads as "these are all your sessions",
              and someone looking for an older one concludes it doesn't exist. */}
          <span className="text-caption text-muted-foreground">
            {hasMore
              ? `Showing the ${rows.length} most recent.`
              : `${rows.length} session${rows.length === 1 ? '' : 's'} — all of them.`}
          </span>
        </div>
      )}
    </div>
  );
};

export default AssistSessions;
