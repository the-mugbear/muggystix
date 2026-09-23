import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

vi.mock('../../services/api', () => ({ getScans: vi.fn() }));

import { ScanBatchRow } from '../../components/scans/ScanBatchList';
import { Table, TableBody } from '../../components/ui/table';
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

// The batch is a group row inside the import history table (v5.239.0).
const renderRow = (filters: { tool?: string }, onViewScan = vi.fn()) =>
  render(
    <Table><TableBody>
      <ScanBatchRow batch={batch} filters={filters} onViewScan={onViewScan} colSpan={6} />
    </TableBody></Table>,
  );

describe('ScanBatchRow', () => {
  beforeEach(() => {
    (getScans as Mock).mockReset();
  });

  it('shows one row per batch with what its files added and what is still landing', () => {
    renderRow({});
    expect(screen.getByText('nmap-tcp-top1000')).toBeInTheDocument();
    // Says what kind of row it is: it sits among single files now.
    expect(screen.getByText('Upload batch · Recon session #3')).toBeInTheDocument();
    expect(screen.getByText('312')).toBeInTheDocument();
    expect(screen.getByText('+850 new')).toBeInTheDocument();
    expect(screen.getByText('4 processing')).toBeInTheDocument();
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
    renderRow({ tool: 'NMAP' }, onViewScan);

    fireEvent.click(screen.getByRole('button', { name: /show the files of nmap-tcp-top1000/i }));
    const file = await screen.findByText('chunk-001.xml');
    expect(getScans).toHaveBeenCalledWith(0, 500, expect.objectContaining({ batchId: 7, tool: 'NMAP' }));

    fireEvent.click(file);
    expect(onViewScan).toHaveBeenCalledWith(11);
  });

  it('names the unit of every file figure, and gives no port count for a tool that reports none', async () => {
    // "1 hosts · +0 · 0 open" read the +0 as ports, and "0 open" on a web
    // tool as "found nothing".
    const row = { scan_type: null, created_at: '2026-09-11T10:00:00Z', up_hosts: 1, updated_hosts: 1, total_ports: 0 };
    (getScans as Mock).mockResolvedValue([
      { ...row, id: 21, filename: 'sweep.xml', tool_name: 'nmap', total_hosts: 1, new_hosts: 0, open_ports: 0 },
      { ...row, id: 22, filename: 'probe.jsonl', tool_name: 'httpx', total_hosts: 2, new_hosts: 1, open_ports: 0 },
      { ...row, id: 23, filename: 'deep.xml', tool_name: 'nmap', total_hosts: 2, new_hosts: 2, open_ports: 1 },
    ]);
    renderRow({});
    fireEvent.click(screen.getByRole('button', { name: /show the files of nmap-tcp-top1000/i }));
    await screen.findByText('sweep.xml');

    expect(screen.getByText('1 host · 0 new')).toBeInTheDocument();
    expect(screen.getByText('0 open ports')).toBeInTheDocument();
    expect(screen.getByText('2 hosts · 1 new')).toBeInTheDocument();
    expect(screen.getByTitle('This tool does not report open ports')).toHaveTextContent('—');
    expect(screen.getByText('1 open port')).toBeInTheDocument();
    expect(screen.queryByText(/\+0/)).not.toBeInTheDocument();
  });
});
