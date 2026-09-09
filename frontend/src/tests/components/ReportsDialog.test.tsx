import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import ReportsDialog from '../../components/ReportsDialog';
import * as api from '../../services/api';

vi.mock('../../hooks/useVisibilityPoll', () => ({ useVisibilityPoll: vi.fn() }));
vi.mock('../../services/api', () => ({
  generateHostsReport: vi.fn(),
  enqueueReportJob: vi.fn(),
  getReportJob: vi.fn(),
  downloadReportJob: vi.fn(),
  listReportJobs: vi.fn().mockResolvedValue([]),
  getReportLimits: vi.fn().mockResolvedValue({
    in_memory_host_cap: 2000,
    streamed_host_cap: 50000,
    per_format: { csv: null, html: 50000, json: 2000, 'markdown-bundle': 2000, 'agent-package': 2000 },
  }),
  dismissReportJob: vi.fn(),
  retryReportJob: vi.fn(),
  cancelReportJob: vi.fn(),
}));

describe('ReportsDialog — async report jobs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Review 2026-09-09 UX#1 — the cap shown must be the server's effective cap
  // for the SELECTED format, never a hardcoded constant.
  it('warns with the per-format cap from /reports/limits', async () => {
    render(<ReportsDialog open onClose={vi.fn()} filters={{}} totalHosts={3000} />);
    await waitFor(() => expect(api.getReportLimits).toHaveBeenCalled());
    // Default selection is comprehensive/HTML: 3,000 < 50,000 → no warning …
    await waitFor(() => expect(screen.queryByText(/includes the first/)).toBeNull());
    // … but the zip bundles are over their in-memory cap, so the note shows.
    expect(screen.getByText(/\.zip bundles below include the first 2,000/)).toBeInTheDocument();
  });

  it('shows no cap number until limits have loaded', async () => {
    (api.getReportLimits as unknown as ReturnType<typeof vi.fn>).mockReturnValueOnce(new Promise(() => {}));
    render(<ReportsDialog open onClose={vi.fn()} filters={{}} totalHosts={999999} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText(/includes the first/)).toBeNull();
  });

  // Review 2026-09-09 Ops-1 — a worker job must not hold the dialog hostage:
  // enqueue, show it in the tray, let the operator continue working, and
  // offer Download when it completes.
  it('enqueues a heavy format, shows it queued in the tray, and "Continue working" closes the dialog', async () => {
    const queued = { id: 7, status: 'queued', format: 'markdown-bundle', report_type: 'comprehensive', truncated: false, created_at: 'x' };
    (api.enqueueReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(queued);
    (api.listReportJobs as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([queued]);
    const onClose = vi.fn();

    render(<ReportsDialog open onClose={onClose} filters={{ state: 'up' }} totalHosts={10} />);
    fireEvent.click(screen.getByRole('button', { name: /markdown bundle/i }));

    await waitFor(() => expect(api.enqueueReportJob).toHaveBeenCalledWith('markdown-bundle', { state: 'up' }, undefined));
    // Tray row + inline "running" panel, no download attempt, dialog still open.
    await waitFor(() => expect(screen.getByTestId('tracked-job-running')).toBeInTheDocument());
    expect(screen.getAllByText('Recent reports').length).toBeGreaterThan(0);
    expect(api.downloadReportJob).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // The export buttons are usable again — nothing is "busy" client-side.
    expect(screen.getByRole('button', { name: /generate html/i })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: /continue working/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('reopening shows the persisted job and offers Download once it completed', async () => {
    const done = { id: 9, status: 'completed', format: 'agent-package', report_type: 'comprehensive', truncated: false, created_at: 'x' };
    (api.listReportJobs as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([done]);
    (api.downloadReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ truncated: false });

    const { rerender } = render(<ReportsDialog open={false} onClose={vi.fn()} filters={{}} totalHosts={5} />);
    expect(screen.queryByText('Recent reports')).toBeNull();
    rerender(<ReportsDialog open onClose={vi.fn()} filters={{}} totalHosts={5} />);
    // The tray comes from the API, so the job is there without any client state.
    await waitFor(() => expect(screen.getByText('Recent reports')).toBeInTheDocument());
    const download = screen.getByRole('button', { name: /^download$/i });
    fireEvent.click(download);
    await waitFor(() => expect(api.downloadReportJob).toHaveBeenCalledWith(9));
  });

  it('offers an inline Download when the tracked job completes while the dialog is open', async () => {
    const queued = { id: 11, status: 'queued', format: 'markdown-bundle', report_type: 'comprehensive', truncated: false, created_at: 'x' };
    const done = { ...queued, status: 'completed' };
    (api.enqueueReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(queued);
    const list = api.listReportJobs as unknown as ReturnType<typeof vi.fn>;
    list.mockResolvedValue([queued]);
    (api.downloadReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ truncated: false });
    const onClose = vi.fn();

    render(<ReportsDialog open onClose={onClose} filters={{}} totalHosts={5} />);
    fireEvent.click(screen.getByRole('button', { name: /markdown bundle/i }));
    await waitFor(() => expect(screen.getByTestId('tracked-job-running')).toBeInTheDocument());

    // Next tray refresh (what the poll would do) reports completion.
    list.mockResolvedValue([done]);
    const poll = (await import('../../hooks/useVisibilityPoll')).useVisibilityPoll as unknown as ReturnType<typeof vi.fn>;
    const lastCallback = poll.mock.calls[poll.mock.calls.length - 1][0] as () => Promise<void>;
    await lastCallback();

    await waitFor(() => expect(screen.getByTestId('tracked-job-ready')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('tracked-job-ready').querySelector('button')!);
    await waitFor(() => expect(api.downloadReportJob).toHaveBeenCalledWith(11));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('surfaces a failed report job', async () => {
    const failed = { id: 8, status: 'failed', format: 'agent-package', report_type: 'comprehensive', truncated: false, error_message: 'render exploded', created_at: 'x' };
    (api.enqueueReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(failed);
    (api.listReportJobs as unknown as ReturnType<typeof vi.fn>).mockResolvedValue([failed]);
    const onClose = vi.fn();

    render(<ReportsDialog open onClose={onClose} filters={{}} totalHosts={5} />);
    fireEvent.click(screen.getByRole('button', { name: /agent dataset/i }));

    await waitFor(() => expect(screen.getByTestId('tracked-job-failed')).toHaveTextContent(/render exploded/i));
    expect(api.downloadReportJob).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
