/**
 * The Scans page's import history as a LIST: "Load more", what stays on
 * screen while another filter loads, and what a failed read leaves.  Written
 * against the page while it still read the history with `useInfiniteQuery`
 * itself, and unchanged by its move onto `hooks/useListQuery` (plan B6):
 *  - "more" is the server's word (`has_more`), or — for all files, which has
 *    no total — "a full page came back";
 *  - every loaded page counts (its rows, and whether part of it is missing);
 *  - after a FAILED read on a NEW filter only the error and Retry show (owner
 *    decision 2026-10-10), while a failed re-read of rows on screen keeps them.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
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
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo' } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'admin' }, hasPermission: () => true }),
}));

import Scans from '../../pages/Scans';
import { TooltipProvider } from '../../components/ui/tooltip';
import { createQueryClient } from '../../lib/query';

const scan = (id: number, filename = `file-${id}.xml`) => ({
  id, filename, tool_name: 'nmap', scan_type: 'port_scan', created_at: '2026-09-19T10:00:00Z',
  total_hosts: 3, up_hosts: 3, new_hosts: 1, updated_hosts: 2, total_ports: 4, open_ports: 4,
});
const batch = (id: number) => ({
  id, label: `Batch ${id}`, created_by: 'me', files: 12, tools: ['nmap'], hosts: 40,
  new_hosts: 5, open_ports: 90, first_uploaded: '2026-09-19T09:00:00Z', last_uploaded: '2026-09-19T09:30:00Z',
  pending_files: 0, failed_files: 0,
});
type Entry = { kind: 'scan' | 'batch'; id: number; at: string };
const entry = (kind: Entry['kind'], id: number): Entry => ({ kind, id, at: '2026-09-19T11:00:00Z' });

/** The history's order, two entries a page: page 1 says there is more. */
const ORDER: Entry[] = [entry('scan', 9), entry('batch', 4), entry('scan', 3), entry('scan', 2)];
const historyPage = (skip = 0) => ({
  items: ORDER.slice(skip, skip + 2), total: ORDER.length, batch_total: 1, scan_total: 3,
  has_more: skip + 2 < ORDER.length,
});
type HistoryOptions = { skip?: number; search?: string };
type ScanOptions = { ids?: number[] };

const renderPage = (path = '/scans', wrapper?: React.ComponentType<{ children: React.ReactNode }>) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <TooltipProvider><Scans /></TooltipProvider>
    </MemoryRouter>,
    wrapper ? { wrapper } : undefined,
  );
/** The page under a client the test can reach — to ask for a re-read. */
const renderWithClient = (path = '/scans') => {
  const client = createQueryClient();
  renderPage(path, ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>);
  return client;
};
const historyAsked = () => api.getImportHistory.mock.calls.map(([, options]) => (options as HistoryOptions).skip ?? 'first');
const moreButton = () => screen.queryByRole('button', { name: /^Load \d+ more/ });

beforeEach(() => {
  vi.clearAllMocks();
  api.getRecentIngestionJobs.mockResolvedValue([]);
  api.getStagedIngestionJobs.mockResolvedValue([]);
  api.getScansSummary.mockResolvedValue({ total_scans: 3, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 3 } });
  api.getScanInventoryMarker.mockResolvedValue({ count: 3, latest_id: 9 });
  api.getImportHistory.mockImplementation(async (_projectId: number, options: HistoryOptions = {}) => historyPage(options.skip));
  api.getScans.mockImplementation(async (_projectId: number, _skip: number, _limit: number, options: ScanOptions = {}) => (
    (options.ids ?? []).map((id) => scan(id))
  ));
  api.getScanBatches.mockImplementation(async (_projectId: number, options: ScanOptions = {}) => (
    (options.ids ?? []).map((id) => batch(id))
  ));
});

describe('Scans — the first read', () => {
  it('keeps the title on screen while the list is read, and has no view switch', async () => {
    // 5.418.0 — one import history: the "Imported scans | Ingestion results"
    // switch is gone (owner decision 2026-10-22, G15).
    let answer: (page: ReturnType<typeof historyPage>) => void = () => undefined;
    api.getImportHistory.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    renderPage();
    expect(await screen.findByRole('heading', { name: 'Scans' })).toBeInTheDocument();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByText('file-9.xml')).toBeNull();
    await act(async () => { answer(historyPage()); });
    expect(await screen.findByText('file-9.xml')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Ingestion results' })).toBeNull();
  });
});

describe('Scans — "Load more" of the import history', () => {
  it('offers more on the server’s word, asks where the loaded entries end, and lists both pages in order', async () => {
    renderPage();
    expect(await screen.findByText('file-9.xml')).toBeInTheDocument();
    expect(screen.getByText('Batch 4')).toBeInTheDocument();
    expect(screen.queryByText('file-3.xml')).toBeNull();
    // The first page is asked with no `skip` at all.
    expect(api.getImportHistory.mock.calls[0][1]).not.toHaveProperty('skip');
    expect(api.getImportHistory.mock.calls[0][1]).toMatchObject({ limit: 100 });
    expect(screen.getByText(/2 of 4 uploads · 1 batch, 1 single file · more below/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Load 100 more uploads' }));
    expect(await screen.findByText('file-2.xml')).toBeInTheDocument();
    expect(historyAsked()).toEqual(['first', 2]);
    const names = within(screen.getByRole('table')).getAllByRole('row').slice(1).map((r) => r.textContent ?? '');
    expect(names.map((text) => text.match(/file-\d\.xml|Batch \d/)?.[0])).toEqual(
      ['file-9.xml', 'Batch 4', 'file-3.xml', 'file-2.xml'],
    );
    // The server said there is no more: nothing further is offered.
    await waitFor(() => expect(moreButton()).toBeNull());
    expect(screen.getByText(/4 uploads · 1 batch, 3 single files/)).toBeInTheDocument();
    expect(screen.queryByText(/more below/)).toBeNull();
  });

  it('a page that says "more" is believed even when the total says the list is whole', async () => {
    api.getImportHistory.mockImplementation(async (_projectId: number, options: HistoryOptions = {}) => (
      { ...historyPage(options.skip), total: 2 }
    ));
    renderPage();
    await screen.findByText('file-9.xml');
    expect(screen.getByRole('button', { name: 'Load 100 more uploads' })).toBeInTheDocument();
  });

  it('a re-read asks for every loaded page again, each where the fresh rows end', async () => {
    const client = renderWithClient();
    await screen.findByText('file-9.xml');
    fireEvent.click(screen.getByRole('button', { name: 'Load 100 more uploads' }));
    await screen.findByText('file-2.xml');
    api.getImportHistory.mockClear();

    await act(async () => { await client.invalidateQueries({ queryKey: ['getImportHistory'] }); });
    expect(historyAsked()).toEqual(['first', 2]);
    expect(screen.getByText('file-2.xml')).toBeInTheDocument();
  });

  it('a failed "Load more" is a toast: the rows stay, more is still offered, and the list shows no failure', async () => {
    renderPage();
    await screen.findByText('file-9.xml');
    api.getImportHistory.mockRejectedValueOnce(new Error('boom'));
    fireEvent.click(screen.getByRole('button', { name: 'Load 100 more uploads' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    expect(String(toast.error.mock.calls[0][0])).toMatch(/Could not load more of the import history|boom/);
    expect(screen.getByText('file-9.xml')).toBeInTheDocument();
    expect(screen.queryByTestId('history-error')).toBeNull();

    fireEvent.click(await screen.findByRole('button', { name: 'Load 100 more uploads' }));
    expect(await screen.findByText('file-2.xml')).toBeInTheDocument();
  });

  it('says the history is missing entries when a part of ANY loaded page could not be read', async () => {
    renderPage();
    await screen.findByText('file-9.xml');
    expect(screen.queryByText(/Some rows of this history could not be loaded/)).toBeNull();

    // The second page's files cannot be read; its order can.
    api.getScans.mockRejectedValueOnce(new Error('boom'));
    fireEvent.click(screen.getByRole('button', { name: 'Load 100 more uploads' }));
    expect(await screen.findByText(/Some rows of this history could not be loaded/)).toBeInTheDocument();
    // The first page's rows are untouched, and nothing is invented for the second.
    expect(screen.getByText('file-9.xml')).toBeInTheDocument();
    expect(screen.queryByText('file-3.xml')).toBeNull();
  });
});

describe('Scans — "Load more" of all files (no total)', () => {
  const files = (from: number, n: number) => Array.from({ length: n }, (_, i) => scan(from + i));

  it('offers more while a full page came back, asks from the files loaded, and stops at a short page', async () => {
    api.getScans.mockImplementation(async (_projectId: number, skip: number, limit: number) => (
      skip === 0 ? files(1, limit) : files(1000, 3)
    ));
    renderPage('/scans?batch_files=show');
    expect(await screen.findByText('file-1.xml', undefined, { timeout: 15_000 })).toBeInTheDocument();
    expect(api.getScans.mock.calls[0].slice(0, 3)).toEqual([1, 0, 250]);
    // Not the history's order — only its status counts (one entry), for the
    // queue strip's failed line.
    for (const call of api.getImportHistory.mock.calls) expect(call[1]).toMatchObject({ limit: 1 });

    fireEvent.click(screen.getByRole('button', { name: 'Load 250 more' }));
    expect(await screen.findByText('file-1002.xml', undefined, { timeout: 15_000 })).toBeInTheDocument();
    expect(api.getScans.mock.calls[1].slice(0, 3)).toEqual([1, 250, 250]);
    await waitFor(() => expect(moreButton()).toBeNull());
    expect(screen.getByText(/^253 files/)).toBeInTheDocument();
    // A full page is 250 rows of the real table: slow to draw in a busy run.
  }, 30_000);

  it('a page that is not full is the end', async () => {
    api.getScans.mockResolvedValue(files(1, 4));
    renderPage('/scans?batch_files=show');
    await screen.findByText('file-1.xml');
    expect(moreButton()).toBeNull();
  });
});

describe('Scans — what stays on screen for another filter', () => {
  const search = (text: string) => fireEvent.change(screen.getByLabelText('Search scan inventory'), { target: { value: text } });

  it('the rows on screen stay while a new filter loads, then are replaced by its own', async () => {
    let answer: (page: unknown) => void = () => undefined;
    api.getImportHistory.mockImplementation((_projectId: number, options: HistoryOptions = {}) => (
      options.search
        ? new Promise((resolve) => { answer = resolve; })
        : Promise.resolve(historyPage(options.skip))
    ));
    renderPage();
    await screen.findByText('file-9.xml');

    search('dmz');
    await waitFor(() => expect(api.getImportHistory).toHaveBeenCalledWith(1, expect.objectContaining({ search: 'dmz' })));
    // No skeleton, no blank: the previous rows, with no "more" to ask of them.
    expect(screen.getByText('file-9.xml')).toBeInTheDocument();
    expect(screen.queryByTestId('history-error')).toBeNull();
    expect(moreButton()).toBeNull();

    await act(async () => { answer({ items: [entry('scan', 7)], total: 1, batch_total: 0, scan_total: 1, has_more: false }); });
    expect(await screen.findByText('file-7.xml')).toBeInTheDocument();
    expect(screen.queryByText('file-9.xml')).toBeNull();
  });

  it('after a FAILED read on a new filter only the error and Retry show — not the rows of the filter that was left', async () => {
    let retried: (page: unknown) => void = () => undefined;
    let attempts = 0;
    api.getImportHistory.mockImplementation((_projectId: number, options: HistoryOptions = {}) => {
      if (!options.search) return Promise.resolve(historyPage(options.skip));
      attempts += 1;
      return attempts === 1 ? Promise.reject(new Error('boom')) : new Promise((resolve) => { retried = resolve; });
    });
    renderPage();
    await screen.findByText('file-9.xml');

    search('dmz');
    const alert = await screen.findByTestId('history-error');
    expect(alert).toHaveTextContent(/Could not load the import history/);
    expect(alert).not.toHaveTextContent(/from the last successful load/);
    expect(screen.queryByText('file-9.xml')).toBeNull();
    expect(screen.queryByText('Batch 4')).toBeNull();
    expect(screen.queryByText('No scans uploaded yet')).toBeNull();

    // Retry is that filter's read again; while it is on its way the page is
    // loading another filter, as before the failure.
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(attempts).toBe(2));
    await waitFor(() => expect(screen.queryByTestId('history-error')).toBeNull());
    expect(screen.getByText('file-9.xml')).toBeInTheDocument();

    await act(async () => { retried({ items: [entry('scan', 7)], total: 1, batch_total: 0, scan_total: 1, has_more: false }); });
    expect(await screen.findByText('file-7.xml')).toBeInTheDocument();
    expect(screen.queryByText('file-9.xml')).toBeNull();
    expect(screen.queryByTestId('history-error')).toBeNull();
  });

  it('a failed RE-READ of the rows on screen keeps them, and says they are from the last successful load', async () => {
    const client = renderWithClient();
    await screen.findByText('file-9.xml');
    api.getImportHistory.mockRejectedValueOnce(new Error('boom'));
    await act(async () => { await client.invalidateQueries({ queryKey: ['getImportHistory'] }); });

    const alert = await screen.findByTestId('history-error');
    expect(alert).toHaveTextContent(/Could not load the import history\. The rows below are from the last successful load\./);
    expect(screen.getByText('file-9.xml')).toBeInTheDocument();
    expect(screen.getByText('Batch 4')).toBeInTheDocument();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByTestId('history-error')).toBeNull());
    expect(screen.getByText('file-9.xml')).toBeInTheDocument();
  });
});

describe('Scans — a first load that failed, tried again', () => {
  it('keeps the page and what it said went wrong until the answer arrives — no skeleton in between', async () => {
    let retried: (page: unknown) => void = () => undefined;
    let attempts = 0;
    api.getImportHistory.mockImplementation(() => {
      attempts += 1;
      return attempts === 1 ? Promise.reject(new Error('boom')) : new Promise((resolve) => { retried = resolve; });
    });
    renderPage();
    const alert = await screen.findByTestId('history-error');
    expect(alert).toHaveTextContent(/Could not load the import history/);

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(attempts).toBe(2));
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 20); }); });
    // Still the page (its heading), still the message.
    expect(screen.getByTestId('history-error')).toHaveTextContent(/Could not load the import history/);
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
    expect(screen.queryByText('No scans uploaded yet')).toBeNull();

    await act(async () => { retried(historyPage()); });
    expect(await screen.findByText('file-9.xml')).toBeInTheDocument();
    expect(screen.queryByTestId('history-error')).toBeNull();
  });
});
