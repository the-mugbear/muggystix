import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../services/api', () => ({ getScans: vi.fn(), getBatchUnimportedJobs: vi.fn() }));

import { ScanBatchRow } from '../../components/scans/ScanBatchList';
import { Table, TableBody } from '../../components/ui/table';
import { getBatchUnimportedJobs, getScans } from '../../services/api';
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
    (getBatchUnimportedJobs as Mock).mockReset();
    (getBatchUnimportedJobs as Mock).mockResolvedValue([]);
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
    // No created_at on this fixture: the first file's upload stands in.
    expect(when).toHaveTextContent(`${formatInstant(new Date('2026-09-11T10:00:00Z'), TZ)}uploaded`);
    // Screenshot 2026-09-23: "uploaded; first file …" read as nonsense.
    expect(when).not.toHaveTextContent(/first file/);
    expect(newHosts).toHaveTextContent('+850');
    expect(newHosts).toHaveTextContent('of 900 unique seen');
    expect(contributed).toHaveTextContent('312 files imported');
    expect(contributed).toHaveTextContent('120 port observations');
    expect(within(actions).getByRole('button', { name: /show the files of/i })).toBeInTheDocument();
    // One table, one date format: never toLocaleString's "9/11/2026, …".
    expect(when.textContent).not.toMatch(/\d+\/\d+\/\d{4}/);
  });

  // Demo — Insights Eval, 2026-09-24: "Upload batch · 31 files" over "32
  // files imported · incl. 1 re-processed" read as a contradiction.
  it('counts a re-processed file beside the batch\'s own files, so the title and the count agree', () => {
    const { container } = renderRow({}, vi.fn(), {
      ...batch, recon_session_id: null, label: '31 files · x', files: 32, total_files: 32, reprocessed_files: 1,
    });
    expect(screen.getByText('Upload batch · 31 files')).toBeInTheDocument();
    const contributed = cells(container)[3];
    expect(contributed).toHaveTextContent('31 files imported + 1 re-processed');
    expect(contributed).not.toHaveTextContent(/32 files/);
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

    expect(screen.getByText('of 1 host seen')).toBeInTheDocument();
    expect(screen.getByText('0 open ports')).toBeInTheDocument();
    expect(screen.getAllByText('of 2 hosts seen')).toHaveLength(2);
    expect(screen.getByTitle('This tool does not report open ports')).toHaveTextContent('—');
    expect(screen.getByText('1 open port')).toBeInTheDocument();
    expect(screen.queryByText(/\+0/)).not.toBeInTheDocument();
  });

  // Screenshot 2026-09-23: the expanded files were a header-less list whose
  // figures did not sit under the table's columns.
  it("lays each expanded file out in the parent table's five columns", async () => {
    (getScans as Mock).mockResolvedValue([
      {
        id: 31, filename: 'chunk-009.xml', tool_name: 'nmap', scan_type: null, created_at: '2026-09-11T11:00:00Z',
        total_hosts: 4, up_hosts: 4, new_hosts: 3, updated_hosts: 1, total_ports: 2, open_ports: 2,
      },
    ]);
    const { container } = renderRow({});
    fireEvent.click(screen.getByRole('button', { name: /show the files of/i }));
    await screen.findByText('chunk-009.xml');
    const fileRow = container.querySelector('tr[data-batch-file]') as HTMLTableRowElement;
    const [scan, when, newHosts, contributed, actions, ...rest] = Array.from(fileRow.querySelectorAll('td'));
    expect(rest).toHaveLength(0);
    expect(scan).toHaveTextContent('chunk-009.xml');
    expect(scan).toHaveTextContent('nmap');
    expect(when).toHaveTextContent(formatInstant(new Date('2026-09-11T11:00:00Z'), TZ));
    expect(newHosts).toHaveTextContent('+3');
    expect(newHosts).toHaveTextContent('of 4 hosts seen');
    expect(contributed).toHaveTextContent('2 open ports');
    expect(actions).toBeEmptyDOMElement();
  });

  // Local Network, 2026-09-24: an expanded batch listed only its imported
  // files, so which of its files failed — and why — was nowhere on the page.
  it('lists the files that did not import, with what happened and why', async () => {
    (getScans as Mock).mockResolvedValue([]);
    (getBatchUnimportedJobs as Mock).mockResolvedValue([
      {
        id: 478, filename: 's', original_filename: 'smbmap-samba.txt', status: 'failed',
        created_at: '2026-09-11T10:00:20Z', superseded_by_job_id: 484,
        error_message: "Failed to parse the file 'smbmap-samba.txt'. The file format may not be supported or the file may be corrupted.",
        failure_reason: 'SMBMap parser found 0 hosts in smbmap-samba.txt; file is empty or not smbmap output.',
      },
      {
        id: 468, filename: 'n', original_filename: 'nikto-all.txt', status: 'failed',
        created_at: '2026-09-11T10:30:00Z', superseded_by_job_id: null,
        failure_reason: 'value too long for type character varying(200)',
      },
      {
        id: 408, filename: 'e', original_filename: 'eyewitness.zip', status: 'failed',
        created_at: '2026-09-11T10:00:00Z', dismissed_at: '2026-09-11T10:05:00Z',
        error_message: 'Discarded before import', failure_reason: 'Discarded before import',
      },
    ]);
    const { container } = renderRow({}, vi.fn(), { ...batch, created_at: '2026-09-11T10:00:00Z', superseded_files: 1 });
    // The batch row says how many failed but were imported later.
    expect(screen.getByRole('link', { name: '1 failed, imported later (superseded)' }))
      .toHaveAttribute('href', '/parse-errors?status=superseded');
    fireEvent.click(screen.getByRole('button', { name: /show the files of/i }));
    await screen.findByText('smbmap-samba.txt');
    expect(getBatchUnimportedJobs).toHaveBeenCalledWith(7);

    const rows = Array.from(container.querySelectorAll('tr[data-batch-job]'));
    expect(rows).toHaveLength(3);
    const smb = rows[0].querySelectorAll('td');
    expect(smb[3]).toHaveTextContent('Superseded — imported by job #484');
    expect(within(smb[3] as HTMLElement).getByRole('link', { name: 'imported by job #484' }))
      .toHaveAttribute('href', '/parse-errors?job_id=484');
    // The parser's cause, not the generic sentence.
    expect(smb[3]).toHaveTextContent('SMBMap parser found 0 hosts');
    expect(smb[3]).not.toHaveTextContent(/format may not be supported/);
    // Uploaded with the batch (20 s later): no time repeated on the row.
    expect(smb[1]).toHaveTextContent('—');
    expect(smb[1]).not.toHaveTextContent(formatInstant(new Date('2026-09-11T10:00:20Z'), TZ));

    const nikto = rows[1].querySelectorAll('td');
    expect(nikto[3]).toHaveTextContent('Failed');
    expect(nikto[3]).toHaveTextContent('value too long');
    // Half an hour after the batch: its own time.
    expect(nikto[1]).toHaveTextContent(formatInstant(new Date('2026-09-11T10:30:00Z'), TZ));

    expect(rows[2].querySelectorAll('td')[3]).toHaveTextContent('Discarded before import');
  });

  it('shows a generated label as "Upload batch · N files", and a name as written', () => {
    const op = { ...batch, recon_session_id: null, created_by: 'admin', created_by_name: 'Administrator Account' };
    const { unmount } = renderRow({}, vi.fn(), { ...op, label: '31 files · 9/18/2026, 10:20:37 PM' });
    expect(screen.getByText('Upload batch · 31 files')).toBeInTheDocument();
    // The subtitle wraps (never an ellipsis over the uploader's name) and does
    // not repeat "Upload batch".
    const by = screen.getByText('Uploaded by Administrator Account');
    expect(by.className).not.toMatch(/truncate/);
    expect(screen.queryByText(/9\/18\/2026/)).not.toBeInTheDocument();
    unmount();
    renderRow({}, vi.fn(), { ...op, label: '12 files uploaded · Sep 23, 2026, 10:08 PM UTC' });
    expect(screen.getByText('Upload batch · 12 files')).toBeInTheDocument();
    unmount();
    renderRow({}, vi.fn(), { ...op, label: 'DMZ sweep, week 2' });
    expect(screen.getByText('DMZ sweep, week 2')).toBeInTheDocument();
    expect(screen.getByText('Upload batch · Uploaded by Administrator Account')).toBeInTheDocument();
  });

  it('says when a re-processed file joined the batch, on its own line', () => {
    const { container } = renderRow({}, vi.fn(), {
      ...batch, created_at: '2026-09-18T22:20:00Z', first_uploaded: '2026-09-18T22:21:00Z',
      last_uploaded: '2026-09-23T22:08:00Z', reprocessed_files: 1,
    });
    const when = cells(container)[1];
    expect(when).toHaveTextContent(`${formatInstant(new Date('2026-09-18T22:20:00Z'), TZ)}uploaded`);
    expect(when).toHaveTextContent(`re-processed ${formatInstant(new Date('2026-09-23T22:08:00Z'), TZ)}`);
  });

  // Local Network, 2026-09-23: "46 files · …" read "17 files imported · 4
  // failed" — 25 files unexplained.  They were refused at upload
  // (duplicates), which creates no job; every file must be accounted for.
  it('accounts for every dropped file, including the ones refused at upload', () => {
    const { container } = renderRow({}, vi.fn(), {
      ...batch, recon_session_id: null, label: '46 files · 9/22/2026, 10:37:38 PM',
      files: 17, total_files: 17, pending_files: 0, processing_files: 0, failed_files: 4, uploaded_files: 21,
    });
    const contributed = cells(container)[3];
    expect(contributed).toHaveTextContent('17 files imported');
    expect(contributed).toHaveTextContent('4 failed');
    expect(contributed).toHaveTextContent('25 refused at upload');
  });
});
