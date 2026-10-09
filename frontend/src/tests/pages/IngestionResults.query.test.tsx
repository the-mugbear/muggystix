/**
 * Ingestion Results (pages/ParseErrors.tsx), review 2026-10-01:
 *   B15 — search, sort, direction and page live in the URL (status already did);
 *   R33 — a slow response for an earlier filter never replaces the current rows;
 *   R34 — a failed "Discard" on a staged file is said;
 *   R32 — retry / discard / dismiss / re-process are a project analyst's.
 */
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useSearchParams } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

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
const role = vi.hoisted(() => ({ value: 'analyst' as string | undefined }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: role.value }, refreshProjects: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../components/scans/ImportResult', () => ({ default: () => null }));
vi.mock('../../components/scans/FormatRetryDialog', () => ({ default: () => null }));

import IngestionResults from '../../pages/ParseErrors';
import { TooltipProvider } from '../../components/ui/tooltip';

const row = (over: Record<string, unknown>) => ({
  id: 1, parse_error_id: null, original_filename: 'scan.xml', status: 'completed',
  file_size: 1024, tool_name: 'nmap', scan_type: null, scan_id: null,
  created_at: '2026-09-19T10:00:00Z', started_at: null, completed_at: null,
  duration_seconds: 2, progress: null, stats: null, error: null, ...over,
});
const response = (items: unknown[], total = items.length) => ({
  items, total,
  summary: {
    total_needs_attention: 2, total_staged: 1, total_completed: 7, total_failed: 3,
    total_queued: 0, total_processing: 0,
    total_hosts: 0, total_hosts_up: 0, total_ports: 0, total_open_ports: 0,
  },
});

const Where = () => <output data-testid="where">{useSearchParams()[0].toString()}</output>;
const renderPage = (url = '/parse-errors') => render(
  <MemoryRouter initialEntries={[url]}>
    <TooltipProvider>
      <Routes><Route path="/parse-errors" element={<><IngestionResults /><Where /></>} /></Routes>
    </TooltipProvider>
  </MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  role.value = 'analyst';
  api.getIngestionResults.mockResolvedValue(response([row({})], 80));
});

describe('Ingestion Results — the view is in the URL (B15)', () => {
  it('a link with search, sort, direction and page restores all four', async () => {
    renderPage('/parse-errors?status=failed&search=dmz&sort=file_size&dir=asc&page=3');
    await screen.findByText('scan.xml');
    expect(api.getIngestionResults).toHaveBeenLastCalledWith({
      skip: 50, limit: 25, status: 'failed', search: 'dmz', sortBy: 'file_size', sortOrder: 'asc',
    }, expect.any(AbortSignal));
    expect(screen.getByLabelText('Search ingestion results by filename or error message')).toHaveValue('dmz');
    expect(screen.getByRole('combobox', { name: 'Sort by' })).toHaveTextContent('Sort: File size');
  });

  // Branch review 2026-10-01 M11 — `?page=1.5` sent `skip=12.5`.
  it.each(['1.5', '-2', '0', 'abc', '1e3', '2 ', ''])('a page of "%s" is the first page', async (value) => {
    renderPage(`/parse-errors?page=${encodeURIComponent(value)}`);
    await screen.findByText('scan.xml');
    expect(api.getIngestionResults.mock.calls.every(([q]) => q.skip === 0)).toBe(true);
  });

  it('typing a search writes it to the URL, and a new filter goes back to the first page', async () => {
    renderPage('/parse-errors?page=2');
    await screen.findByText('scan.xml');
    expect(api.getIngestionResults).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 25 }), expect.any(AbortSignal));

    fireEvent.change(screen.getByLabelText('Search ingestion results by filename or error message'), { target: { value: 'nessus' } });
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('search=nessus'));
    await waitFor(() => expect(api.getIngestionResults).toHaveBeenLastCalledWith(
      expect.objectContaining({ skip: 0, search: 'nessus' }), expect.any(AbortSignal),
    ));
  });

  it('Next and the sort direction are written too', async () => {
    renderPage();
    await screen.findByText('scan.xml');
    fireEvent.click(screen.getByRole('button', { name: /Sort direction/ }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('dir=asc'));
    await screen.findByText('scan.xml');
    fireEvent.click(screen.getByRole('button', { name: /Next/ }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('dir=asc&page=2'));
    await waitFor(() => expect(api.getIngestionResults).toHaveBeenLastCalledWith(
      expect.objectContaining({ skip: 25, sortOrder: 'asc' }), expect.any(AbortSignal),
    ));
  });

  it('ignores a sort key the server does not have', async () => {
    renderPage('/parse-errors?sort=drop_table');
    await screen.findByText('scan.xml');
    expect(api.getIngestionResults).toHaveBeenLastCalledWith(expect.objectContaining({ sortBy: 'created_at' }), expect.any(AbortSignal));
  });
});

describe('Ingestion Results — the latest filter wins (R33)', () => {
  it('a slow response for "all" never replaces the rows of the filter chosen after it', async () => {
    let releaseAll!: (v: unknown) => void;
    const slowAll = new Promise((resolve) => { releaseAll = resolve; });
    api.getIngestionResults.mockImplementation(({ status }: { status?: string }) =>
      (status === 'failed'
        ? Promise.resolve(response([row({ id: 2, original_filename: 'failed.xml', status: 'failed' })]))
        : slowAll));
    const Shell = () => {
      const [, setParams] = useSearchParams();
      return <button type="button" onClick={() => setParams({ status: 'failed' })}>to failed</button>;
    };
    render(
      <MemoryRouter initialEntries={['/parse-errors']}>
        <TooltipProvider><Shell /><IngestionResults /></TooltipProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(api.getIngestionResults).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'to failed' }));
    await screen.findByText('failed.xml');

    await act(async () => { releaseAll(response([row({ id: 1, original_filename: 'everything.xml' })])); await Promise.resolve(); });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText('failed.xml')).toBeInTheDocument();
    expect(screen.queryByText('everything.xml')).toBeNull();
  });
});

describe('Ingestion Results — a staged file', () => {
  const staged = row({ id: 4, original_filename: 'waiting.xml', status: 'staged', file_retained: true });

  it('says why a Discard failed instead of doing nothing (R34)', async () => {
    api.getIngestionResults.mockResolvedValue(response([staged]));
    api.discardIngestionJob.mockRejectedValue({ response: { status: 409, data: { detail: 'It was started meanwhile.' } } });
    renderPage();
    const tableRow = (await screen.findByText('waiting.xml')).closest('tr')!;
    fireEvent.click(within(tableRow).getByRole('button', { name: 'Expand details' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('It was started meanwhile.'));
  });

  it('shows a reader the row without its actions (R32)', async () => {
    role.value = 'auditor';
    api.getIngestionResults.mockResolvedValue(response([
      staged, row({ id: 5, original_filename: 'broken.xml', status: 'failed', file_retained: true, error: { error_message: 'bad' } }),
    ]));
    renderPage();
    const stagedRow = (await screen.findByText('waiting.xml')).closest('tr')!;
    fireEvent.click(within(stagedRow).getByRole('button', { name: 'Expand details' }));
    await screen.findByText(/Stored but not imported/);
    expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Review format and import/ })).toBeNull();

    const failedRow = screen.getByText('broken.xml').closest('tr')!;
    fireEvent.click(within(failedRow).getByRole('button', { name: 'Expand details' }));
    await screen.findByRole('button', { name: 'View Details' });
    expect(screen.queryByRole('button', { name: /Review format and retry/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();
  });
});
