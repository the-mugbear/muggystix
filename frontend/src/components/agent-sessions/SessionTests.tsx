/**
 * The host tests one agent session proposed (5.320.0) — the session page's
 * "what did it do?" for testing work. It replaces the list of plans and
 * execution runs a session used to open. Each row opens the host, where the
 * test, its status and its evidence live.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { listHostTests, type HostTest } from '../../services/api';
import { formatApiError } from '../../utils/apiErrors';
import { hostTestStatusLabel, hostTestStatusVariant } from '../../utils/hostTests';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';

const PAGE = 25;

export const SessionTests: React.FC<{ sessionId: number; ended: boolean }> = ({ sessionId, ended }) => {
  const [rows, setRows] = useState<HostTest[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (offset: number) => {
    setLoading(true);
    setError(null);
    try {
      const page = await listHostTests({ agent_session_id: sessionId, limit: PAGE, offset });
      setRows((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
      setTotal(page.total);
    } catch (err) {
      setError(formatApiError(err, 'Could not load the tests this session proposed.'));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void load(0);
  }, [load]);

  if (error && rows.length === 0) {
    return (
      <p role="alert" className="text-metadata text-destructive">
        {error}{' '}
        <Button variant="ghost" size="sm" onClick={() => void load(0)}>Retry</Button>
      </p>
    );
  }
  if (total === null) {
    return <p role="status" className="text-metadata text-muted-foreground">Loading tests…</p>;
  }
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
          <Button variant="ghost" size="sm" disabled={loading} onClick={() => void load(rows.length)}>
            Show more ({(total - rows.length).toLocaleString()} left)
          </Button>
        </div>
      )}
    </>
  );
};

export default SessionTests;
