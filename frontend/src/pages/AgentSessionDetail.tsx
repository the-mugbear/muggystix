/**
 * One agent session (5.312.0) — the page an operator manages it from: where it
 * stands, the controls (Resume, End with the wrap-up prompt), the tests and
 * evidence it recorded, the notes it wrote under the operator's name,
 * and every call it made.
 *
 * Keyed by the SESSION id — the one Agent Sessions, End, Resume and the agent
 * itself use, and (5.328.0) the session's only id: the row, its notes and its
 * API-call feed are all read by it. The review page this replaces
 * (`/assist-sessions/:id`) was keyed by a second id sequence (session #72 =
 * detail #52); that path now redirects here.
 */
import React, { useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Loader2, StickyNote } from 'lucide-react';

import { getAgentSession, getAgentSessionNotes } from '../services/api';
import AgentActivityLog from '../components/AgentActivityLog';
import {
  AuthorityBadge,
  SessionActions,
  SessionStateBadge,
  StateLineText,
  rowOperatorName,
  sessionStateLine,
} from '../components/agent-sessions/SessionParts';
import SessionTests from '../components/agent-sessions/SessionTests';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import LastUpdated from '../components/LastUpdated';
import { useAgentSessionControls } from '../hooks/useAgentSessionControls';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { useProjectId } from '../hooks/useProjectId';
import { queryErrorText } from '../lib/query';
import { formatTimestamp } from '../utils/relativeTime';
import { safeFallback } from '../utils/uiStyles';
import { SESSIONS_LIST_PATH } from '../utils/agentRuns';

/** How long the session ran (to its end, or to now while it is active).
 *  Not `utils/scanTime`'s formatDuration, which takes seconds. */
const sessionDuration = (from: string | null | undefined, to: string | null | undefined): string => {
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

const ConnectionBadge: React.FC<{ connection: string }> = ({ connection }) => {
  const [label, explanation] = connection === 'none'
    ? ['Never connected', 'No authenticated call ever reached this session — the key was minted and no client used it.']
    : connection === 'mcp'
      ? ['MCP verified', 'At least one authenticated tool call arrived through the MCP transport.']
      : ['Connected via curl', 'Calls arrived by direct HTTP (the pasted-prompt path); no MCP client used this key.'];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" tabIndex={0}>{label}</Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">{explanation}</TooltipContent>
    </Tooltip>
  );
};

const Fact: React.FC<{ label: string; title?: string; children: React.ReactNode }> = ({ label, title, children }) => (
  <div className="min-w-0">
    <dt className="text-muted-foreground">{label}</dt>
    <dd className="truncate text-foreground" title={title}>{children}</dd>
  </div>
);

const AgentSessionDetail: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const id = Number(sessionId);
  const validId = Number.isFinite(id) && id > 0;
  const projectId = useProjectId();
  const session = useQuery({
    queryKey: ['getAgentSession', projectId, id],
    queryFn: ({ signal }) => getAgentSession(projectId, id, signal),
    enabled: validId,
  });
  const row = session.data ?? null;
  const rowId = row?.id;
  // The notes are their own read: when it fails, the session, its controls
  // and its calls still show.
  const written = useQuery({
    queryKey: ['getAgentSessionNotes', projectId, rowId],
    queryFn: ({ signal }) => getAgentSessionNotes(projectId, rowId as number, undefined, signal),
    enabled: rowId != null,
  });
  const notesError = queryErrorText(written.error, 'Could not load the session’s notes.');
  // No notes from an earlier read beside the error.
  const notes = notesError ? null : written.data ?? null;
  const error = validId
    ? queryErrorText(session.error, 'Could not load this agent session.')
    : 'Not a session id.';
  const loading = session.isFetching || written.isFetching;
  const lastFetched = session.dataUpdatedAt ? new Date(session.dataUpdatedAt) : null;
  const { refetch: refetchSession } = session;
  const { refetch: refetchNotes } = written;
  // The Refresh button.  (An End or a Resume asks for these again itself.)
  const refresh = useCallback(() => {
    void refetchSession();
    if (rowId != null) void refetchNotes();
  }, [refetchSession, refetchNotes, rowId]);
  const controls = useAgentSessionControls();

  const back = (
    <Button variant="ghost" size="sm" className="mb-xs px-0" asChild>
      <Link to={SESSIONS_LIST_PATH}>
        <ArrowLeft className="size-4" aria-hidden />
        All agent sessions
      </Link>
    </Button>
  );

  if (loading && !row) {
    return (
      <div className="p-md md:p-lg">
        {back}
        <p className="flex items-center gap-sm text-metadata text-muted-foreground" role="status">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Loading session…
        </p>
      </div>
    );
  }
  // The whole page is the error only when there is no session to show: a
  // Refresh that failed keeps the session that was read, and says so above it.
  if (!row) {
    return (
      <div className="p-md md:p-lg">
        {back}
        <h1 className="mb-md text-page-title">Agent session #{safeFallback(sessionId, '?')}</h1>
        <Alert variant="destructive">
          <AlertDescription>{error ?? 'Session not found.'}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const operator = rowOperatorName(row);
  // The agent's client, recorded from its MCP handshake.
  const clientName = row.generated_by_tool ?? null;
  const testCount = row.host_test_count ?? 0;
  const evidenceCount = row.evidence_count ?? 0;
  const ended = row.status !== 'active';

  return (
    <div className="flex flex-col gap-lg p-md md:p-lg">
      {controls.dialogs}
      <div>
        {back}
        <div className="flex items-start justify-between gap-sm">
          <div className="min-w-0 flex-1">
            <h1 className="text-page-title">Agent session #{row.id}</h1>
            <p className="mt-xxs break-words text-metadata text-foreground">
              {safeFallback(row.purpose, 'No stated purpose')}
            </p>
          </div>
          <LastUpdated compact lastFetched={lastFetched} onRefresh={refresh} isLoading={loading} label="agent session" />
        </div>
      </div>

      {error && (
        <Alert variant="destructive" data-testid="session-refresh-error">
          <AlertDescription className="flex flex-wrap items-center gap-sm">
            <span className="min-w-0 break-words">{error} The session below is as it was last read.</span>
            <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>Retry</Button>
          </AlertDescription>
        </Alert>
      )}

      {/* Sections, not cards (§7): where it stands and what can be done. */}
      <section className="flex flex-col gap-sm border-b border-border pb-md">
        <div className="flex flex-wrap items-center gap-xs">
          <SessionStateBadge row={row} />
          <AuthorityBadge role={row.operator_role} operator={operator} />
          {row.connection && <ConnectionBadge connection={row.connection} />}
          <StateLineText line={sessionStateLine(row)} className="min-w-0" />
        </div>
        <SessionActions row={row} controls={controls} labelled />
        {!ended && !row.can_end && !row.can_resume && (
          <p className="text-caption text-muted-foreground">
            Only {safeFallback(operator, 'its operator')} or a project admin can end this session, and only{' '}
            {safeFallback(operator, 'its operator')} can resume it — its key acts under their name.
          </p>
        )}
        <dl className="grid grid-cols-2 gap-x-md gap-y-xs text-caption md:grid-cols-4">
          <Fact label="Started by" title={row.user_username ?? undefined}>{safeFallback(operator, 'unknown')}</Fact>
          <Fact label="Started">{formatTimestamp(row.started_at)}</Fact>
          <Fact label={ended ? 'Ran for' : 'Running for'}>{sessionDuration(row.started_at, row.completed_at)}</Fact>
          <Fact label="Last call">{row.last_activity_at ? formatTimestamp(row.last_activity_at) : 'none yet'}</Fact>
          <Fact
            label="Model"
            title={row.prompt_version ? `Prompt ${row.prompt_version}` : undefined}
          >
            {/* The agent's own report; never its name (5.314.1: "admin-agent"
                read as a model). */}
            {safeFallback(row.generated_by_model, 'not reported')}
          </Fact>
          <Fact label="Client" title={clientName ?? undefined}>{safeFallback(clientName, '—')}</Fact>
          <Fact label="API calls">{row.call_count != null ? row.call_count.toLocaleString() : '—'}</Fact>
          <Fact label="Feedback left">{(row.feedback_count ?? 0).toLocaleString()}</Fact>
        </dl>
      </section>

      <PostureSection
        title={<>Tests proposed {testCount > 0 && <SectionCount>{testCount}</SectionCount>}</>}
        description={
          `The tests this session put on hosts — each opens its host, where the test, its status and its `
          + `evidence live. It recorded ${evidenceCount.toLocaleString()} evidence record${evidenceCount === 1 ? '' : 's'}.`
        }
      >
        <SessionTests sessionId={row.id} ended={ended} />
      </PostureSection>

      {notesError && (
        <Alert variant="destructive">
          <AlertDescription>{notesError}</AlertDescription>
        </Alert>
      )}

      {notes && (
        <PostureSection
          title={(
            <span className="flex items-center gap-xs">
              <StickyNote className="size-4 text-primary" aria-hidden />
              Notes written <SectionCount>{notes.total}</SectionCount>
            </span>
          )}
          description="The session’s durable output on hosts. These appear under the operator’s name with an agent badge — the answer to “what did it put my name on?”"
        >
          {notes.items.length === 0 ? (
            <p className="text-metadata text-muted-foreground">This session wrote no notes.</p>
          ) : (
            <Table style={{ tableLayout: 'fixed' }}>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[18%]">Host</TableHead>
                  <TableHead className="w-[66%]">Note</TableHead>
                  <TableHead className="w-[16%]">Written</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {notes.items.map((note) => (
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
                      <span className="line-clamp-3 break-words text-caption text-foreground">{note.body}</span>
                    </TableCell>
                    <TableCell className="align-top text-caption text-muted-foreground">
                      {formatTimestamp(note.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {notes.total > notes.items.length && (
            <p className="mt-xxs text-caption text-muted-foreground">
              Showing the {notes.items.length} most recent of {notes.total}.
            </p>
          )}
        </PostureSection>
      )}

      <AgentActivityLog
        sessionId={row.id}
        title="API activity"
        subtitle="Every request this session's agent made, in order. Filter by host or IP to answer 'did it look at the right things?'"
        defaultMineOnly={false}
      />
    </div>
  );
};

export default AgentSessionDetail;
