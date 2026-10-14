import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  getQueueMetrics: vi.fn(),
}));
const selectProject = vi.fn();
const projects = [{ id: 2, name: 'Eval — Scenarios' }, { id: 1, name: 'Default' }];
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ projects, currentProject: projects[1], selectProject }),
}));

import * as api from '../../services/api';
import QueueHealthCard from '../../components/QueueHealthCard';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const snapshot = (over: Partial<Record<string, number | null>> = {}) => ({
  queued: 0,
  processing: 0,
  failed: 0,
  stale_processing: 0,
  oldest_queued_age_seconds: 0,
  completed_last_hour: 0,
  avg_processing_seconds: null,
  stale_cutoff_seconds: 600,
  ...over,
});

const metrics = (ingestion = snapshot(), report = snapshot()) => ({
  generated_at: '2026-08-07T12:00:00Z',
  ingestion,
  report,
});

describe('QueueHealthCard', () => {
  beforeEach(() => vi.clearAllMocks());

  // v5.302.0 — production filled its disk and Postgres stopped; nothing said so.
  it('warns when disk space is low and says how to reclaim it', async () => {
    mocked.getQueueMetrics.mockResolvedValue({
      ...metrics(), disk: { total_bytes: 160 * 1024 ** 3, free_bytes: 4 * 1024 ** 3, low: true },
    });
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    expect(await screen.findByText('Disk space low — 4.0 GB free of 160 GB')).toBeInTheDocument();
    expect(screen.getByText(/docker builder prune -f/)).toBeInTheDocument();
  });

  // v5.389.0 — the host and each process.  A worker is killed at ITS limit
  // whatever the host has free, so it is judged by its peak.
  it('says what the host, the API, each worker and the database are using', async () => {
    const GB = 1024 ** 3;
    mocked.getQueueMetrics.mockResolvedValue({
      ...metrics(),
      host: {
        memory_total_bytes: 64 * GB, memory_available_bytes: 55 * GB, memory_low: false,
        load_1: 1.5, load_5: 0.75, load_15: 0.5, cpu_count: 32, load_high: false,
      },
      backend_memory: { current_bytes: 0.9 * GB, peak_bytes: 1.2 * GB, limit_bytes: 2 * GB, near_limit: false },
      workers: [
        { name: 'ingestion_jobs', current_bytes: 1 * GB, peak_bytes: 3.9 * GB, limit_bytes: 4 * GB, near_limit: true, age_seconds: 3, stale: false },
        { name: 'report_jobs', current_bytes: 0.2 * GB, peak_bytes: 0.4 * GB, limit_bytes: 2 * GB, near_limit: false, age_seconds: 400, stale: true },
      ],
      database: { size_bytes: 1.7 * GB, connections: 14, max_connections: 120, connections_high: false },
    });
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);

    expect(await screen.findByText('Host memory — 55 GB available of 64 GB')).toBeInTheDocument();
    expect(screen.getByText('Host load — 1.50 · 0.75 · 0.50 (1, 5, 15 min) on 32 CPUs')).toBeInTheDocument();
    expect(screen.getByText('API memory — peaked at 1.2 GB of a 2.0 GB limit, 0.9 GB now')).toBeInTheDocument();
    // Plenty of host memory, and the worker is still about to be killed.
    expect(screen.getByText('Ingestion worker memory near its limit — peaked at 3.9 GB of a 4.0 GB limit, 1.0 GB now')).toBeInTheDocument();
    expect(screen.getByText(/Raise WORKER_MEM_LIMIT in \.env/)).toBeInTheDocument();
    // A worker that stopped reporting is said to be silent, never shown as calm.
    expect(screen.getByText(/^Report worker is not reporting — last heard 7m ago/)).toBeInTheDocument();
    expect(screen.getByText('Database — 1.7 GB, 14 of 120 connections in use')).toBeInTheDocument();
  });

  it('warns about low host memory, high load and connections nearly used up — and says nothing for what was not read', async () => {
    const GB = 1024 ** 3;
    mocked.getQueueMetrics.mockResolvedValue({
      ...metrics(),
      host: {
        memory_total_bytes: 16 * GB, memory_available_bytes: 1 * GB, memory_low: true,
        load_1: 9, load_5: 8.25, load_15: 3, cpu_count: 4, load_high: true,
      },
      backend_memory: null,
      workers: [],
      database: { size_bytes: 40 * GB, connections: 110, max_connections: 120, connections_high: true },
    });
    const first = render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    expect(await screen.findByText('Host memory low — 1.0 GB available of 16 GB')).toBeInTheDocument();
    expect(screen.getByText(/^Host load high — 9\.00 · 8\.25 · 3\.00/)).toBeInTheDocument();
    expect(screen.getByText('Database connections nearly used up — 40 GB, 110 of 120 connections in use')).toBeInTheDocument();
    expect(screen.queryByText(/API memory/)).toBeNull();
    expect(screen.queryByText(/worker memory|not reporting/)).toBeNull();
    first.unmount();

    // An older server, or a host that exposes none of it: the queues and nothing else.
    mocked.getQueueMetrics.mockResolvedValue({ ...metrics(), host: null, database: null });
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    await screen.findByText(/Checked /);
    expect(screen.queryByText(/Host memory|Host load|Database/)).toBeNull();
  });

  it('shows the free space quietly when it is fine', async () => {
    mocked.getQueueMetrics.mockResolvedValue({
      ...metrics(), disk: { total_bytes: 160 * 1024 ** 3, free_bytes: 38 * 1024 ** 3, low: false },
    });
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    expect(await screen.findByText('Disk — 38 GB free of 160 GB')).toBeInTheDocument();
    expect(screen.queryByText(/Disk space low/)).toBeNull();
  });

  it('reports healthy queues without raising an alarm', async () => {
    mocked.getQueueMetrics.mockResolvedValue(metrics());
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/Scan ingestion idle/)).toBeInTheDocument());
    expect(screen.queryByText(/stuck in flight/)).toBeNull();
  });

  // The signal this card exists for: a worker died holding jobs.
  it('escalates stale in-flight jobs and names the diagnostic step', async () => {
    mocked.getQueueMetrics.mockResolvedValue(
      metrics(snapshot({ processing: 3, stale_processing: 3 })),
    );
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    await waitFor(() =>
      expect(screen.getByText(/3 scan ingestion jobs stuck in flight/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/docker compose logs worker/)).toBeInTheDocument();
  });

  // A deep queue that is draining is just a busy queue — don't cry wolf.
  it('does not warn about a backlog that is still fresh', async () => {
    mocked.getQueueMetrics.mockResolvedValue(
      metrics(snapshot({ queued: 40, processing: 1, oldest_queued_age_seconds: 30 })),
    );
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/Scan ingestion healthy/)).toBeInTheDocument());
    expect(screen.queryByText(/not draining/)).toBeNull();
  });

  it('warns when the oldest queued job has been waiting too long', async () => {
    mocked.getQueueMetrics.mockResolvedValue(
      metrics(snapshot({ queued: 2, oldest_queued_age_seconds: 3600 })),
    );
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    await waitFor(() =>
      expect(screen.getByText(/Scan ingestion backlog not draining/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/oldest for 1\.0h/)).toBeInTheDocument();
  });

  it('surfaces a load failure instead of rendering an empty card', async () => {
    mocked.getQueueMetrics.mockRejectedValue(new Error('boom'));
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);
    await waitFor(() =>
      expect(screen.getByText(/Could not load queue metrics/)).toBeInTheDocument(),
    );
  });

  // A card that says "review and dismiss these" and offers only Refresh is a
  // dead end; the point is to turn monitoring into a recovery step.
  it('links failed ingestion jobs to a filtered view', async () => {
    mocked.getQueueMetrics.mockResolvedValue(metrics(snapshot({ failed: 4 })));
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);

    const link = await screen.findByRole('link', { name: /Review failed jobs/ });
    expect(link).toHaveAttribute('href', '/scans?view=results&status=failed');
  });

  // The queue is deployment-wide; Ingestion Results lists ONE project's jobs.
  // "78 failed … Review failed jobs →" landed on the current project's list,
  // which could hold none of them — so name each project and go to it.
  it('links each project holding failed jobs, switching project when needed', async () => {
    mocked.getQueueMetrics.mockResolvedValue(
      metrics({
        ...snapshot({ failed: 78 }),
        failed_by_project: [
          { project_id: 2, project_name: 'Eval — Scenarios', count: 60 },
          { project_id: 1, project_name: 'Default', count: 18 },
        ],
      } as never),
    );
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);

    // v5.288.0 — lower case mid-sentence (was "78 failed Scan ingestion jobs").
    await screen.findByText('78 failed scan ingestion jobs');
    const current = screen.getByRole('link', { name: /Review 18 in Default/ });
    expect(current).toHaveAttribute('href', '/scans?view=results&status=failed');
    fireEvent.click(screen.getByRole('button', { name: /Review 60 in Eval — Scenarios/ }));
    expect(selectProject).toHaveBeenCalledWith(projects[0]);
  });

  // Report jobs surface only inside the Reports dialog (no route), so linking
  // somewhere useless would be worse than not linking.
  it('does not fabricate a destination for the report queue', async () => {
    mocked.getQueueMetrics.mockResolvedValue(metrics(snapshot(), snapshot({ failed: 2 })));
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);

    await screen.findByText('2 failed report jobs');
    expect(screen.queryByRole('link')).toBeNull();
  });
});
