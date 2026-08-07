/**
 * QueueHealthCard — deployment worker health (admin only).
 *
 * The backend has published queue depth, stale in-flight counts, failed
 * backlog, and throughput for a long time, but nothing consumed it: an
 * operator learned a worker had died by noticing their uploads never
 * finished. This is that signal, made visible.
 *
 * Deliberately verdict-first rather than a metrics dump — every row states
 * whether the queue is healthy and, when it isn't, what to do about it.
 * Raw counts appear only where they change the operator's next action.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react';

import { getQueueMetrics, type QueueMetrics, type QueueSnapshot } from '../services/api';
import { formatApiError } from '../utils/apiErrors';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { InlineLoader } from './ui/inline-loader';
import { cn } from '../utils/cn';

type Verdict = {
  tone: 'ok' | 'warn' | 'bad';
  headline: string;
  /** What the operator should actually do. Empty when healthy. */
  action?: string;
};

const formatAge = (seconds: number): string => {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
};

/**
 * Turn a snapshot into a judgement. Ordered by severity so the most
 * actionable problem is the one surfaced.
 */
const assess = (q: QueueSnapshot, label: string): Verdict => {
  if (q.stale_processing > 0) {
    return {
      tone: 'bad',
      headline: `${q.stale_processing} ${label} job${q.stale_processing === 1 ? '' : 's'} stuck in flight`,
      action:
        `A worker took these and stopped reporting progress (past the ${formatAge(q.stale_cutoff_seconds)} ` +
        `cutoff). The reaper will requeue them — if the count keeps growing, the worker is crash-looping: ` +
        `check "docker compose logs worker".`,
    };
  }
  // A backlog only matters if it is also going stale; a deep queue that is
  // draining is just a busy queue.
  if (q.queued > 0 && q.oldest_queued_age_seconds > 900) {
    return {
      tone: 'warn',
      headline: `${label} backlog not draining`,
      action:
        `${q.queued} job${q.queued === 1 ? '' : 's'} waiting, oldest for ` +
        `${formatAge(q.oldest_queued_age_seconds)}. Confirm the worker container is up and not wedged.`,
    };
  }
  if (q.failed > 0) {
    return {
      tone: 'warn',
      headline: `${q.failed} failed ${label} job${q.failed === 1 ? '' : 's'}`,
      action:
        'These exhausted their retries. Review and dismiss them so the queue view reflects live work.',
    };
  }
  return {
    tone: 'ok',
    headline:
      q.processing > 0
        ? `${label} healthy — ${q.processing} in flight`
        : q.completed_last_hour > 0
          ? `${label} healthy — ${q.completed_last_hour} completed in the last hour`
          : `${label} idle`,
  };
};

const VerdictRow: React.FC<{ verdict: Verdict }> = ({ verdict }) => {
  const Icon = verdict.tone === 'ok' ? CheckCircle2 : AlertTriangle;
  return (
    <div className="flex items-start gap-xs">
      <Icon
        className={cn(
          'mt-px size-4 shrink-0',
          verdict.tone === 'ok' && 'text-success',
          verdict.tone === 'warn' && 'text-warning',
          verdict.tone === 'bad' && 'text-destructive',
        )}
        aria-hidden
      />
      <div className="min-w-0">
        <p className="text-metadata font-semibold text-foreground">{verdict.headline}</p>
        {verdict.action && (
          <p className="text-caption text-muted-foreground">{verdict.action}</p>
        )}
      </div>
    </div>
  );
};

export const QueueHealthCard: React.FC = () => {
  const [metrics, setMetrics] = useState<QueueMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setMetrics(await getQueueMetrics());
    } catch (err) {
      setError(formatApiError(err, 'Could not load queue metrics.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Card className="mb-md">
      <CardHeader className="flex flex-row items-center justify-between gap-xs">
        <CardTitle>Worker Queues</CardTitle>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => load()}
          aria-label="Refresh queue metrics"
          disabled={loading}
        >
          <RefreshCw className={cn('size-4', loading && 'animate-spin')} aria-hidden />
        </Button>
      </CardHeader>
      <CardContent>
        {loading && !metrics ? (
          <InlineLoader label="Checking worker queues…" />
        ) : error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : metrics ? (
          <div className="flex flex-col gap-sm">
            <VerdictRow verdict={assess(metrics.ingestion, 'Scan ingestion')} />
            <VerdictRow verdict={assess(metrics.report, 'Report')} />
            <p className="text-caption text-muted-foreground">
              Checked {new Date(metrics.generated_at).toLocaleTimeString()}
            </p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
};

export default QueueHealthCard;
