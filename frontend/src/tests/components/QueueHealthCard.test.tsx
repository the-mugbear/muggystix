import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  getQueueMetrics: vi.fn(),
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
      expect(screen.getByText(/3 Scan ingestion jobs stuck in flight/)).toBeInTheDocument(),
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
    expect(link).toHaveAttribute('href', '/parse-errors?status=failed');
  });

  // Report jobs surface only inside the Reports dialog (no route), so linking
  // somewhere useless would be worse than not linking.
  it('does not fabricate a destination for the report queue', async () => {
    mocked.getQueueMetrics.mockResolvedValue(metrics(snapshot(), snapshot({ failed: 2 })));
    render(<MemoryRouter><QueueHealthCard /></MemoryRouter>);

    await screen.findByText(/2 failed Report jobs/);
    expect(screen.queryByRole('link')).toBeNull();
  });
});
