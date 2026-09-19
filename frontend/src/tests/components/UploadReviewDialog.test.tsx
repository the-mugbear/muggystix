import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  uploadFile: vi.fn(),
  getJobDetection: vi.fn(),
  startIngestionJob: vi.fn(),
  createScanBatch: vi.fn(),
  discardIngestionJob: vi.fn(),
  getUploadFormats: vi.fn(),
}));
vi.mock('../../services/api', () => api);

import UploadReviewDialog from '../../components/scans/UploadReviewDialog';

const detectionFor = (jobId: number, sample: string) => ({
  job_id: jobId,
  filename: 'f',
  candidates: [{ file_type: 'nmap_xml', label: 'Nmap XML', basis: 'structure', rank: 0 }],
  primary: 'nmap_xml',
  needs_choice: false,
  reason: null,
  preview: { raw: `<nmaprun job="${jobId}">`, sample: [sample] },
  formats: [{ file_type: 'nmap_xml', label: 'Nmap XML', family: 'port' }],
});

beforeEach(() => {
  vi.clearAllMocks();
  let next = 1;
  api.createScanBatch.mockResolvedValue({ id: 3, label: 'b' });
  api.uploadFile.mockImplementation(async (f: File) => ({
    job_id: next++, filename: f.name, status: 'staged', message: 'staged', scan_id: null,
  }));
  api.getJobDetection.mockImplementation(async (jobId: number) => detectionFor(jobId, `host-of-job-${jobId}: 1 open port`));
});

const renderDialog = () =>
  render(
    <UploadReviewDialog
      open
      onOpenChange={() => {}}
      projectName="Demo"
      skipInformational={false}
      savingSkipInformational={false}
      onSkipInformationalChange={() => {}}
      onStarted={() => {}}
      onViewScan={() => {}}
    />,
  );

describe('UploadReviewDialog preview', () => {
  it('opens directly beneath the row it belongs to, not after the whole table', async () => {
    const { container } = renderDialog();
    const input = container.ownerDocument.querySelector('input[type="file"]') as HTMLInputElement;
    const files = ['a.xml', 'b.xml', 'c.xml'].map((n) => new File(['<nmaprun/>'], n, { type: 'text/xml' }));
    fireEvent.change(input, { target: { files } });

    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Preview' })).toHaveLength(3));

    // Preview the FIRST file of three.
    fireEvent.click(screen.getAllByRole('button', { name: 'Preview' })[0]);
    const heading = await screen.findByText('What the reader saw');
    const previewRow = heading.closest('tr') as HTMLTableRowElement;
    const fileRow = screen.getByText('a.xml').closest('tr') as HTMLTableRowElement;

    // The preview row is the file row's next sibling — above b.xml and c.xml.
    expect(fileRow.nextElementSibling).toBe(previewRow);
    expect(previewRow.nextElementSibling).toBe(screen.getByText('b.xml').closest('tr'));
    expect(screen.getByText('host-of-job-1: 1 open port')).toBeInTheDocument();
    expect(screen.getByLabelText('Raw start of a.xml')).toHaveTextContent('<nmaprun job="1">');

    // One preview at a time: opening another moves it under that row.
    fireEvent.click(screen.getAllByRole('button', { name: 'Preview' })[1]);
    await waitFor(() => expect(screen.getByText('host-of-job-3: 1 open port')).toBeInTheDocument());
    expect(screen.queryByText('host-of-job-1: 1 open port')).not.toBeInTheDocument();
    expect(screen.getByText('c.xml').closest('tr')!.nextElementSibling).toBe(
      screen.getByText('What the reader saw').closest('tr'),
    );
  });
});

const drop = (container: HTMLElement, names: string[]) => {
  const input = container.ownerDocument.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: names.map((n) => new File(['x'], n, { type: 'text/xml' })) } });
};

/** The page's wiring: the dialog asks to close, the page flips `open`. */
const Harness: React.FC<{ onOpenChange?: (v: boolean) => void }> = ({ onOpenChange }) => {
  const [open, setOpen] = React.useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Upload scans</button>
      <UploadReviewDialog
        open={open}
        onOpenChange={(v) => { onOpenChange?.(v); setOpen(v); }}
        projectName="Demo"
        skipInformational={false}
        savingSkipInformational={false}
        onSkipInformationalChange={() => {}}
        onStarted={() => {}}
        onViewScan={() => {}}
      />
    </>
  );
};

describe('UploadReviewDialog flow', () => {
  it('can be reopened after every file was imported', async () => {
    // Started rows used to stay in state, so `allStarted` was still true and
    // the dialog closed itself the instant it was opened again.
    api.startIngestionJob.mockResolvedValue({ id: 1, status: 'queued' });
    const onOpenChange = vi.fn();
    const { container } = render(<Harness onOpenChange={onOpenChange} />);
    drop(container, ['a.xml']);
    fireEvent.click(await screen.findByRole('button', { name: 'Import 1 ready file' }));
    await waitFor(() => expect(onOpenChange).toHaveBeenLastCalledWith(false));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    onOpenChange.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Upload scans' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    // Still open a tick later, with the finished row gone.
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.queryByText('a.xml')).not.toBeInTheDocument();
  });

  it('a filename-only match is suggested, and not ready until confirmed', async () => {
    api.getJobDetection.mockResolvedValue({
      ...detectionFor(1, 's'),
      candidates: [{ file_type: 'nmap_xml', label: 'Nmap XML', basis: 'filename', rank: 0 }],
      needs_choice: true,
      reason: 'Recognised from the filename only; the content did not confirm it.',
    });
    const { container } = renderDialog();
    drop(container, ['nmap-thing.xml']);
    expect(await screen.findByText('Needs a format')).toBeInTheDocument();
    expect(screen.queryByText('Format chosen')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import 0 ready files' })).toBeDisabled();
    expect(screen.getByLabelText('Format for nmap-thing.xml')).toHaveValue('');

    fireEvent.click(screen.getByRole('button', { name: 'Confirm suggested format' }));
    expect(await screen.findByText('Format chosen')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import 1 ready file' })).not.toBeDisabled();
  });

  it('a failed inspection offers a retry and still lets a format be chosen', async () => {
    api.getJobDetection.mockRejectedValueOnce(new Error('inspect failed'));
    api.getUploadFormats.mockResolvedValue([{ file_type: 'nmap_xml', label: 'Nmap XML', family: 'port' }]);
    const { container } = renderDialog();
    drop(container, ['a.xml']);
    // The selector used to render only with a detection: a dead end.
    const select = await screen.findByLabelText('Format for a.xml');
    await waitFor(() => expect(screen.getByRole('option', { name: 'Nmap XML' })).toBeInTheDocument());
    fireEvent.change(select, { target: { value: 'nmap_xml' } });
    expect(screen.getByRole('button', { name: 'Import 1 ready file' })).not.toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Retry inspection' }));
    await waitFor(() => expect(screen.getByText(/recognised by structure/)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Retry inspection' })).not.toBeInTheDocument();
  });

  it('removing a staged row discards the staged file on the server', async () => {
    api.discardIngestionJob.mockResolvedValue({ id: 1, status: 'failed' });
    const { container } = renderDialog();
    drop(container, ['a.xml']);
    fireEvent.click(await screen.findByRole('button', { name: 'Discard staged file a.xml' }));
    await waitFor(() => expect(api.discardIngestionJob).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.queryByText('a.xml')).not.toBeInTheDocument());
  });
});
