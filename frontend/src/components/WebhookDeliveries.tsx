/**
 * Webhook delivery outbox (v2.243.0) — admin view of what actually went out.
 *
 * The delivery table has existed since the outbox landed and nothing ever
 * rendered it, so a webhook that had silently stopped delivering looked
 * exactly like one with nothing to report. This is the difference between
 * "configured" and "working" — which is the whole reason the outbox exists.
 *
 * Retry resets the attempt counter server-side: requeuing is a fresh human
 * decision after fixing the receiver, not a continuation of the exhausted
 * backoff.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, Clock, Loader2, RefreshCw, RotateCw } from 'lucide-react';
import {
  WebhookDeliveryRow,
  listWebhookDeliveries,
  retryWebhookDelivery,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { useToast } from '../contexts/ToastContext';
import { formatApiError } from '../utils/apiErrors';
import { safeFallback } from '../utils/uiStyles';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from './ui/select';

const STATUS_FILTERS = [
  { value: 'all', label: 'All statuses' },
  { value: 'pending', label: 'Pending' },
  { value: 'sending', label: 'Sending' },
  { value: 'delivered', label: 'Delivered' },
  { value: 'failed', label: 'Failed' },
];

function statusBadge(status: string) {
  const s = (status || '').toLowerCase();
  if (s === 'delivered') {
    return (
      <Badge variant="outline" className="gap-xxs border-success/40 text-success">
        <CheckCircle2 className="size-3" aria-hidden /> delivered
      </Badge>
    );
  }
  if (s === 'failed') {
    return (
      <Badge variant="outline" className="gap-xxs border-destructive/40 text-destructive">
        <AlertCircle className="size-3" aria-hidden /> failed
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="gap-xxs text-muted-foreground">
      <Clock className="size-3" aria-hidden /> {s || 'unknown'}
    </Badge>
  );
}

function when(value?: string | null): string {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString();
}

const WebhookDeliveries: React.FC = () => {
  const { currentProject } = useProject();
  const toast = useToast();
  const [rows, setRows] = useState<WebhookDeliveryRow[]>([]);
  const [status, setStatus] = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const projectId = currentProject?.id;

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listWebhookDeliveries(
        status === 'all' ? { limit: 100 } : { status, limit: 100 },
      );
      setRows(data);
    } catch (err) {
      // An error must not read as "no deliveries" — that is the exact
      // misreading this panel exists to prevent.
      setError(formatApiError(err, 'Failed to load delivery history.'));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [status, projectId]);

  useEffect(() => {
    if (!projectId) return;
    void reload();
  }, [reload, projectId]);

  const handleRetry = async (row: WebhookDeliveryRow) => {
    setBusyId(row.id);
    try {
      await retryWebhookDelivery(row.id);
      toast.success(`Delivery #${row.id} requeued.`);
      await reload();
    } catch (err) {
      toast.error(formatApiError(err, 'Failed to requeue delivery.'));
    } finally {
      setBusyId(null);
    }
  };

  if (!projectId) return null;

  const failedCount = rows.filter((r) => (r.status || '').toLowerCase() === 'failed').length;

  return (
    <Card className="mb-md">
      <CardHeader className="flex flex-row items-center justify-between gap-sm">
        <CardTitle className="min-w-0">
          Webhook Deliveries
          {failedCount > 0 && (
            <Badge variant="outline" className="ml-xs border-destructive/40 text-destructive">
              {failedCount} failed
            </Badge>
          )}
        </CardTitle>
        <div className="flex shrink-0 items-center gap-xs">
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="h-8 w-[150px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_FILTERS.map((f) => (
                <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" onClick={() => void reload()} disabled={loading}>
            <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} aria-hidden />
            Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <p className="mb-sm text-caption text-muted-foreground">
          The last 100 delivery attempts for this project. A configured webhook that has
          stopped delivering shows up here and nowhere else.
        </p>

        {error && (
          <p className="mb-sm text-metadata text-destructive" role="alert">{error}</p>
        )}

        {loading && rows.length === 0 && (
          <p className="py-md text-center text-metadata text-muted-foreground">
            <Loader2 className="mr-xs inline size-4 animate-spin" aria-hidden />
            Loading deliveries…
          </p>
        )}

        {!loading && !error && rows.length === 0 && (
          <p className="py-md text-center text-metadata text-muted-foreground">
            No delivery attempts recorded{status !== 'all' ? ` with status “${status}”` : ''}.
          </p>
        )}

        {rows.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-metadata" style={{ tableLayout: 'fixed' }}>
              <colgroup>
                <col style={{ width: '13%' }} />
                <col style={{ width: '17%' }} />
                <col style={{ width: '13%' }} />
                <col style={{ width: '8%' }} />
                <col style={{ width: '17%' }} />
                <col style={{ width: '22%' }} />
                <col style={{ width: '10%' }} />
              </colgroup>
              <thead>
                <tr className="border-b border-border text-left text-caption text-muted-foreground">
                  <th className="py-xs pr-xs font-medium">Status</th>
                  <th className="py-xs pr-xs font-medium">Event</th>
                  <th className="py-xs pr-xs font-medium">Webhook</th>
                  <th className="py-xs pr-xs font-medium">Tries</th>
                  <th className="py-xs pr-xs font-medium">Created</th>
                  <th className="py-xs pr-xs font-medium">Last error</th>
                  <th className="py-xs font-medium" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const isFailed = (r.status || '').toLowerCase() === 'failed';
                  return (
                    <tr key={r.id} className="border-b border-border/50 align-top">
                      <td className="py-xs pr-xs">{statusBadge(r.status)}</td>
                      <td className="py-xs pr-xs">
                        <span className="block truncate font-mono text-caption" title={r.event}>
                          {safeFallback(r.event)}
                        </span>
                      </td>
                      <td className="py-xs pr-xs">
                        <span className="block truncate" title={r.webhook_name ?? undefined}>
                          {safeFallback(r.webhook_name)}
                        </span>
                      </td>
                      <td className="py-xs pr-xs whitespace-nowrap">
                        {r.attempts}/{r.max_attempts}
                        {r.response_status ? (
                          <span className="block text-caption text-muted-foreground">
                            HTTP {r.response_status}
                          </span>
                        ) : null}
                      </td>
                      <td className="py-xs pr-xs whitespace-nowrap text-muted-foreground">
                        {when(r.created_at)}
                        {r.delivered_at && (
                          <span className="block text-caption text-success">
                            ✓ {when(r.delivered_at)}
                          </span>
                        )}
                      </td>
                      <td className="py-xs pr-xs">
                        {r.last_error ? (
                          <span className="line-clamp-2 break-words text-destructive" title={r.last_error}>
                            {r.last_error}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="py-xs text-right">
                        {isFailed && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busyId === r.id}
                            onClick={() => void handleRetry(r)}
                          >
                            {busyId === r.id
                              ? <Loader2 className="size-3.5 animate-spin" aria-hidden />
                              : <RotateCw className="size-3.5" aria-hidden />}
                            Retry
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default WebhookDeliveries;
