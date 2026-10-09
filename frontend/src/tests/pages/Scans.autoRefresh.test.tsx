/**
 * The Scans page's ingestion queue and its "Auto" switch (5.351.0).
 *
 * The switch used to be `LastUpdated`'s own timer calling the page's refresh.
 * It is now the page's queue reads polling (`pollEvery`): every 15 s while it
 * is on, every 5 s while a job is queued or processing whatever its position,
 * and not at all otherwise.  Off when the page opens.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Everything the page (and the components it mounts) takes from the barrel;
// unlisted functions resolve to an empty list.
const api = vi.hoisted(() => {
  const named: Record<string, ReturnType<typeof vi.fn>> = {};
  return new Proxy(named, {
    get(target, prop: string) {
      if (prop === '__esModule') return true;
      if (prop === 'then') return undefined;
      if (!(prop in target)) target[prop] = vi.fn().mockResolvedValue([]);
      return target[prop];
    },
    has: () => true,
  });
});
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo' } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'admin' }, hasPermission: () => true }),
}));

import Scans from '../../pages/Scans';
import { TooltipProvider } from '../../components/ui/tooltip';

const job = (status: string) => ({
  id: 71, status, original_filename: 'sweep.xml', created_at: '2026-09-19T10:00:00Z', message: null,
});

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/scans']}>
      <TooltipProvider><Scans /></TooltipProvider>
    </MemoryRouter>,
  );

const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

describe('Scans — the ingestion queue re-reads itself', () => {
  // shouldAdvanceTime keeps findBy / waitFor usable; the cadence (seconds) is
  // driven explicitly below.
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.clearAllMocks();
    api.getStagedIngestionJobs.mockResolvedValue([]);
    api.getScansSummary.mockResolvedValue({ total_scans: 0, total_hosts: 0, up_hosts: 0, open_services: 0, tool_counts: {} });
    api.getScanInventoryMarker.mockResolvedValue({ count: 0, latest_id: null });
    api.getImportHistory.mockResolvedValue({ items: [], total: 0, batch_total: 0, scan_total: 0, has_more: false });
  });
  afterEach(() => { vi.useRealTimers(); });

  it('shows when the queue was read, a refresh button and the Auto switch, off', async () => {
    api.getRecentIngestionJobs.mockResolvedValue([job('failed')]);
    renderPage();
    const queue = await screen.findByTestId('ingestion-queue');
    expect(queue).toHaveTextContent('Updated just now');
    expect(within(queue).getByRole('button', { name: 'Refresh ingestion jobs' })).toBeInTheDocument();
    expect(within(queue).getByRole('switch', { name: 'Auto' })).not.toBeChecked();
  });

  it('reads the queue every 15 s only while Auto is on; turning it off stops the re-reads', async () => {
    api.getRecentIngestionJobs.mockResolvedValue([job('failed')]);
    renderPage();
    const queue = await screen.findByTestId('ingestion-queue');
    await waitFor(() => expect(api.getStagedIngestionJobs).toHaveBeenCalledTimes(1));
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(1);

    // Nothing is running and Auto is off: the queue is not read again.
    await pass(60_000);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(1);
    expect(api.getStagedIngestionJobs).toHaveBeenCalledTimes(1);

    const auto = within(queue).getByRole('switch', { name: 'Auto' });
    fireEvent.click(auto);
    expect(auto).toBeChecked();
    await pass(14_000);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(1);
    await pass(1_000);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(2);
    expect(api.getStagedIngestionJobs).toHaveBeenCalledTimes(2);
    await pass(15_000);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(3);
    expect(api.getStagedIngestionJobs).toHaveBeenCalledTimes(3);

    fireEvent.click(auto);
    expect(auto).not.toBeChecked();
    await pass(60_000);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(3);
    expect(api.getStagedIngestionJobs).toHaveBeenCalledTimes(3);
  });

  it('reads the queue every 5 s while a job is processing, Auto or not, and stops when it is done', async () => {
    api.getRecentIngestionJobs.mockResolvedValueOnce([job('processing')]);
    api.getRecentIngestionJobs.mockResolvedValueOnce([job('processing')]);
    api.getRecentIngestionJobs.mockResolvedValue([job('failed')]);
    renderPage();
    await screen.findByTestId('ingestion-queue');
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(1);

    await pass(5_000);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(2);
    await pass(5_000);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(3);
    // The third answer holds no running job: nothing polls any more.
    await pass(60_000);
    expect(api.getRecentIngestionJobs).toHaveBeenCalledTimes(3);
  });
});
