import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import ReportsDialog from '../../components/ReportsDialog';
import * as api from '../../services/api';

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

  it('enqueues a job for a heavy (zip) format and downloads on completion', async () => {
    (api.enqueueReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 7, status: 'completed', format: 'markdown-bundle', truncated: false,
    });
    (api.downloadReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ truncated: false });
    const onClose = vi.fn();

    render(<ReportsDialog open onClose={onClose} filters={{ state: 'up' }} totalHosts={10} />);
    // The markdown-bundle button takes the async path.
    fireEvent.click(screen.getByRole('button', { name: /markdown bundle/i }));

    await waitFor(() => expect(api.enqueueReportJob).toHaveBeenCalled());
    expect(api.enqueueReportJob).toHaveBeenCalledWith('markdown-bundle', { state: 'up' }, undefined);
    await waitFor(() => expect(api.downloadReportJob).toHaveBeenCalledWith(7));
    expect(api.generateHostsReport).not.toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('surfaces a failed report job', async () => {
    (api.enqueueReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 8, status: 'failed', format: 'agent-package', truncated: false, error_message: 'render exploded',
    });
    const onClose = vi.fn();

    render(<ReportsDialog open onClose={onClose} filters={{}} totalHosts={5} />);
    fireEvent.click(screen.getByRole('button', { name: /agent dataset/i }));

    await waitFor(() => expect(screen.getByText(/render exploded/i)).toBeInTheDocument());
    expect(api.downloadReportJob).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
