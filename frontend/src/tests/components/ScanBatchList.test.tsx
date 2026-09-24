import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../services/api', () => ({ getScans: vi.fn() }));

import { ScanBatchRow } from '../../components/scans/ScanBatchList';
import { Table, TableBody } from '../../components/ui/table';
import { getScans } from '../../services/api';
import type { ScanBatchSummary } from '../../services/api';
import { formatInstant } from '../../utils/scanTime';

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

const TZ = { timeZone: 'UTC', locale: 'en-US' };

// The batch is a group row inside the import history table (v5.239.0).
const renderRow = (filters: { tool?: string }, onViewScan = vi.fn(), b: ScanBatchSummary = batch) =>
  render(
    <MemoryRouter>
      <Table><TableBody>
        <ScanBatchRow batch={b} filters={filters} onViewScan={onViewScan} colSpan={5} timeFormat={TZ} />
      </TableBody></Table>
    </MemoryRouter>,
  );

/** The batch row's cells, in the table's column order. */
const cells = (container: HTMLElement) => {
  const row = container.querySelector('tr[data-batch-id]') as HTMLTableRowElement;
  return Array.from(row.querySelectorAll('td'));
};

describe('ScanBatchRow', () => {
  beforeEach(() => {
    (getScans as Mock).mockReset();
  });

  it('shows one row per batch with what its files added and what is still landing', () => {
    renderRow({});
    expect(screen.getByText('nmap-tcp-top1000')).toBeInTheDocument();
    // Says what kind of row it is: it sits among single files now.
    expect(screen.getByText('Upload batch · Recon session #3')).toBeInTheDocument();
    expect(screen.getByText(/312 files imported/)).toBeInTheDocument();
    expect(screen.getByText('+850')).toBeInTheDocument();
    expect(screen.getByText('4 processing')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '1 failed' })).toHaveAttribute('href', '/parse-errors?status=needs_attention');
    expect(getScans).not.toHaveBeenCalled();
  });

  // Screenshot 2026-09-23: one full-width cell put "32 files" under WHEN and
  // the times in a fifth block past the last header.
  it('fills the same five columns as a file row: Scan · When · New hosts · Contributed · Actions', () => {
    const { container } = renderRow({});
    const [scan, when, newHosts, contributed, actions, ...rest] = cells(container);
    expect(rest).toHaveLength(0);
    expect(scan).toHaveTextContent('nmap-tcp-top1000');
    expect(when).toHaveTextContent(formatInstant(new Date('2026-09-11T12:00:00Z'), TZ));
    expect(when).toHaveTextContent(`first file ${formatInstant(new Date('2026-09-11T10:00:00Z'), TZ)}`);
    expect(newHosts).toHaveTextContent('+850');
    expect(newHosts).toHaveTextContent('of 900 unique seen');
    expect(contributed).toHaveTextContent('312 files imported');
    expect(contributed).toHaveTextContent('120 port observations');
    expect(within(actions).getByRole('button', { name: /show the files of/i })).toBeInTheDocument();
    // One table, one date format: never toLocaleString's "9/11/2026, …".
    expect(when.textContent).not.toMatch(/\d+\/\d+\/\d{4}/);
  });

  it('says a re-processed file is why the batch holds more files than its name', () => {
    renderRow({}, vi.fn(), { ...batch, label: '31 files · x', files: 32, total_files: 32, reprocessed_files: 1 });
    expect(screen.getByText(/32 files imported/)).toBeInTheDocument();
    expect(screen.getByText(/incl\. 1 re-processed/)).toBeInTheDocument();
  });

  it('a batch with nothing imported says why', () => {
    const empty = {
      ...batch, label: '31 files · y', recon_session_id: null, files: 0, total_files: 0, hosts: 0, new_hosts: 0,
      open_ports: 0, tools: [], pending_files: 0, processing_files: 0, failed_files: 0, expired_files: 31,
    };
    const { container } = renderRow({}, vi.fn(), empty);
    const contributed = cells(container)[3];
    expect(contributed).toHaveTextContent('Nothing imported');
    expect(within(contributed).getByRole('link', { name: '31 expired before import' }))
      .toHaveAttribute('href', '/parse-errors?status=failed');
    expect(contributed).not.toHaveTextContent(/0 files/);
    expect(screen.queryByText(/reached the import/)).not.toBeInTheDocument();
  });

  it('shows at most four tool chips, then "+N more" with the full list on hover', () => {
    const tools = Array.from({ length: 22 }, (_, i) => `tool${String(i).padStart(2, '0')}`);
    const { container } = renderRow({}, vi.fn(), { ...batch, tools });
    const scan = cells(container)[0];
    expect(within(scan).getByText('tool03')).toBeInTheDocument();
    expect(within(scan).queryByText('tool04')).not.toBeInTheDocument();
    expect(within(scan).getByText('+18 more')).toBeInTheDocument();
    expect(scan.querySelector(`[title="${tools.join(', ')}"]`)).not.toBeNull();
  });

  it("names the uploader by full name, falling back to the username", () => {
    const op = { ...batch, recon_session_id: null, created_by: 'admin' };
    const { unmount } = renderRow({}, vi.fn(), { ...op, created_by_name: 'Ada Admin' });
    expect(screen.getByText('Upload batch · Uploaded by Ada Admin')).toBeInTheDocument();
    unmount();
    renderRow({}, vi.fn(), op);
    expect(screen.getByText('Upload batch · Uploaded by admin')).toBeInTheDocument();
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
