import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import ReportsDialog from '../../components/ReportsDialog';
import * as api from '../../services/api';

vi.mock('../../services/api', () => ({
  generateHostsReport: vi.fn(),
  enqueueReportJob: vi.fn(),
  getReportJob: vi.fn(),
  downloadReportJob: vi.fn(),
}));

describe('ReportsDialog — async report jobs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('enqueues a job for a heavy format and downloads on completion', async () => {
    (api.enqueueReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 7, status: 'completed', format: 'pdf', truncated: false,
    });
    (api.downloadReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ truncated: false });
    const onClose = vi.fn();

    render(<ReportsDialog open onClose={onClose} filters={{ state: 'up' }} totalHosts={10} />);
    // Default format is PDF — the heavy (async) path.
    fireEvent.click(screen.getByRole('button', { name: /generate pdf/i }));

    await waitFor(() =>
      expect(api.enqueueReportJob).toHaveBeenCalledWith('pdf', { state: 'up' }, 'comprehensive'),
    );
    await waitFor(() => expect(api.downloadReportJob).toHaveBeenCalledWith(7));
    // Sync path must NOT be used for PDF.
    expect(api.generateHostsReport).not.toHaveBeenCalled();
    // A complete download closes the dialog.
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('surfaces a failed report job', async () => {
    (api.enqueueReportJob as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 8, status: 'failed', format: 'pdf', truncated: false, error_message: 'render exploded',
    });
    const onClose = vi.fn();

    render(<ReportsDialog open onClose={onClose} filters={{}} totalHosts={5} />);
    fireEvent.click(screen.getByRole('button', { name: /generate pdf/i }));

    await waitFor(() => expect(screen.getByText(/render exploded/i)).toBeInTheDocument());
    expect(api.downloadReportJob).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
