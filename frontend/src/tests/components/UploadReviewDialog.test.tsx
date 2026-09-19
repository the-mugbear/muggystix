import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  uploadFile: vi.fn(),
  getJobDetection: vi.fn(),
  startIngestionJob: vi.fn(),
  createScanBatch: vi.fn(),
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
