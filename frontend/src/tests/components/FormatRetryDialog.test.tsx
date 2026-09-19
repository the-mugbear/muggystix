import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import FormatRetryDialog from '../../components/scans/FormatRetryDialog';

const api = vi.hoisted(() => ({
  getJobDetection: vi.fn(),
  startIngestionJob: vi.fn(),
  reprocessIngestionJob: vi.fn(),
}));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

const detection = {
  job_id: 9,
  filename: 'results.txt',
  candidates: [{ file_type: 'naabu_output', label: 'Naabu host:port text', basis: 'filename', rank: 0 }],
  primary: 'naabu_output',
  needs_choice: true,
  reason: 'Recognised from the filename only; the content did not confirm it.',
  preview: { raw: '10.0.0.5 443', sample: ['10.0.0.5 443'] },
  formats: [
    { file_type: 'naabu_output', label: 'Naabu host:port text', family: 'port' },
    { file_type: 'amass_output', label: 'Hostname list (Amass / Subfinder text)', family: 'dns' },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getJobDetection.mockResolvedValue(detection);
  api.startIngestionJob.mockResolvedValue({ id: 9, status: 'queued' });
  api.reprocessIngestionJob.mockResolvedValue({ id: 42, status: 'queued' });
});

describe('FormatRetryDialog', () => {
  it('retry: shows the detection, lets the operator change the format, and restarts the job in place', async () => {
    const onDone = vi.fn();
    render(<FormatRetryDialog open onOpenChange={() => {}} jobId={9} filename="results.txt" mode="retry" onDone={onDone} />);
    await waitFor(() => expect(screen.getByText(/Recognised from the filename only/)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Parse as'), { target: { value: 'amass_output' } });
    fireEvent.change(screen.getByLabelText('Source tool (optional)'), { target: { value: 'subfinder' } });
    fireEvent.click(screen.getByRole('button', { name: 'Retry import' }));
    await waitFor(() => expect(api.startIngestionJob).toHaveBeenCalledWith(9, { formatOverride: 'amass_output', sourceTool: 'subfinder' }));
    expect(api.reprocessIngestionJob).not.toHaveBeenCalled();
    expect(onDone).toHaveBeenCalled();
  });

  it('re-process: says what it does before doing it, then creates a new job', async () => {
    render(<FormatRetryDialog open onOpenChange={() => {}} jobId={9} filename="results.txt" mode="reprocess" priorScanId={5} onDone={() => {}} />);
    expect(screen.getByText(/new scan record/)).toBeInTheDocument();
    expect(screen.getByText(/prior scan \(#5\)/)).toBeInTheDocument();
    expect(screen.getByText(/duplicate guard is bypassed/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('Parse as')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Re-process' }));
    await waitFor(() => expect(api.reprocessIngestionJob).toHaveBeenCalledWith(9, { formatOverride: 'naabu_output', sourceTool: null }));
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('job #42'), expect.anything());
  });

  it('a file that is no longer retained is reported, not silently re-uploaded', async () => {
    api.getJobDetection.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'The uploaded file is no longer on disk — re-upload it.' } } });
    render(<FormatRetryDialog open onOpenChange={() => {}} jobId={9} filename="results.txt" mode="retry" onDone={() => {}} />);
    await waitFor(() => expect(screen.getByText(/no longer on disk/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Retry import' })).toBeDisabled();
  });
});
