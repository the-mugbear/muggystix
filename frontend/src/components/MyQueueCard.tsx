/**
 * Personal attention queue card — extracted from Dashboard.tsx.
 * Self-contained: fetches its own data and renders empty state.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, RefreshCw } from 'lucide-react';
import { getMyAttentionQueue } from '../services/api';
import type { MyAttentionHost, MyAttentionResponse } from '../services/api';
import { NavigableTableCell, NavigableTableRow } from './NavigableTableRow';
import { Card, CardContent } from './ui/card';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { formatApiError } from '../utils/apiErrors';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table';

function fmtAgo(value?: string | null): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export const MyQueueCard: React.FC = () => {
  const navigate = useNavigate();
  const [data, setData] = useState<MyAttentionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  // Audit CRIT-6 — previously the failure path collapsed silently into
  // the empty-queue Alert, so a backend outage looked like "nothing to
  // review". Track error separately and surface a retry.
  const [error, setError] = useState<string | null>(null);

  const fetchQueue = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getMyAttentionQueue(10)
      .then((resp) => { if (!cancelled) setData(resp); })
      .catch((err) => {
        if (cancelled) return;
        setData(null);
        setError(formatApiError(err, 'Could not load your attention queue.'));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => fetchQueue(), [fetchQueue]);

  const items: MyAttentionHost[] = data?.items ?? [];
  const total = data?.in_review_count ?? 0;

  return (
    <Card className="h-full">
      <CardContent className="p-md">
        <div className="mb-sm flex items-start justify-between gap-sm">
          <div>
            <p className="text-subheading font-semibold text-foreground">My Queue</p>
            <p className="text-caption text-muted-foreground">
              Hosts you've marked <strong>In Review</strong>
              {total > 0 && <> · showing {items.length} of {total}</>}
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={() => navigate('/hosts?follow_status=in_review')}>
            All In Review
          </Button>
        </div>
        {loading ? (
          <div className="flex items-center gap-xs">
            <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            <p className="text-metadata text-muted-foreground">Loading queue…</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTitle>Couldn't load your queue</AlertTitle>
            <AlertDescription>
              <p className="break-words">{error}</p>
              <Button
                size="sm"
                variant="outline"
                className="mt-xs"
                onClick={fetchQueue}
              >
                <RefreshCw className="size-3.5" aria-hidden />
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : items.length === 0 ? (
          <Alert variant="info">
            <AlertDescription>
              Your queue is empty. Open the Hosts page and mark a host as{' '}
              <strong>In Review</strong> to add it here. The queue is per-user — each analyst sees
              their own active work, not a shared list.
            </AlertDescription>
          </Alert>
        ) : (
          <div className="overflow-x-auto rounded-panel border border-border">
            <Table className="min-w-[580px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[40%]">Host</TableHead>
                  <TableHead className="w-[28%]">Findings</TableHead>
                  <TableHead className="w-[14%] text-right">Ports</TableHead>
                  <TableHead className="w-[18%] text-right">Touched</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((host) => (
                  // v2.43.0 — UX review #2: NavigableTableRow replaces the
                  // interactive-<tr> pattern.  The Link lives in the primary
                  // cell (IP/hostname); other cells stay independent.
                  <NavigableTableRow key={host.host_id}>
                    <NavigableTableCell
                      to={`/hosts/${host.host_id}`}
                      ariaLabel={`Open host ${host.ip_address}${host.hostname ? ` (${host.hostname})` : ''}`}
                    >
                      <p className="truncate font-mono text-metadata font-medium">{host.ip_address}</p>
                      {host.hostname && (
                        <p className="truncate text-caption text-muted-foreground">{host.hostname}</p>
                      )}
                    </NavigableTableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-xxs">
                        {host.critical_vulns > 0 && (
                          <Badge variant="severity-critical">{host.critical_vulns} crit</Badge>
                        )}
                        {host.high_vulns > 0 && (
                          <Badge variant="severity-high">{host.high_vulns} high</Badge>
                        )}
                        {host.critical_vulns === 0 && host.high_vulns === 0 && (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">{host.open_port_count}</TableCell>
                    <TableCell className="text-right text-caption text-muted-foreground">
                      {fmtAgo(host.follow_updated_at) || '—'}
                    </TableCell>
                  </NavigableTableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default MyQueueCard;
