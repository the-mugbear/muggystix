import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import FormatRetryDialog from '../../components/scans/FormatRetryDialog';

const api = vi.hoisted(() => ({
  getJobDetection: vi.fn(),
  startIngestionJob: vi.fn(),
  reprocessIngestionJob: vi.fn(),
  getUploadFormats: vi.fn(),
  // The dialog shares suggestion/basis helpers with the review hook, whose
  // module takes the rest of its defaults from the barrel.
  uploadFile: vi.fn(),
  createScanBatch: vi.fn(),
  discardIngestionJob: vi.fn(),
  renameScanBatch: vi.fn(),
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
    // A filename-only match is a suggestion: nothing is preselected and the
    // action waits for the operator.  It used to go out as the override
    // although nobody had chosen it.
    expect(screen.getByLabelText('Parse as')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Re-process' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm suggested format' }));
    expect(screen.getByLabelText('Parse as')).toHaveValue('naabu_output');
    fireEvent.click(screen.getByRole('button', { name: 'Re-process' }));
    await waitFor(() => expect(api.reprocessIngestionJob).toHaveBeenCalledWith(9, { formatOverride: 'naabu_output', sourceTool: null }));
    expect(toast.success).toHaveBeenCalledWith(expect.stringContaining('job #42'), expect.anything());
  });

  it('a confident detection needs no choice: detection decides, no override is sent', async () => {
    api.getJobDetection.mockResolvedValue({
      ...detection,
      candidates: [{ file_type: 'naabu_output', label: 'Naabu host:port text', basis: 'structure', rank: 0 }],
      needs_choice: false,
      reason: null,
    });
    render(<FormatRetryDialog open onOpenChange={() => {}} jobId={9} filename="results.txt" mode="retry" onDone={() => {}} />);
    await waitFor(() => expect(screen.getByText(/recognised by structure/)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Confirm suggested format' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry import' }));
    await waitFor(() => expect(api.startIngestionJob).toHaveBeenCalledWith(9, { formatOverride: null, sourceTool: null }));
  });

  it('fallback candidates are not suggested and not called a detection', async () => {
    api.getJobDetection.mockResolvedValue({
      ...detection,
      candidates: [{ file_type: 'nmap_xml', label: 'Nmap XML', basis: 'fallback', rank: 0 }],
      primary: 'nmap_xml',
      reason: 'No distinctive signature was recognised.',
    });
    render(<FormatRetryDialog open onOpenChange={() => {}} jobId={9} filename="inventory.xml" mode="start" onDone={() => {}} />);
    await waitFor(() => expect(screen.getByText(/No distinctive signature/)).toBeInTheDocument());
    expect(screen.queryByText(/Detected:/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Confirm suggested format' })).not.toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Nmap XML (not recognised)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled();
  });

  it('a failed inspection can be retried, and a format still chosen by hand', async () => {
    api.getJobDetection.mockRejectedValueOnce(new Error('boom'));
    api.getUploadFormats.mockResolvedValue(detection.formats);
    render(<FormatRetryDialog open onOpenChange={() => {}} jobId={9} filename="results.txt" mode="retry" onDone={() => {}} />);
    const select = await screen.findByLabelText('Parse as');
    expect(screen.getByRole('button', { name: 'Retry import' })).toBeDisabled();
    fireEvent.change(select, { target: { value: 'amass_output' } });
    expect(screen.getByRole('button', { name: 'Retry import' })).not.toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Retry inspection' }));
    await waitFor(() => expect(screen.getByText(/Recognised from the filename only/)).toBeInTheDocument());
    expect(api.getJobDetection).toHaveBeenCalledTimes(2);
  });

  it('a file that is no longer retained is reported, not silently re-uploaded', async () => {
    api.getJobDetection.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'The uploaded file is no longer on disk — re-upload it.' } } });
    render(<FormatRetryDialog open onOpenChange={() => {}} jobId={9} filename="results.txt" mode="retry" onDone={() => {}} />);
    await waitFor(() => expect(screen.getByText(/no longer on disk/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Retry import' })).toBeDisabled();
    // The bytes are gone: no format choice or re-inspection can help.
    expect(api.getUploadFormats).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Retry inspection' })).not.toBeInTheDocument();
  });
});
