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
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react';

import {
  getQueueMetrics,
  type DatabaseSnapshot,
  type DiskSnapshot,
  type HostSnapshot,
  type ProcessMemory,
  type WorkerMemory,
  type FailedJobsInProject,
  type QueueSnapshot,
} from '../services/api';
import { useProject } from '../contexts/ProjectContext';
import { queryErrorText } from '../lib/query';
import PostureSection from './posture/PostureSection';
import { Alert, AlertDescription } from './ui/alert';
import { Button } from './ui/button';
import { InlineLoader } from './ui/inline-loader';
import { cn } from '../utils/cn';
import { importResultsPath } from '../utils/importResultsPath';

/** How many projects the failed-jobs breakdown names before "and N more". */
const MAX_FAILED_PROJECTS = 5;

/** Where a queue's actionable states can be inspected. Report jobs currently
 *  surface only inside the Reports dialog (no route), so that queue has no
 *  destinations — better to say nothing than to link somewhere useless. */
type QueueSurface = {
  queuedHref?: string;
  inFlightHref?: string;
  failedHref?: string;
};

const INGESTION_SURFACE: QueueSurface = {
  queuedHref: importResultsPath({ status: 'queued' }),
  inFlightHref: importResultsPath({ status: 'processing' }),
  failedHref: importResultsPath({ status: 'failed' }),
};
const REPORT_SURFACE: QueueSurface = {};

type Verdict = {
  tone: 'ok' | 'warn' | 'bad';
  headline: string;
  /** What the operator should actually do. Empty when healthy. */
  action?: string;
  /** Where to go and do it. A card that says "review and dismiss these" and
   *  then offers only a Refresh button is a dead end — the whole point is to
   *  turn monitoring into a recovery step. */
  href?: string;
  hrefLabel?: string;
  /** Where the failed jobs are. The queue is deployment-wide but Ingestion
   *  Results lists one project's jobs, so a single link to it showed only the
   *  current project's share — or none of them. */
  failedByProject?: FailedJobsInProject[];
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
const assess = (
  q: QueueSnapshot & { failed_by_project?: FailedJobsInProject[] },
  /** Lower-case queue name ("scan ingestion"); v5.288.0 — it is used
   *  mid-sentence ("4 failed scan ingestion jobs"), and capitalised only
   *  where it opens the headline. */
  label: string,
  surface: QueueSurface,
): Verdict => {
  const Label = label.charAt(0).toUpperCase() + label.slice(1);
  if (q.stale_processing > 0) {
    return {
      tone: 'bad',
      headline: `${q.stale_processing} ${label} job${q.stale_processing === 1 ? '' : 's'} stuck in flight`,
      action:
        `A worker took these and stopped reporting progress (past the ${formatAge(q.stale_cutoff_seconds)} ` +
        `cutoff). The reaper will requeue them — if the count keeps growing, the worker is crash-looping: ` +
        `check "docker compose logs worker".`,
      href: surface.inFlightHref,
      hrefLabel: 'View in-flight jobs',
    };
  }
  // A backlog only matters if it is also going stale; a deep queue that is
  // draining is just a busy queue.
  if (q.queued > 0 && q.oldest_queued_age_seconds > 900) {
    return {
      tone: 'warn',
      headline: `${Label} backlog not draining`,
      action:
        `${q.queued} job${q.queued === 1 ? '' : 's'} waiting, oldest for ` +
        `${formatAge(q.oldest_queued_age_seconds)}. Confirm the worker container is up and not wedged.`,
      href: surface.queuedHref,
      hrefLabel: 'View queued jobs',
    };
  }
  if (q.failed > 0) {
    return {
      tone: 'warn',
      headline: `${q.failed} failed ${label} job${q.failed === 1 ? '' : 's'}`,
      action:
        'These exhausted their retries. Review and dismiss them so the queue view reflects live work.',
      href: surface.failedHref,
      hrefLabel: 'Review failed jobs',
      failedByProject: surface.failedHref && q.failed_by_project?.length ? q.failed_by_project : undefined,
    };
  }
  return {
    tone: 'ok',
    headline:
      q.processing > 0
        ? `${Label} healthy — ${q.processing} in flight`
        : q.completed_last_hour > 0
          ? `${Label} healthy — ${q.completed_last_hour} completed in the last hour`
          : `${Label} idle`,
  };
};

const gb = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(bytes < 10 * 1024 ** 3 ? 1 : 0)} GB`;

/**
 * v5.302.0 — production filled its disk (2026-09-24) and Postgres stopped
 * ("No space left on device") with nothing in the app having said so.
 */
export const assessDisk = (disk: DiskSnapshot): Verdict => (disk.low
  ? {
    tone: 'bad',
    headline: `Disk space low — ${gb(disk.free_bytes)} free of ${gb(disk.total_bytes)}`,
    action:
      'A full disk stops the database. On the host: "docker builder prune -f" and "docker image prune -f" '
      + 'reclaim old build layers and images (running containers, volumes and rollback images are kept); '
      + 'lowering INGESTION_RETAIN_FILES_DAYS keeps fewer uploaded files.',
  }
  : { tone: 'ok', headline: `Disk — ${gb(disk.free_bytes)} free of ${gb(disk.total_bytes)}` });

/*
 * v5.389.0 — the host and each process, for "is it slow because the box is
 * short of something".  A line is said only for what the server could read;
 * each warning says what to change.
 */
const WORKER_LABEL: Record<string, { label: string; limit: string }> = {
  ingestion_jobs: { label: 'Ingestion worker', limit: 'WORKER_MEM_LIMIT' },
  report_jobs: { label: 'Report worker', limit: 'REPORT_WORKER_MEM_LIMIT' },
};

/** The host's memory and load, one line each; nothing for what was not read. */
export const assessHost = (host: HostSnapshot): Verdict[] => {
  const out: Verdict[] = [];
  if (host.memory_total_bytes != null && host.memory_available_bytes != null) {
    const line = `${gb(host.memory_available_bytes)} available of ${gb(host.memory_total_bytes)}`;
    out.push(host.memory_low
      ? {
        tone: 'bad',
        headline: `Host memory low — ${line}`,
        action: 'Under a tenth is available: the kernel starts killing processes when it runs out. '
          + 'See what is using it on the host ("docker stats"), or give the host more memory.',
      }
      : { tone: 'ok', headline: `Host memory — ${line}` });
  }
  if (host.load_1 != null && host.load_5 != null && host.load_15 != null) {
    const cpus = host.cpu_count != null ? ` on ${host.cpu_count} CPU${host.cpu_count === 1 ? '' : 's'}` : '';
    const line = `${host.load_1.toFixed(2)} · ${host.load_5.toFixed(2)} · ${host.load_15.toFixed(2)} (1, 5, 15 min)${cpus}`;
    out.push(host.load_high
      ? {
        tone: 'warn',
        headline: `Host load high — ${line}`,
        action: 'More is waiting to run than there are CPUs, so everything is slower: a large import or a '
          + 'report render is the usual cause here, and it passes. Persistent load needs more CPUs.',
      }
      : { tone: 'ok', headline: `Host load — ${line}` });
  }
  return out;
};

const memoryLine = (m: ProcessMemory): string => {
  const limit = m.limit_bytes != null ? ` of a ${gb(m.limit_bytes)} limit` : ' (no limit set)';
  return m.peak_bytes != null
    ? `peaked at ${gb(m.peak_bytes)}${limit}, ${gb(m.current_bytes)} now`
    : `${gb(m.current_bytes)}${limit}`;
};

/** A process against ITS OWN limit: the kernel kills it there however much
 *  the host has free, so this is judged by the peak. */
export const assessProcessMemory = (m: ProcessMemory, label: string, limitSetting: string): Verdict => (m.near_limit
  ? {
    tone: 'bad',
    headline: `${label} memory near its limit — ${memoryLine(m)}`,
    action: `It is killed when it reaches the limit, whatever the host has free; a job it was running then `
      + `looks stalled and is started again. Raise ${limitSetting} in .env and deploy, or split a very large file.`,
  }
  : { tone: 'ok', headline: `${label} memory — ${memoryLine(m)}` });

export const assessWorker = (w: WorkerMemory): Verdict => {
  const known = WORKER_LABEL[w.name] ?? { label: w.name, limit: 'its memory limit' };
  if (w.stale) {
    return {
      tone: 'warn',
      headline: `${known.label} is not reporting — last heard ${formatAge(w.age_seconds)} ago`
        + ` (then: ${memoryLine(w)})`,
      action: 'It is stopped, was killed, or is stuck in one step. On the host: "docker compose ps" and '
        + '"docker compose logs" for that worker.',
    };
  }
  return assessProcessMemory(w, known.label, known.limit);
};

export const assessDatabase = (d: DatabaseSnapshot): Verdict => {
  const line = `${gb(d.size_bytes)}, ${d.connections} of ${d.max_connections} connections in use`;
  return d.connections_high
    ? {
      tone: 'warn',
      headline: `Database connections nearly used up — ${line}`,
      action: 'Requests wait, then fail, when none is left. Raise PG_MAX_CONNECTIONS in .env and deploy '
        + '(each connection uses memory on the database).',
    }
    : { tone: 'ok', headline: `Database — ${line}` };
};

const linkClass =
  'rounded text-caption text-info hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';

/** One link per project holding failed jobs: the current project's goes
 *  straight to its list; another project's switches to it first (the same
 *  selectProject path the header uses), since Ingestion Results is per project. */
const FailedByProject: React.FC<{ rows: FailedJobsInProject[]; href: string }> = ({ rows, href }) => {
  const { projects, currentProject, selectProject } = useProject();
  const navigate = useNavigate();
  const shown = rows.slice(0, MAX_FAILED_PROJECTS);
  const rest = rows.slice(MAX_FAILED_PROJECTS).reduce((n, r) => n + r.count, 0);
  return (
    <ul className="mt-xxs flex flex-col gap-xxs" aria-label="Failed jobs by project">
      {shown.map((row) => {
        const label = `${row.count} in ${row.project_name}`;
        const isCurrent = currentProject?.id === row.project_id;
        const project = projects.find((p) => p.id === row.project_id);
        return (
          <li key={row.project_id} className="min-w-0 truncate text-caption">
            {isCurrent ? (
              <Link to={href} className={linkClass} title={row.project_name}>
                Review {label} →
              </Link>
            ) : project ? (
              <button
                type="button"
                className={linkClass}
                title={`Switch to ${row.project_name} and review its failed jobs`}
                onClick={() => {
                  selectProject(project);
                  navigate(href);
                }}
              >
                Review {label} →
              </button>
            ) : (
              <span className="text-muted-foreground" title={row.project_name}>{label}</span>
            )}
          </li>
        );
      })}
      {rest > 0 && (
        <li className="text-caption text-muted-foreground">
          and {rest} more in {rows.length - shown.length} other project{rows.length - shown.length === 1 ? '' : 's'}
        </li>
      )}
    </ul>
  );
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
        {verdict.href && verdict.failedByProject ? (
          <FailedByProject rows={verdict.failedByProject} href={verdict.href} />
        ) : verdict.href ? (
          <Link to={verdict.href} className={cn('mt-xxs inline-block', linkClass)}>
            {verdict.hrefLabel ?? 'View'} →
          </Link>
        ) : null}
      </div>
    </div>
  );
};

export const QueueHealthCard: React.FC = () => {
  const query = useQuery({
    queryKey: ['getQueueMetrics'],
    queryFn: ({ signal }) => getQueueMetrics(signal),
  });
  const metrics = query.data ?? null;
  // Only the first read is "loading": a Refresh keeps the verdicts on screen
  // and the button usable, as before.
  const loading = query.isPending;
  const error = query.isFetching ? null : queryErrorText(query.error, 'Could not load queue metrics.');
  const load = () => { void query.refetch(); };

  return (
    <PostureSection
      title="Worker queues"
      description="Ingestion and report workers across the whole deployment, and what the host and each process are using."
      actions={
        <Button
          variant="ghost"
          size="icon"
          onClick={() => load()}
          aria-label="Refresh queue metrics"
          disabled={loading}
        >
          <RefreshCw className={cn('size-4', loading && 'animate-spin')} aria-hidden />
        </Button>
      }
    >
        {loading && !metrics ? (
          <InlineLoader label="Checking worker queues…" />
        ) : error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : metrics ? (
          <div className="flex flex-col gap-sm">
            <VerdictRow verdict={assess(metrics.ingestion, 'scan ingestion', INGESTION_SURFACE)} />
            <VerdictRow verdict={assess(metrics.report, 'report', REPORT_SURFACE)} />
            {metrics.disk && <VerdictRow verdict={assessDisk(metrics.disk)} />}
            {metrics.host && assessHost(metrics.host).map((v) => <VerdictRow key={v.headline} verdict={v} />)}
            {metrics.backend_memory && (
              <VerdictRow verdict={assessProcessMemory(metrics.backend_memory, 'API', 'BACKEND_MEM_LIMIT')} />
            )}
            {(metrics.workers ?? []).map((w) => <VerdictRow key={w.name} verdict={assessWorker(w)} />)}
            {metrics.database && <VerdictRow verdict={assessDatabase(metrics.database)} />}
            <p className="text-caption text-muted-foreground">
              Checked {new Date(metrics.generated_at).toLocaleTimeString()}
            </p>
          </div>
        ) : null}
    </PostureSection>
  );
};

export default QueueHealthCard;
