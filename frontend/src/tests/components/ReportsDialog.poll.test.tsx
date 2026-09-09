import React from 'react';
import { render, screen, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Real polling hook here on purpose: the unit suite mocks it, so it could not
// see that a swallowed refresh failure defeats the hook's backoff.
vi.mock('../../services/api', () => ({
  generateHostsReport: vi.fn(),
  enqueueReportJob: vi.fn(),
  listReportJobs: vi.fn(),
  getReportJob: vi.fn(),
  downloadReportJob: vi.fn(),
  retryReportJob: vi.fn(),
  cancelReportJob: vi.fn(),
  dismissReportJob: vi.fn(),
  getReportLimits: vi.fn().mockResolvedValue({ in_memory_host_cap: 2000, streamed_host_cap: 50000, per_format: {} }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import * as api from '../../services/api';
import ReportsDialog from '../../components/ReportsDialog';

const running = { id: 1, status: 'processing', format: 'json', report_type: 'comprehensive', truncated: false, created_at: 'x' };

describe('ReportsDialog tray polling (real useVisibilityPoll)', () => {
  // shouldAdvanceTime keeps waitFor/findBy usable; the poll cadence (seconds)
  // is still driven explicitly below.
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it('backs off after a failed refresh and tells the user the status may be stale', async () => {
    const list = api.listReportJobs as unknown as ReturnType<typeof vi.fn>;
    list.mockResolvedValueOnce([running]); // initial load on open
    list.mockRejectedValue(new Error('503'));

    render(<ReportsDialog open onClose={vi.fn()} filters={{}} totalHosts={5} />);
    await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getAllByText(/comprehensive/i).length).toBeGreaterThan(0));

    // First poll tick at 2.5s → rejects.
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(list).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/status may be stale/i));
    // The tray still shows the job (stale beats blank).
    expect(screen.getAllByText(/comprehensive/i).length).toBeGreaterThan(0);

    // Backoff: the next tick must NOT come at +2.5s; it comes at +5s.
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(list).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
    expect(list).toHaveBeenCalledTimes(3);
  });
});
