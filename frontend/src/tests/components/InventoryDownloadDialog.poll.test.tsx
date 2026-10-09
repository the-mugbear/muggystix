import { render, screen, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// The poll on the clock, on purpose: a swallowed refresh failure would defeat
// its backoff, and only the cadence of the requests shows that.
vi.mock('../../services/api', () => ({
  downloadInventoryCsv: vi.fn(),
  enqueueInventoryJson: vi.fn(),
  listReportJobs: vi.fn(),
  downloadReportJob: vi.fn(),
  retryReportJob: vi.fn(),
  cancelReportJob: vi.fn(),
  dismissReportJob: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import * as api from '../../services/api';
import InventoryDownloadDialog from '../../components/InventoryDownloadDialog';

const running = { id: 1, project_id: 1, status: 'processing', format: 'json', report_type: 'comprehensive', created_at: '2026-10-07T10:00:00Z' };

describe('InventoryDownloadDialog job polling', () => {
  // shouldAdvanceTime keeps waitFor/findBy usable; the poll cadence (seconds)
  // is still driven explicitly below.
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it('backs off after a failed refresh and tells the user the status may be stale', async () => {
    const list = api.listReportJobs as unknown as ReturnType<typeof vi.fn>;
    list.mockResolvedValueOnce([running]); // initial load on open
    list.mockRejectedValue(new Error('503'));

    render(<InventoryDownloadDialog open onClose={vi.fn()} filters={{}} totalHosts={5} />);
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
    await screen.findByTestId('inventory-job-1');

    // First poll tick at 2.5s → rejects.
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(list).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/status may be stale/i));
    // The list still shows the job (stale beats blank).
    expect(screen.getByTestId('inventory-job-1')).toHaveTextContent('Preparing');

    // Backoff: the next tick must NOT come at +2.5s; it comes at +5s.
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(list).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(list).toHaveBeenCalledTimes(3);
  });
});
