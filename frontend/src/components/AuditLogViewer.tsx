/**
 * Audit log viewer (v2.243.0) — admin only, deployment-wide.
 *
 * CLAUDE.md documents audit logging as a feature and the backend has recorded
 * events all along; there was simply no way to read them without hitting the
 * API by hand. That undercuts the point of an audit trail, and especially the
 * auditor role.
 *
 * NOT project-scoped: the underlying endpoint spans the whole deployment,
 * which is why this lives in System Settings rather than Project Settings.
 * Login attempts and user administration aren't project events.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react';
import {
  AuditLogRow,
  AuditStats,
  getAuditStats,
  listAuditLogs,
} from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Input } from './ui/input';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from './ui/select';

const PAGE_SIZE = 50;

function when(value?: string | null): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

const AuditLogViewer: React.FC = () => {
  const toast = useToast();
  const [rows, setRows] = useState<AuditLogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [actionFilter, setActionFilter] = useState('all');
  const [resourceFilter, setResourceFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async (nextSkip: number) => {
    setLoading(true);
    setError(null);
    try {
      const page = await listAuditLogs({
        skip: nextSkip,
        limit: PAGE_SIZE,
        ...(actionFilter !== 'all' ? { action: actionFilter } : {}),
        ...(resourceFilter.trim() ? { resource_type: resourceFilter.trim() } : {}),
      });
      setRows(page.logs ?? []);
      setTotal(page.total ?? 0);
      setSkip(nextSkip);
    } catch (err) {
      // A failed fetch must not render as an empty (i.e. "nothing happened")
      // audit trail — that is the most misleading possible state here.
      setError(formatApiError(err, 'Failed to load audit log.'));
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [actionFilter, resourceFilter]);

  useEffect(() => { void reload(0); }, [reload]);

  useEffect(() => {
    getAuditStats()
      .then(setStats)
      .catch(() => setStats(null));  // stats are a nicety; the table is the feature
  }, []);

  const actionOptions = stats?.top_actions?.map((a) => a.action) ?? [];
  const pageEnd = Math.min(skip + PAGE_SIZE, total);

  return (
    <Card className="mb-md">
      <CardHeader className="flex flex-row items-center justify-between gap-sm">
        <CardTitle className="min-w-0">
          Audit Log
          {stats && stats.failed_logs > 0 && (
            <Badge variant="outline" className="ml-xs border-destructive/40 text-destructive">
              {stats.failed_logs} failed
            </Badge>
          )}
        </CardTitle>
        <Button size="sm" variant="outline" onClick={() => void reload(skip)} disabled={loading}>
          <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} aria-hidden />
          Refresh
        </Button>
      </CardHeader>
      <CardContent>
        <p className="mb-sm text-caption text-muted-foreground">
          Authentication and administration events across the whole deployment — not scoped
          to the selected project.
          {stats ? ` ${stats.recent_logs} in the last 24 hours.` : ''}
        </p>

        <div className="mb-sm flex flex-wrap items-end gap-xs">
          <div className="space-y-xxs">
            <label className="text-caption text-muted-foreground" htmlFor="audit-action">Action</label>
            <Select value={actionFilter} onValueChange={setActionFilter}>
              <SelectTrigger id="audit-action" className="h-8 w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All actions</SelectItem>
                {actionOptions.map((a) => (
                  <SelectItem key={a} value={a}>{a}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-xxs">
            <label className="text-caption text-muted-foreground" htmlFor="audit-resource">
              Resource type
            </label>
            <Input
              id="audit-resource"
              value={resourceFilter}
              onChange={(e) => setResourceFilter(e.target.value)}
              placeholder="e.g. user"
              className="h-8 w-[180px]"
              maxLength={60}
            />
          </div>
        </div>

        {error && <p className="mb-sm text-metadata text-destructive" role="alert">{error}</p>}

        {loading && rows.length === 0 && (
          <p className="py-md text-center text-metadata text-muted-foreground">
            <Loader2 className="mr-xs inline size-4 animate-spin" aria-hidden />
            Loading audit log…
          </p>
        )}

        {!loading && !error && rows.length === 0 && (
          <p className="py-md text-center text-metadata text-muted-foreground">
            No audit events match these filters.
          </p>
        )}

        {rows.length > 0 && (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-metadata" style={{ tableLayout: 'fixed' }}>
                <colgroup>
                  <col style={{ width: '16%' }} />
                  <col style={{ width: '19%' }} />
                  <col style={{ width: '15%' }} />
                  <col style={{ width: '8%' }} />
                  <col style={{ width: '12%' }} />
                  <col style={{ width: '30%' }} />
                </colgroup>
                <thead>
                  <tr className="border-b border-border text-left text-caption text-muted-foreground">
                    <th className="py-xs pr-xs font-medium">When</th>
                    <th className="py-xs pr-xs font-medium">Action</th>
                    <th className="py-xs pr-xs font-medium">Resource</th>
                    <th className="py-xs pr-xs font-medium">User</th>
                    <th className="py-xs pr-xs font-medium">Source IP</th>
                    <th className="py-xs pr-xs font-medium">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className="border-b border-border/50 align-top">
                      <td className="py-xs pr-xs whitespace-nowrap text-muted-foreground">
                        {when(r.created_at)}
                      </td>
                      <td className="py-xs pr-xs">
                        <span className="flex items-start gap-xxs">
                          {r.success
                            ? <CheckCircle2 className="mt-0.5 size-3 shrink-0 text-success" aria-hidden />
                            : <AlertCircle className="mt-0.5 size-3 shrink-0 text-destructive" aria-hidden />}
                          <span className="min-w-0 truncate font-mono text-caption" title={r.action}>
                            {safeFallback(r.action)}
                          </span>
                        </span>
                      </td>
                      <td className="py-xs pr-xs">
                        <span className="block truncate" title={r.resource_type ?? undefined}>
                          {safeFallback(r.resource_type)}
                          {r.resource_id ? (
                            <span className="text-muted-foreground"> #{r.resource_id}</span>
                          ) : null}
                        </span>
                      </td>
                      <td className="py-xs pr-xs text-muted-foreground">
                        {r.user_id ?? '—'}
                      </td>
                      <td className="py-xs pr-xs">
                        <span className="block truncate font-mono text-caption" title={r.ip_address ?? undefined}>
                          {safeFallback(r.ip_address)}
                        </span>
                      </td>
                      <td className="py-xs pr-xs">
                        <span
                          className="line-clamp-2 break-words"
                          title={r.error_message || r.details || undefined}
                        >
                          {r.error_message
                            ? <span className="text-destructive">{r.error_message}</span>
                            : safeFallback(r.details)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-sm flex items-center justify-between text-caption text-muted-foreground">
              <span>
                {total === 0 ? 'No events' : `${skip + 1}–${pageEnd} of ${total}`}
              </span>
              <span className="flex gap-xs">
                <Button
                  size="sm" variant="outline"
                  disabled={skip === 0 || loading}
                  onClick={() => void reload(Math.max(0, skip - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  size="sm" variant="outline"
                  disabled={pageEnd >= total || loading}
                  onClick={() => void reload(skip + PAGE_SIZE)}
                >
                  Next
                </Button>
              </span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
};

export default AuditLogViewer;
