import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../services/api', () => ({ getScans: vi.fn() }));

import ScanBatchList from '../../components/scans/ScanBatchList';
import { getScans } from '../../services/api';
import type { ScanBatchSummary } from '../../services/api';

// An agent sweep of a large scope arrives as hundreds of chunk files; the
// Scans page shows each sweep as one row that expands to its files.
const batch: ScanBatchSummary = {
  id: 7,
  label: 'nmap-tcp-top1000',
  recon_session_id: 3,
  created_by: null,
  files: 312,
  tools: ['nmap'],
  hosts: 900,
  new_hosts: 850,
  open_ports: 120,
  first_uploaded: '2026-09-11T10:00:00Z',
  last_uploaded: '2026-09-11T12:00:00Z',
  pending_files: 4,
  failed_files: 1,
};

describe('ScanBatchList', () => {
  beforeEach(() => {
    (getScans as Mock).mockReset();
  });

  it('shows one row per batch with what its files added and what is still landing', () => {
    render(<ScanBatchList batches={[batch]} filters={{}} onViewScan={vi.fn()} />);
    expect(screen.getByText('nmap-tcp-top1000')).toBeInTheDocument();
    expect(screen.getByText('Recon session #3')).toBeInTheDocument();
    expect(screen.getByText('312')).toBeInTheDocument();
    expect(screen.getByText('+850 new')).toBeInTheDocument();
    expect(screen.getByText('4 still parsing')).toBeInTheDocument();
    expect(screen.getByText('1 failed')).toBeInTheDocument();
    expect(getScans).not.toHaveBeenCalled();
  });

  it("lists a batch's files on expand, scoped to the page filters", async () => {
    (getScans as Mock).mockResolvedValue([
      {
        id: 11, filename: 'chunk-001.xml', tool_name: 'nmap', scan_type: null,
        created_at: '2026-09-11T10:00:00Z', total_hosts: 250, up_hosts: 250,
        new_hosts: 240, updated_hosts: 10, total_ports: 12, open_ports: 12,
      },
    ]);
    const onViewScan = vi.fn();
    render(<ScanBatchList batches={[batch]} filters={{ tool: 'NMAP' }} onViewScan={onViewScan} />);

    fireEvent.click(screen.getByRole('button', { name: /show the files of nmap-tcp-top1000/i }));
    const file = await screen.findByText('chunk-001.xml');
    expect(getScans).toHaveBeenCalledWith(0, 500, expect.objectContaining({ batchId: 7, tool: 'NMAP' }));

    fireEvent.click(file);
    expect(onViewScan).toHaveBeenCalledWith(11);
  });
});
