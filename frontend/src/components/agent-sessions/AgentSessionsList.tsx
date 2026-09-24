/**
 * The agent-session list — one row per key an operator handed an agent.
 *
 * v5.294.0 (UX review): Agent Sessions was its own page listing the same
 * sessions Agent Runs lists, in a different format and with different controls.
 * It is now the "Sessions" view of Agent Runs (`/agent-activity?view=sessions`);
 * this component is that view, and the badges below are shared with the session
 * detail page (`/assist-sessions/:id`, which kept its route).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bot, KeyRound, Loader2, MessageCircleQuestion } from 'lucide-react';

import { AssistSessionRow, listAssistSessions } from '../../services/api';
import ListFilterBar, { FILTER_TRIGGER_CLASS } from '../ListFilterBar';
import { NavigableTableCell, NavigableTableRow } from '../NavigableTableRow';
import { TableSkeleton } from '../PageSkeleton';
import PostureEmpty from '../posture/PostureEmpty';
import TimeAgo from '../TimeAgo';
import { Alert, AlertDescription } from '../ui/alert';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { formatApiError } from '../../utils/apiErrors';
import { safeFallback } from '../../utils/uiStyles';

type StatusFilter = '' | 'active' | 'ended';

/** One page. Matches the API's own default; the list appends rather than
 *  raising it, so a project with thousands of sessions stays responsive. */
const PAGE_SIZE = 100;

const STATUS_OPTIONS: Array<{ value: StatusFilter; label: string }> = [
  { value: '', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'ended', label: 'Ended' },
];

/** Where a session is started: the Operations page's "Start Agent Session"
 *  button — the param opens its dialog on arrival. */
export const START_SESSION_PATH = '/operations?start=agent-session';

/** The operator's display name, falling back to the username. */
export const operatorName = (
  row: Pick<AssistSessionRow, 'started_by_full_name' | 'started_by_username'>,
): string | null => row.started_by_full_name?.trim() || row.started_by_username || null;

export const SessionStatusBadge: React.FC<{ status: string }> = ({ status }) =>
  status === 'active' ? (
    <Badge variant="success">Active</Badge>
  ) : (
    <Badge variant="muted">Ended</Badge>
  );

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin role',
  analyst: 'Analyst role',
  auditor: 'Auditor role',
  viewer: 'Viewer role',
  global_admin: 'Global admin',
};

/** The badge text for a session's authority. */
const authorityLabel = (role: string | null | undefined): string =>
  role ? ROLE_LABELS[role] ?? `${role} role` : 'No project role';

/** The authority the session acts with — its operator's PROJECT ROLE.
 *
 *  v5.189.0 made this "as <operator>", which repeated Started by (and wrapped
 *  on a long name). v5.288.0 — the role itself, which is what the agent gate
 *  checks. It is the role NOW: the role at start is not recorded, and every
 *  call is checked against the current one, so the tooltip says so.
 *  v5.294.0 — the chip stays inside its cell: "No project role" ran on into
 *  Started by. It truncates, the tooltip carries the whole story. */
export const AuthorityBadge: React.FC<{ role: string | null | undefined; operator: string | null }> = ({
  role,
  operator,
}) => {
  const who = operator ?? 'the operator';
  const explanation = !role
    ? `${who} is no longer a member of this project, so any further call with this session's key is refused.`
    : role === 'global_admin'
      ? `${who} is a global admin, which the agent gate treats as full access to every project.`
      : `The session acts with ${who}'s ${role} role on this project.`;
  const label = authorityLabel(role);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" tabIndex={0} className="max-w-full overflow-hidden" aria-label={label}>
          <span className="truncate whitespace-nowrap">{label}</span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">
        {explanation} This is the operator&rsquo;s role now — it is checked on every
        call, and the role at the session&rsquo;s start is not recorded.
      </TooltipContent>
    </Tooltip>
  );
};

export interface AgentSessionsListProps {
  /** Bumped by the host page to reload page one (its refresh control). */
  refreshNonce?: number;
  /** Resume / End for a row, drawn by the host page, which owns those dialogs. */
  renderActions?: (row: AssistSessionRow) => React.ReactNode;
  /** Told when a load finishes, for the host page's "updated" indicator. */
  onLoaded?: () => void;
}

export const AgentSessionsList: React.FC<AgentSessionsListProps> = ({
  refreshNonce = 0,
  renderActions,
  onLoaded,
}) => {
  const [rows, setRows] = useState<AssistSessionRow[]>([]);
  const [status, setStatus] = useState<StatusFilter>('');
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The API caps a page (default 100). Asking for a page and appending is the
  // difference between "these are your sessions" and "these are your newest 100
  // sessions" — the second is a lie the page has no way to signal otherwise.
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
        onLoaded?.();
      }
    },
    [status, rows.length, onLoaded],
  );

  useEffect(() => {
    // Keyed on the filter and the host's refresh, not on `load` — `load`
    // closes over rows.length, so depending on it would re-fetch page one the
    // moment more rows arrive.
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, refreshNonce]);

  const activeCount = useMemo(() => rows.filter((r) => r.status === 'active').length, [rows]);

  return (
    <div className="flex flex-col gap-sm">
      <ListFilterBar
        summary={rows.length > 0
          ? (hasMore
            ? `Showing the ${rows.length} most recent`
            : `${rows.length} session${rows.length === 1 ? '' : 's'} — all of them`)
          : undefined}
      >
        <Select value={status || 'all'} onValueChange={(v) => setStatus(v === 'all' ? '' : (v as StatusFilter))}>
          <SelectTrigger className={`${FILTER_TRIGGER_CLASS} w-40`} aria-label="Filter sessions by status">
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
      </ListFilterBar>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {activeCount > 0 && (
        <p className="flex min-w-0 items-start gap-xxs text-caption text-muted-foreground">
          <KeyRound className="mt-px size-3.5 shrink-0" aria-hidden />
          <span>
            {activeCount} session{activeCount === 1 ? '' : 's'} still hold a live agent key.
            Sessions lapse on their own when the key expires; end one early from its row to
            revoke it now.
          </span>
        </p>
      )}

      {loading && rows.length === 0 ? (
        <TableSkeleton />
      ) : rows.length === 0 ? (
        error ? null : (
          <PostureEmpty
            Icon={MessageCircleQuestion}
            title="No agent sessions yet"
            action={{ to: START_SESSION_PATH, label: 'Start Agent Session' }}
          >
            An agent session lets an agent query this project&rsquo;s data and open
            recon, plan or execution work, with your permissions. It will show up
            here with everything it did.
          </PostureEmpty>
        )
      ) : (
        // Sections, not cards (§7): the table sits on the page, no bordered box.
        // v5.294.0 — the column budget fits the content width (it used to set a
        // 900px minimum and scroll); Purpose takes what is left.
        <Table data-testid="sessions-table">
          <TableHeader>
            <TableRow>
              <TableHead className="w-12">#</TableHead>
              <TableHead>Purpose</TableHead>
              <TableHead className="w-24">Status</TableHead>
              <TableHead className="w-32">Authority</TableHead>
              <TableHead className="w-36">Started by</TableHead>
              <TableHead className="w-24">Started</TableHead>
              <TableHead className="w-24 text-right">Calls · notes</TableHead>
              {renderActions && <TableHead className="w-20"><span className="sr-only">Actions</span></TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <NavigableTableRow key={row.id}>
                <TableCell className="font-mono text-caption text-muted-foreground">{row.id}</TableCell>
                {/* Purpose is the primary cell: it carries the link, so the row
                    is navigable by Tab + Enter. */}
                <NavigableTableCell
                  to={`/assist-sessions/${row.id}`}
                  ariaLabel={`Open agent session ${row.id}`}
                >
                  {/* Most sessions state no purpose; the fallback is quiet so a
                      column of it does not read as content. */}
                  {row.purpose?.trim() ? (
                    <span className="line-clamp-2 break-words text-metadata">{row.purpose}</span>
                  ) : (
                    <span className="text-caption text-muted-foreground">No stated purpose</span>
                  )}
                </NavigableTableCell>
                <TableCell>
                  <SessionStatusBadge status={row.status} />
                </TableCell>
                <TableCell className="overflow-hidden">
                  <AuthorityBadge role={row.operator_role} operator={operatorName(row)} />
                </TableCell>
                <TableCell
                  className="truncate text-metadata text-foreground"
                  title={row.started_by_username ?? undefined}
                >
                  {safeFallback(operatorName(row), 'unknown')}
                </TableCell>
                <TableCell className="truncate text-caption text-muted-foreground">
                  <TimeAgo value={row.started_at} />
                </TableCell>
                <TableCell className="text-right text-caption">
                  {/* A session that made no calls is the common dead end — key
                      minted, prompt never pasted. */}
                  {row.call_count === 0 ? (
                    <span className="text-muted-foreground">not used</span>
                  ) : (
                    <span className="text-foreground">
                      <Bot className="mr-xxs inline size-3 align-text-bottom" aria-hidden />
                      {row.call_count} · {row.note_count}
                    </span>
                  )}
                </TableCell>
                {renderActions && <TableCell>{renderActions(row)}</TableCell>}
              </NavigableTableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {hasMore && rows.length > 0 && (
        <div>
          <Button variant="outline" size="sm" onClick={() => void load(true)} disabled={loading}>
            {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            Load more
          </Button>
        </div>
      )}

      <p className="text-caption text-muted-foreground">
        <Link to={START_SESSION_PATH} className="text-primary underline-offset-4 hover:underline">
          Start a session on Operations
        </Link>
        .
      </p>
    </div>
  );
};

export default AgentSessionsList;
