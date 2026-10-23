/**
 * An upload row of the import history (components/scans/UploadHistoryRow,
 * 5.418.0) — what the Ingestion results view's rows did, moved onto the one
 * history (owner decision 2026-10-22, G15).  Ported from the deleted
 * `pages/IngestionResults*.test.tsx`: the row readable without opening it, a
 * file cut short (owner decision 2026-10-11), a partial import and Dismiss, a
 * staged file, the recorded parse error's detail, and the write controls
 * hidden below analyst.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useQuery } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => {
  const named: Record<string, ReturnType<typeof vi.fn>> = {};
  return new Proxy(named, {
    get(target, prop: string) {
      if (prop === '__esModule') return true;
      if (prop === 'then') return undefined;
      if (!(prop in target)) target[prop] = vi.fn().mockResolvedValue([]);
      return target[prop];
    },
    has: () => true,
  });
});
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
const who = vi.hoisted(() => ({ projectRole: 'analyst' as string }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: who.projectRole } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: () => false }),
}));
vi.mock('../../components/scans/ImportResult', () => ({ default: () => null }));

import UploadHistoryRow from '../../components/scans/UploadHistoryRow';
import { KEEP_TRUNCATED_EXPLANATION } from '../../components/scans/KeepTruncatedImport';
import { Table, TableBody } from '../../components/ui/table';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { IngestionResultItem } from '../../services/api';

const CUT = 'This whatweb JSON file was cut short: it ends after 28,253 complete records, before the end of the document.';

const row = (over: Record<string, unknown>) => ({
  id: 1, parse_error_id: null, original_filename: 'scan.xml', status: 'completed',
  file_size: 1024, tool_name: 'whatweb', scan_type: null, scan_id: null,
  created_at: '2026-10-11T10:00:00Z', started_at: null, completed_at: null,
  duration_seconds: 171, progress: null, stats: null, error: null, ...over,
}) as unknown as IngestionResultItem;

const cutShort = (over: Record<string, unknown> = {}) => row({
  id: 41, status: 'failed', original_filename: 'whatweb-all.json', parse_error_id: 7,
  file_retained: true, failure_kind: 'truncated_file', failure_reason: CUT,
  error: { error_type: 'truncated_file', error_message: CUT, user_message: `${CUT} Nothing from this file was imported.` },
  ...over,
});

/** A reader of the history, so a write's refresh of it can be seen. */
const HistoryReader = () => {
  useQuery({ queryKey: ['getImportHistory', 1], queryFn: () => api.getImportHistory(1) });
  return null;
};

const renderRows = (...items: IngestionResultItem[]) => render(
  <MemoryRouter>
    <TooltipProvider>
      <HistoryReader />
      <Table><TableBody>{items.map((it) => <UploadHistoryRow key={it.id} upload={it} />)}</TableBody></Table>
    </TooltipProvider>
  </MemoryRouter>,
);
const expand = (filename: string) => {
  const tableRow = screen.getByText(filename).closest('tr')!;
  fireEvent.click(within(tableRow).getByRole('button', { name: `Show details of ${filename}` }));
  return tableRow;
};

beforeEach(() => {
  vi.clearAllMocks();
  who.projectRole = 'analyst';
  api.getImportHistory.mockResolvedValue({ items: [], total: 0, batch_total: 0, scan_total: 0, has_more: false });
  api.retryIngestionJob.mockResolvedValue({ job_id: 41, status: 'queued', message: 'Job re-queued' });
});

describe('an upload row, readable without opening it', () => {
  it('says why a failed row was not imported, and an expired or discarded one says what happened', () => {
    const failed = (id: number, message: string) => row({
      id, status: 'failed', original_filename: `f${id}.xml`, dismissed_at: '2026-09-20T10:00:00Z',
      error: { error_type: null, error_message: message, user_message: message },
    });
    renderRows(
      failed(1, 'Staged upload expired: not started within 24 hours.'),
      failed(2, 'Discarded before import'),
      failed(3, "Failed to parse the file 'nikto-all.txt'.\nTraceback: …"),
    );
    expect(screen.getAllByTestId('failure-reason').map((el) => el.textContent)).toEqual([
      'Not started within 24 hours of upload; the file was removed',
      'Discarded before import',
      "Failed to parse the file 'nikto-all.txt'.",
    ]);
    const badge = (name: string) => within(screen.getByText(name).closest('tr')!).getAllByText(/^(expired|discarded|failed)$/)[0];
    expect(badge('f1.xml')).toHaveTextContent('expired');
    expect(badge('f2.xml')).toHaveTextContent('discarded');
    expect(badge('f3.xml')).toHaveTextContent('failed');
    expect(within(screen.getByText('f1.xml').closest('tr')!).getByText('dismissed')).toBeInTheDocument();
  });

  it('gives the parser\'s cause, and says when a later upload imported the same file', () => {
    renderRows(row({
      id: 478, status: 'failed', original_filename: 'smbmap-samba.txt', superseded_by_job_id: 484,
      failure_reason: 'SMBMap parser found 0 hosts in smbmap-samba.txt; file is empty or not smbmap output.',
      error: { error_type: 'parsing_error', error_message: 'x', user_message: 'The file format may not be supported.' },
    }));
    const sup = screen.getByTestId('superseded-by');
    expect(sup).toHaveTextContent('Superseded — imported by upload #484');
    expect(within(sup).getByRole('link', { name: 'upload #484' })).toHaveAttribute('href', '/scans?job_id=484');
    expect(screen.getByTestId('failure-reason')).toHaveTextContent('SMBMap parser found 0 hosts');
    expect(screen.getByTestId('failure-reason')).not.toHaveTextContent(/format may not be supported/);
  });

  it('shows the whole filename, breaking only after a separator, and one case for tools', () => {
    renderRows(row({ id: 5, original_filename: 'netexec-spider-172.30.77.10.json', tool_name: 'Nessus' }));
    const cell = screen.getByText('netexec-spider-172.30.77.10.json');
    expect(cell.className).not.toMatch(/truncate|break-all/);
    expect(cell).toHaveAttribute('title', 'netexec-spider-172.30.77.10.json');
    expect(cell.innerHTML).toBe('netexec-<wbr>spider-<wbr>172.<wbr>30.<wbr>77.<wbr>10.<wbr>json');
    expect(screen.getByText('nessus')).toBeInTheDocument();
    expect(screen.queryByText('Nessus')).toBeNull();
  });

  it('shows "<0.1s" for a sub-100 ms import, and figures only when the file had any', () => {
    renderRows(
      row({ id: 5, original_filename: 'names.txt', duration_seconds: 0.03,
        stats: { hosts_parsed: 0, hosts_up: 0, ports_found: 0, open_ports: 0, services_detected: 0 } }),
      row({ id: 6, original_filename: 'sweep.xml', duration_seconds: 2,
        stats: { hosts_parsed: 4, hosts_up: 3, ports_found: 10, open_ports: 6, services_detected: 5 } }),
    );
    const noPorts = screen.getByText('names.txt').closest('tr')!;
    expect(noPorts).toHaveTextContent('took <0.1s');
    expect(noPorts).not.toHaveTextContent(/0\/0/);
    expect(screen.getByText('sweep.xml').closest('tr')!).toHaveTextContent('3/4 up · 6/10 open');
  });
});

describe('an upload row — a file that was cut short', () => {
  it('says what happened and offers to import what the file holds, beside the retry', async () => {
    renderRows(cutShort());
    expect(within(screen.getByText('whatweb-all.json').closest('tr')!).getByTestId('failure-reason')).toHaveTextContent(CUT);
    expand('whatweb-all.json');
    expect(await screen.findByText('The file was cut short — nothing from it was imported')).toBeInTheDocument();
    expect(screen.queryByText(/Error type:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Nothing from this file was imported\./)).not.toBeInTheDocument();
    expect(screen.getByText(/A retry reads the same file and fails the same way/)).toBeInTheDocument();
    const offer = screen.getByTestId('keep-truncated-import');
    expect(within(offer).getByRole('button', { name: 'Import what whatweb-all.json holds' }))
      .toHaveTextContent('Import what the file holds');
    expect(within(offer).getByText(KEEP_TRUNCATED_EXPLANATION)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review format and retry' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
  });

  it('the button is the answer: it re-queues the job with the instruction and re-reads the history', async () => {
    renderRows(cutShort());
    await waitFor(() => expect(api.getImportHistory).toHaveBeenCalledTimes(1));
    expand('whatweb-all.json');
    fireEvent.click(await screen.findByRole('button', { name: 'Import what whatweb-all.json holds' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(api.retryIngestionJob).toHaveBeenCalledWith(1, 41, { keepTruncated: true }));
    await waitFor(() => expect(api.getImportHistory.mock.calls.length).toBeGreaterThan(1));
  });

  it('follows the kind, not the wording: an invalid file is never offered', async () => {
    renderRows(cutShort({
      id: 43, original_filename: 'first-record.json', failure_kind: 'parsing_error',
      error: { error_type: 'parsing_error', error_message: 'cut short before its first record', user_message: '' },
    }));
    expand('first-record.json');
    expect(await screen.findByText('Error type: parsing_error')).toBeInTheDocument();
    expect(screen.queryByTestId('keep-truncated-import')).not.toBeInTheDocument();
  });

  it('offers nothing once the uploaded file is no longer retained', async () => {
    renderRows(cutShort({ file_retained: false }));
    expand('whatweb-all.json');
    expect(await screen.findByText('The file was cut short — nothing from it was imported')).toBeInTheDocument();
    expect(screen.queryByTestId('keep-truncated-import')).not.toBeInTheDocument();
    expect(screen.getByText(/File no longer retained/)).toBeInTheDocument();
  });

  it.each(['viewer', 'auditor'])('hides every write from a project %s (hidden, not disabled)', async (role) => {
    who.projectRole = role;
    renderRows(cutShort());
    expand('whatweb-all.json');
    expect(await screen.findByText('The file was cut short — nothing from it was imported')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Import what/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Review format and retry' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
  });
});

describe('an upload row — a partial import', () => {
  const partial = row({
    id: 9, original_filename: '23-nmap-truncated.xml', partial: true, skipped_count: 0,
    parser_warnings: 'Incomplete XML — hosts after this point are MISSING, not down.',
  });

  it('reads as partial, says what was lost, and is dismissed here; the history is read again', async () => {
    api.dismissIngestionJob.mockResolvedValue({});
    renderRows(partial);
    const tableRow = screen.getByText('23-nmap-truncated.xml').closest('tr')!;
    expect(within(tableRow).getByText('completed')).toBeInTheDocument();
    expect(within(tableRow).getByText('partial')).toBeInTheDocument();
    await waitFor(() => expect(api.getImportHistory).toHaveBeenCalledTimes(1));
    expand('23-nmap-truncated.xml');
    expect(await screen.findByText(/hosts after this point are MISSING/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(api.dismissIngestionJob).toHaveBeenCalledWith(1, 9));
    await waitFor(() => expect(api.getImportHistory.mock.calls.length).toBeGreaterThan(1));
  });

  it('a dismissed one says so and offers no second dismiss; a clean import has nothing to dismiss', async () => {
    renderRows(
      row({ ...partial, dismissed_at: '2026-09-19T12:00:00Z' }),
      row({ id: 3, original_filename: 'fine.xml' }),
    );
    expand('23-nmap-truncated.xml');
    expect(await screen.findByText(/no longer listed as blocked/)).toBeInTheDocument();
    expand('fine.xml');
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
  });
});

describe('an upload row — a staged file', () => {
  const staged = row({ id: 4, original_filename: 'waiting.xml', status: 'staged', file_retained: true });

  it('says why a Discard failed instead of doing nothing (R34)', async () => {
    api.discardIngestionJob.mockRejectedValue({ response: { status: 409, data: { detail: 'It was started meanwhile.' } } });
    renderRows(staged);
    expand('waiting.xml');
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('It was started meanwhile.'));
  });

  it('shows an auditor the row without its actions (R32)', async () => {
    who.projectRole = 'auditor';
    renderRows(staged);
    expand('waiting.xml');
    await screen.findByText(/Stored but not imported/);
    expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Review format and import/ })).toBeNull();
  });
});

describe('an upload row — the recorded parse error', () => {
  const detail = (over: Record<string, unknown> = {}) => ({
    id: 77, filename: 'file-5.xml', file_type: 'xml', file_size: 2048, error_type: 'XMLSyntaxError',
    error_message: 'not well-formed (line 3)', error_details: null, file_preview: null,
    user_message: 'The file is not valid XML.', status: 'unresolved', created_at: '2026-09-19T10:00:00Z', updated_at: null,
    ...over,
  });
  const failed = (id: number, over: Record<string, unknown> = {}) => row({
    id, original_filename: `file-${id}.xml`, status: 'failed', error: { error_message: `bad ${id}` }, ...over,
  });

  it('asks for the row\'s parse error — not the job\'s number — and opens on what the server returned', async () => {
    api.getParseError.mockResolvedValue(detail());
    renderRows(failed(5, { parse_error_id: 77 }));
    expand('file-5.xml');
    expect(api.getParseError).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: 'View details' }));
    const dialog = await screen.findByRole('dialog');
    expect(api.getParseError.mock.calls[0].slice(0, 2)).toEqual([1, 77]);
    expect(within(dialog).getByText('not well-formed (line 3)')).toBeInTheDocument();
    expect(within(dialog).getByText('The file is not valid XML.')).toBeInTheDocument();
  });

  it('a lookup that fails is said and opens nothing; asked again, it is looked up again', async () => {
    api.getParseError.mockRejectedValue({ response: { status: 404, data: { detail: 'Parse error not found' } } });
    renderRows(failed(5, { parse_error_id: 77 }));
    expand('file-5.xml');
    fireEvent.click(await screen.findByRole('button', { name: 'View details' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Parse error not found', { id: 'pe-detail-5' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    api.getParseError.mockResolvedValue(detail());
    fireEvent.click(screen.getByRole('button', { name: 'View details' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(api.getParseError).toHaveBeenCalledTimes(2);
  });

  it('a row with no recorded parse error offers no detail to look up', () => {
    renderRows(failed(6));
    expand('file-6.xml');
    expect(screen.queryByRole('button', { name: 'View details' })).toBeNull();
    expect(api.getParseError).not.toHaveBeenCalled();
  });
});
