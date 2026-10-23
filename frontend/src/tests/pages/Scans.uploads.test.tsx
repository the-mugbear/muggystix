/**
 * ONE import history (5.418.0; owner decision 2026-10-22, G15, option A).
 *
 * The Scans page had two views of the same uploads — the import history and
 * Ingestion results (`?view=results`).  Now the uploads that were not imported
 * are rows of the history, in the server's one order; "Needs attention" and
 * the other status counts are its filter (each count is the server's and
 * opens exactly its list); a link to one upload lists that upload, open; and
 * an old `?view=results` address lands on the same filter, query kept.  A
 * viewer is given none of it: no upload rows (the server's), no counts, and
 * no lead figure that would open a list they cannot read.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The real router: the address is part of what is checked.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));

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
const role = vi.hoisted(() => ({ value: 'analyst' as string }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: role.value } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 5, role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../components/scans/FormatRetryDialog', () => ({ default: () => null }));

import ScansHub from '../../pages/ScansHub';
import { TooltipProvider } from '../../components/ui/tooltip';
import { formatInstant } from '../../utils/scanTime';

const scan = (id: number, filename: string) => ({
  id, filename, tool_name: 'nmap', scan_type: 'port_scan', created_at: '2026-09-19T10:00:00Z',
  total_hosts: 3, up_hosts: 3, new_hosts: 1, updated_hosts: 2, total_ports: 4, open_ports: 4,
});
const upload = (id: number, over: Record<string, unknown> = {}) => ({
  id, parse_error_id: null, original_filename: `upload-${id}.xml`, status: 'failed', file_size: 2048,
  tool_name: 'Nessus', scan_type: null, scan_id: null, created_at: '2026-09-19T10:30:00Z',
  started_at: null, completed_at: null, duration_seconds: null, progress: null, stats: null,
  file_retained: true, retained_until: '2026-09-26T10:30:00Z',
  failure_reason: 'Nessus parser found 0 hosts', failure_kind: 'parsing_error',
  error: { error_type: 'parsing_error', error_message: 'Nessus parser found 0 hosts', user_message: 'Nessus parser found 0 hosts' },
  ...over,
});
const COUNTS = {
  needs_attention: 3, failed: 4, failed_dismissed: 1, expired: 0, discarded: 0, superseded: 0,
  staged: 0, queued: 0, processing: 0, completed: 7,
};
const history = (items: Array<{ kind: string; id: number }>, over: Record<string, unknown> = {}) => ({
  items: items.map((e) => ({ ...e, at: '2026-09-19T10:00:00Z' })),
  total: items.length, batch_total: 0, scan_total: items.filter((e) => e.kind === 'scan').length,
  upload_total: items.filter((e) => e.kind === 'upload').length, has_more: false,
  status_counts: COUNTS,
  ...over,
});

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/scans', element: <TooltipProvider><ScansHub /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
const historyCalls = () => api.getImportHistory.mock.calls.map((c) => c[1] as Record<string, unknown>);
const lastHistoryCall = () => historyCalls()[historyCalls().length - 1];

beforeEach(() => {
  vi.clearAllMocks();
  role.value = 'analyst';
  api.getRecentIngestionJobs.mockResolvedValue([]);
  api.getStagedIngestionJobs.mockResolvedValue([]);
  api.getScansSummary.mockResolvedValue({
    total_scans: 1, total_hosts: 3, up_hosts: 3, open_services: 4, tool_counts: { NMAP: 1 },
    imports_need_attention: 3, imports_not_imported: 0,
  });
  api.getScanInventoryMarker.mockResolvedValue({ count: 1, latest_id: 9 });
  api.getImportHistory.mockResolvedValue(history([{ kind: 'upload', id: 41 }, { kind: 'scan', id: 9 }]));
  api.getScans.mockResolvedValue([scan(9, 'newest.xml')]);
  api.getScanBatches.mockResolvedValue([]);
  api.getIngestionResults.mockResolvedValue({ items: [upload(41)], total: 1, summary: {} });
});

describe('Scans — one history of imports and of the uploads that were not imported', () => {
  it('lists an upload row among the scan rows, in the server\'s order, fetched by id', async () => {
    open('/scans');
    const failed = await screen.findByText('upload-41.xml');
    const scanRow = screen.getByText('newest.xml').closest('tr')!;
    // The server's order: the upload first.
    expect(failed.closest('tr')!.compareDocumentPosition(scanRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(api.getIngestionResults).toHaveBeenCalledWith(1, { ids: [41] });
    // Readable without opening it: what happened and why.
    const row = failed.closest('tr')!;
    expect(within(row).getByText('failed')).toBeInTheDocument();
    expect(within(row).getByTestId('failure-reason')).toHaveTextContent('Nessus parser found 0 hosts');
    // No second view of the page.
    expect(screen.queryByRole('tab', { name: 'Ingestion results' })).toBeNull();
  });

  it('offers on the upload row what Ingestion results offered', async () => {
    api.getIngestionResults.mockResolvedValue({
      items: [upload(41, {
        parse_error_id: 7, failure_kind: 'truncated_file', uninterpreted_total: 0,
        detected_format_label: 'Nessus', final_format_label: 'Nessus',
        error: { error_type: 'truncated_file', error_message: 'cut short after 12 records', user_message: 'cut short after 12 records' },
      })],
      total: 1, summary: {},
    });
    open('/scans');
    const row = (await screen.findByText('upload-41.xml')).closest('tr')!;
    fireEvent.click(within(row).getByRole('button', { name: 'Show details of upload-41.xml' }));
    expect(await screen.findByText('The file was cut short — nothing from it was imported')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import what upload-41.xml holds' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review format and retry' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'View details' })).toBeInTheDocument();
    expect(screen.getByTestId('format-chain')).toHaveTextContent('Detected as Nessus');
    expect(screen.getByText(/File retained until/)).toBeInTheDocument();
  });

  it('a status count opens its list: the server is asked for it, and the address says it', async () => {
    const router = open('/scans');
    const chip = await screen.findByRole('button', { name: /^Needs attention\s*3$/ });
    fireEvent.click(chip);
    await waitFor(() => expect(router.state.location.search).toBe('?status=needs_attention'));
    await waitFor(() => expect(lastHistoryCall()).toMatchObject({ status: 'needs_attention' }));
    expect(screen.getByRole('button', { name: /^Needs attention\s*3$/ })).toHaveAttribute('aria-pressed', 'true');
    // A status lists uploads one row each: the Grouped / All files switch goes.
    expect(screen.queryByRole('group', { name: 'How the import history is listed' })).toBeNull();
    // Pressed again, the whole history.
    fireEvent.click(screen.getByRole('button', { name: /^Needs attention\s*3$/ }));
    await waitFor(() => expect(router.state.location.search).toBe(''));
    await waitFor(() => expect(lastHistoryCall()).not.toHaveProperty('status', 'needs_attention'));
  });

  // G7 — "Failed 4" beside Operations' "3 imports failed": one was dismissed.
  it('says how many of the failed uploads are dismissed, right after Failed, and that opens exactly those', async () => {
    const router = open('/scans');
    const failed = await screen.findByRole('button', { name: /^Failed\s*4$/ });
    const dismissed = screen.getByRole('button', { name: /^of them dismissed\s*1$/ });
    expect(failed.nextElementSibling).toBe(dismissed);
    fireEvent.click(dismissed);
    await waitFor(() => expect(router.state.location.search).toBe('?status=failed_dismissed'));
    await waitFor(() => expect(lastHistoryCall()).toMatchObject({ status: 'failed_dismissed' }));
  });

  it('a link to one upload lists that upload, open, and the whole history is one click away', async () => {
    const router = open('/scans?job_id=41');
    await waitFor(() => expect(lastHistoryCall()).toMatchObject({ jobId: 41 }));
    // Open on arrival.
    expect(await screen.findByText('Error type: parsing_error')).toBeInTheDocument();
    expect(screen.getByTestId('one-upload')).toHaveTextContent('Upload #41');
    fireEvent.click(screen.getByRole('button', { name: 'Show the whole history' }));
    await waitFor(() => expect(router.state.location.search).toBe(''));
  });

  it('a link to a parse error asks for the upload that recorded it', async () => {
    open('/scans?error_id=55');
    await waitFor(() => expect(lastHistoryCall()).toMatchObject({ errorId: 55 }));
  });

  // A completed import is its scan row; the upload behind it (format chain,
  // retained file, Re-process) is one menu item away.
  it('a scan row\'s menu opens the upload that made it', async () => {
    api.getScans.mockResolvedValue([{ ...scan(9, 'newest.xml'), import_job_id: 33 }]);
    open('/scans');
    const row = (await screen.findByText('newest.xml')).closest('tr')!;
    await userEvent.click(within(row).getByRole('button', { name: 'More actions for newest.xml' }));
    expect(await screen.findByRole('menuitem', { name: 'Upload details' })).toHaveAttribute('href', '/scans?job_id=33');
  });

  // G8 — the column has ONE meaning on every row: when the file was uploaded.
  it('the time column is the upload time on every row, an upload row included', async () => {
    open('/scans');
    const row = (await screen.findByText('upload-41.xml')).closest('tr')!;
    const heading = screen.getAllByRole('columnheader')[1];
    expect(heading).toHaveTextContent(/^Uploaded/);
    const cell = row.querySelectorAll('td')[1];
    expect(cell.querySelector('p')).toHaveTextContent(formatInstant(new Date('2026-09-19T10:30:00Z'), { withZone: false }));
    expect(cell).not.toHaveTextContent(/\bran\b/);
  });
});

describe('Scans — the old Ingestion results address', () => {
  it('lands on the history with the same filter, query kept, replacing the entry', async () => {
    const router = open('/scans?view=results&status=failed&search=dmz&page=2&sort=status');
    await waitFor(() => expect(router.state.location.search).toBe('?status=failed&search=dmz'));
    expect(router.state.historyAction).toBe('REPLACE');
    await waitFor(() => expect(lastHistoryCall()).toMatchObject({ status: 'failed', search: 'dmz' }));
  });

  it('a link to one row of it lands on that upload', async () => {
    const router = open('/scans?view=results&job_id=484');
    await waitFor(() => expect(router.state.location.search).toBe('?job_id=484'));
    await waitFor(() => expect(lastHistoryCall()).toMatchObject({ jobId: 484 }));
  });
});

describe('Scans — a viewer is shown nothing about uploads they cannot open', () => {
  beforeEach(() => {
    role.value = 'viewer';
    // What the server answers a viewer: no upload entries and no counts.
    api.getImportHistory.mockResolvedValue(history([{ kind: 'scan', id: 9 }], { status_counts: null, upload_total: 0 }));
  });

  it('no upload row, no status count, no lead figure', async () => {
    open('/scans');
    await screen.findByText('newest.xml');
    expect(screen.queryByRole('button', { name: /Needs attention/ })).toBeNull();
    expect(screen.queryByRole('group', { name: /upload status/ })).toBeNull();
    // The summary still carries the project's figure; the lead does not show it.
    expect(screen.queryByRole('link', { name: /need attention/ })).toBeNull();
    expect(document.body).not.toHaveTextContent(/need attention|nothing failed|never imported/);
    expect(api.getIngestionResults).not.toHaveBeenCalled();
  });

  it('a scan row\'s menu offers no upload details', async () => {
    api.getScans.mockResolvedValue([{ ...scan(9, 'newest.xml'), import_job_id: 33 }]);
    open('/scans');
    const row = (await screen.findByText('newest.xml')).closest('tr')!;
    await userEvent.click(within(row).getByRole('button', { name: 'More actions for newest.xml' }));
    expect(await screen.findByRole('menuitem', { name: 'Open scan' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: 'Upload details' })).toBeNull();
  });

  it('an address naming a status or an upload is the history to them — nothing analyst-only is asked', async () => {
    open('/scans?status=needs_attention&job_id=41');
    await screen.findByText('newest.xml');
    await act(async () => { await Promise.resolve(); });
    for (const call of historyCalls()) {
      expect(call.status).toBeUndefined();
      expect(call.jobId).toBeUndefined();
    }
    expect(screen.queryByTestId('one-upload')).toBeNull();
  });
});
