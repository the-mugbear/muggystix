/**
 * One agent session (5.312.0) — the page an operator manages it from: where it
 * stands, the controls (Resume, End with the wrap-up prompt), the plans and
 * executions it opened, the notes it wrote under the operator's name,
 * and every call it made.
 *
 * Keyed by the SESSION id — the one Agent Sessions, End, Resume and the agent
 * itself use. The review page it replaces (`/assist-sessions/:id`) was keyed
 * by the session's detail row, a second id sequence (session #72 = detail
 * #52), and had no controls and none of the session's work; that path now
 * redirects here. Notes and the API-call feed are still read by the detail
 * row's id, which the session row names (`assist_session_id`).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Loader2, StickyNote } from 'lucide-react';

import {
  AgentSessionRow,
  AssistSessionDetail,
  getAgentSession,
  getAssistSession,
} from '../services/api';
import AgentActivityLog from '../components/AgentActivityLog';
import {
  AuthorityBadge,
  SessionActions,
  SessionStateBadge,
  StateLineText,
  rowOperatorName,
  sessionStateLine,
} from '../components/agent-sessions/SessionParts';
import PostureSection, { SectionCount } from '../components/posture/PostureSection';
import LastUpdated from '../components/LastUpdated';
import { useAgentSessionControls } from '../hooks/useAgentSessionControls';
import { Alert, AlertDescription } from '../components/ui/alert';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { formatApiError } from '../utils/apiErrors';
import { formatTimestamp } from '../utils/relativeTime';
import { safeFallback } from '../utils/uiStyles';
import { PHASE_KIND_LABEL, SESSIONS_LIST_PATH, isOpenPhase, phasePath } from '../utils/agentRuns';

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
  const [row, setRow] = useState<AgentSessionRow | null>(null);
  const [review, setReview] = useState<AssistSessionDetail | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastFetched, setLastFetched] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  const controls = useAgentSessionControls(refresh);

  // Another session (Back/Forward reuses this element): drop the previous
  // one's data, so its notes never show under this session's header.
  useEffect(() => {
    setRow(null);
    setReview(null);
    setReviewError(null);
    setError(null);
  }, [id]);

  useEffect(() => {
    if (!Number.isFinite(id) || id <= 0) {
      setError('Not a session id.');
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const next = await getAgentSession(id);
        if (cancelled) return;
        setRow(next);
        setError(null);
        setLastFetched(new Date());
        if (next.assist_session_id != null) {
          try {
            const detail = await getAssistSession(next.assist_session_id);
            if (!cancelled) { setReview(detail); setReviewError(null); }
          } catch (e) {
            // No notes from an earlier read beside the error.
            if (!cancelled) {
              setReview(null);
              setReviewError(formatApiError(e, 'Could not load the session’s notes and calls.'));
            }
          }
        } else {
          setReview(null);
        }
      } catch (e) {
        if (!cancelled) setError(formatApiError(e, 'Could not load this agent session.'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [id, nonce]);

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
  if (error || !row) {
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
  const clientName = row.generated_by_tool ?? review?.agent_tool ?? null;
  const phases = row.phases ?? [];
  // Only an execution RUN can be stranded by its session ending (and ending
  // abandons them since 5.313.1, so this is legacy data); a draft plan is a
  // resting state, not stranded work.
  const strandedRuns = phases.filter((p) => p.kind === 'execution' && isOpenPhase(p)).length;
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

      {/* Sections, not cards (§7): where it stands and what can be done. */}
      <section className="flex flex-col gap-sm border-b border-border pb-md">
        <div className="flex flex-wrap items-center gap-xs">
          <SessionStateBadge row={row} />
          <AuthorityBadge role={row.operator_role} operator={operator} />
          {review && <ConnectionBadge connection={review.connection} />}
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
            title={review?.prompt_version ? `Prompt ${review.prompt_version}` : undefined}
          >
            {/* The agent's own report; never its name (5.314.1: "admin-agent"
                read as a model). */}
            {safeFallback(row.generated_by_model ?? review?.agent_model, 'not reported')}
          </Fact>
          <Fact label="Client" title={clientName ?? undefined}>{safeFallback(clientName, '—')}</Fact>
          <Fact label="API calls">{review ? review.call_count.toLocaleString() : '—'}</Fact>
          <Fact label="Feedback left">{(row.feedback_count ?? 0).toLocaleString()}</Fact>
        </dl>
      </section>

      <PostureSection
        title={<>Work opened {phases.length > 0 && <SectionCount>{phases.length}</SectionCount>}</>}
        description={
          strandedRuns > 0 && ended
            ? 'This session has ended but an execution run it opened is still open — nothing will move it until someone abandons it from its page.'
            : 'The plans and execution runs this session opened, each on its own page.'
        }
      >
        {phases.length === 0 ? (
          <p className="text-metadata text-muted-foreground">
            None — this session has only queried the inventory{ended ? '' : ' so far'}.
          </p>
        ) : (
          <Table style={{ tableLayout: 'fixed' }} data-testid="session-phases">
            <TableHeader>
              <TableRow>
                <TableHead className="w-28">Kind</TableHead>
                <TableHead>Target</TableHead>
                <TableHead className="w-32">Status</TableHead>
                <TableHead className="w-44">Opened</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {phases.map((phase) => (
                <TableRow key={`${phase.kind}-${phase.id}`}>
                  <TableCell className="whitespace-nowrap">
                    <Link to={phasePath(phase)} className="text-primary underline-offset-4 hover:underline">
                      {PHASE_KIND_LABEL[phase.kind]} #{phase.id}
                    </Link>
                  </TableCell>
                  <TableCell className="truncate" title={phase.label ?? undefined}>
                    {safeFallback(phase.label, '—')}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={isOpenPhase(phase) ? 'warning' : 'muted'}
                      className="whitespace-nowrap"
                    >
                      {phase.status.replace(/_/g, ' ')}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-caption text-muted-foreground">{formatTimestamp(phase.started_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </PostureSection>

      {reviewError && (
        <Alert variant="destructive">
          <AlertDescription>{reviewError}</AlertDescription>
        </Alert>
      )}

      {review && (
        <PostureSection
          title={(
            <span className="flex items-center gap-xs">
              <StickyNote className="size-4 text-primary" aria-hidden />
              Notes written <SectionCount>{review.note_count}</SectionCount>
            </span>
          )}
          description="The session’s durable output on hosts. These appear under the operator’s name with an agent badge — the answer to “what did it put my name on?”"
        >
          {review.notes.length === 0 ? (
            <p className="text-metadata text-muted-foreground">This session wrote no notes.</p>
          ) : (
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
                {review.notes.map((note) => (
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
          {review.note_count > review.notes.length && (
            <p className="mt-xxs text-caption text-muted-foreground">
              Showing the {review.notes.length} most recent of {review.note_count}.
            </p>
          )}
        </PostureSection>
      )}

      {row.assist_session_id != null && (
        <AgentActivityLog
          source={{ kind: 'assist', assistSessionId: row.assist_session_id }}
          title="API activity"
          subtitle="Every request this session's agent made, in order. Filter by host or IP to answer 'did it look at the right things?'"
          defaultMineOnly={false}
        />
      )}
    </div>
  );
};

export default AgentSessionDetail;
