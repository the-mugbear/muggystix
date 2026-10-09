/**
 * The host tests one agent session proposed (5.320.0) — the session page's
 * "what did it do?" for testing work. It replaces the list of plans and
 * execution runs a session used to open. Each row opens the host, where the
 * test, its status and its evidence live.
 */
import React from 'react';
import { useMutation } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { listHostTests, type HostTest } from '../../services/api';
import { useListQuery } from '../../hooks/useListQuery';
import { queryErrorText } from '../../lib/query';
import { hostTestStatusLabel, hostTestStatusVariant } from '../../utils/hostTests';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

const PAGE = 25;
const FAILED = 'Could not load the tests this session proposed.';

export const SessionTests: React.FC<{ sessionId: number; ended: boolean }> = ({ sessionId, ended }) => {
  const list = useListQuery<HostTest>(
    'listHostTests',
    ({ offset, limit }) => listHostTests({ agent_session_id: sessionId, limit, offset }),
    [{ agent_session_id: sessionId }],
    { pageSize: PAGE, errorMessage: FAILED },
  );
  // A failed "Show more" keeps the rows that are shown and says so under them.
  const more = useMutation({ mutationFn: () => list.loadMore() });
  const { total } = list;
  const error = list.error ?? queryErrorText(more.error, FAILED);

  if (!list.rows) {
    if (list.error) {
      return (
        <p role="alert" className="text-metadata text-destructive">
          {list.error}{' '}
          <Button variant="ghost" size="sm" onClick={() => void list.reload()}>Retry</Button>
        </p>
      );
    }
    return <p role="status" className="text-metadata text-muted-foreground">Loading tests…</p>;
  }
  const rows = list.rows;
  if (total === 0) {
    return (
      <p className="text-metadata text-muted-foreground">
        None — this session has proposed no tests{ended ? '' : ' so far'}.
      </p>
    );
  }
  return (
    <>
      <Table style={{ tableLayout: 'fixed' }} data-testid="session-tests">
        <TableHeader>
          <TableRow>
            <TableHead className="w-40">Host</TableHead>
            <TableHead>Test</TableHead>
            <TableHead className="w-28">Status</TableHead>
            <TableHead className="w-24 text-right">Evidence</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((t) => (
            <TableRow key={t.id}>
              <TableCell className="truncate font-mono text-caption">
                <Link
                  to={`/hosts/${t.host_id}#host-test-${t.id}`}
                  className="text-primary underline-offset-4 hover:underline"
                  title={t.target_fqdn ?? t.host_ip}
                >
                  {t.host_ip}
                </Link>
              </TableCell>
              <TableCell className="truncate" title={t.description}>
                {t.tool && <span className="font-semibold">{t.tool} · </span>}
                {t.description}
              </TableCell>
              <TableCell>
                <Badge variant={hostTestStatusVariant(t.status)} className="whitespace-nowrap">
                  {hostTestStatusLabel(t.status)}
                </Badge>
              </TableCell>
              <TableCell className="text-right tabular-nums">{t.evidence_count.toLocaleString()}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {error && <p role="alert" className="text-caption text-destructive">{error}</p>}
      {rows.length < total && (
        <div>
          <Button variant="ghost" size="sm" disabled={list.loading || list.loadingMore} onClick={() => more.mutate()}>
            Show more ({(total - rows.length).toLocaleString()} left)
          </Button>
        </div>
      )}
    </>
  );
};

export default SessionTests;
