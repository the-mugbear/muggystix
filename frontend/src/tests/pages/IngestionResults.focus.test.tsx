/**
 * Ingestion Results (pages/ParseErrors.tsx) — what a link to one row does, and
 * the "View Details" lookup.  Written before the page moved onto
 * `usePagedList` + `useUrlPage` and the lookup became a query (plan B4 / B19,
 * 2026-10-10), and green on the page as it was: the row a link names is opened
 * once its OWN list has answered, the id leaves the address in one write that
 * keeps everything else, and the detail dialog opens only on what the server
 * returned.
 */
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes, useNavigate as useRealNavigate, useSearchParams } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The real router (setupTests replaces `useNavigate`): Back is part of this.
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
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: 'analyst' } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../components/scans/ImportResult', () => ({ default: () => null }));
vi.mock('../../components/scans/FormatRetryDialog', () => ({ default: () => null }));

import IngestionResults from '../../pages/ParseErrors';
import { TooltipProvider } from '../../components/ui/tooltip';

Element.prototype.scrollIntoView = vi.fn();

const row = (over: Record<string, unknown>) => ({
  id: 1, parse_error_id: null, original_filename: 'scan.xml', status: 'completed',
  file_size: 1024, tool_name: 'nmap', scan_type: null, scan_id: null,
  created_at: '2026-09-19T10:00:00Z', started_at: null, completed_at: null,
  duration_seconds: 2, progress: null, stats: null, error: null, ...over,
});
const failed = (id: number, over: Record<string, unknown> = {}) => row({
  id, original_filename: `file-${id}.xml`, status: 'failed', error: { error_message: `bad ${id}` }, ...over,
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
const where = () => screen.getByTestId('where').textContent;
const renderPage = (url: string, link = '/parse-errors') => render(
  <MemoryRouter initialEntries={[url]}>
    <TooltipProvider>
      <Routes>
        <Route path="/parse-errors" element={<><IngestionResults /><Where /><Link to={link}>the link</Link></>} />
      </Routes>
    </TooltipProvider>
  </MemoryRouter>,
);
const tableRow = (id: number) => document.querySelector(`[data-ingestion-row="${id}"]`) as HTMLElement;
const isOpen = (id: number) => within(tableRow(id)).queryByRole('button', { name: 'Collapse details' }) !== null;
const lastQuery = () => api.getIngestionResults.mock.lastCall?.[1] as Record<string, unknown>;

beforeEach(() => {
  vi.clearAllMocks();
  api.getIngestionResults.mockResolvedValue(response([failed(4), failed(5)], 80));
});

describe('Ingestion Results — a link to one row', () => {
  it('opens the job’s row and takes the id out of the address, keeping the filter and the page', async () => {
    renderPage('/parse-errors?status=failed&page=2&job_id=5');
    await screen.findByText('file-5.xml');
    await waitFor(() => expect(isOpen(5)).toBe(true));
    expect(isOpen(4)).toBe(false);
    await waitFor(() => expect(where()).toBe('status=failed&page=2'));
    expect(lastQuery()).toMatchObject({ skip: 25, limit: 25, status: 'failed' });
    expect(toast.info).not.toHaveBeenCalled();
  });

  it('a parse-error link is resolved to the job that recorded it, never to a job with that number', async () => {
    api.getIngestionResults.mockResolvedValue(response([failed(77), failed(5, { parse_error_id: 77 })]));
    renderPage('/parse-errors?error_id=77');
    await screen.findByText('file-5.xml');
    await waitFor(() => expect(isOpen(5)).toBe(true));
    expect(isOpen(77)).toBe(false);
    await waitFor(() => expect(where()).toBe(''));
  });

  it('a row that is not on this page is said, and the id leaves the address', async () => {
    renderPage('/parse-errors?search=dmz&job_id=999');
    await screen.findByText('file-5.xml');
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(
      'Ingestion result for job #999 isn\'t on this page — search or page to it.', { id: 'pe-focus-999' },
    ));
    await waitFor(() => expect(where()).toBe('search=dmz'));
    expect(isOpen(4)).toBe(false);
    expect(isOpen(5)).toBe(false);
    expect(toast.info).toHaveBeenCalledTimes(1);
  });

  it('a row the reader then closes stays closed', async () => {
    renderPage('/parse-errors?job_id=5');
    await waitFor(() => expect(tableRow(5) && isOpen(5)).toBe(true));
    await waitFor(() => expect(where()).toBe(''));
    fireEvent.click(within(tableRow(5)).getByRole('button', { name: 'Collapse details' }));
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 20); }); });
    expect(isOpen(5)).toBe(false);
  });

  // v5.289.0 — "Superseded — imported by job #N": ONE navigation changes the
  // filter and names a row.  The row is looked for in the NEW filter's rows.
  it('a link that changes the filter and names a row waits for that filter’s rows', async () => {
    let answerAll!: (v: unknown) => void;
    api.getIngestionResults.mockImplementation((_projectId: number, q: { status?: string }) => (
      q.status === 'superseded'
        ? Promise.resolve(response([failed(478, { superseded_by_job_id: 484 })], 80))
        : new Promise((resolve) => { answerAll = resolve; })
    ));
    renderPage('/parse-errors?status=superseded&page=2', '/parse-errors?job_id=484');
    await screen.findByText('file-478.xml');
    expect(lastQuery()).toMatchObject({ skip: 25, status: 'superseded' });

    fireEvent.click(screen.getByRole('link', { name: 'the link' }));
    await waitFor(() => expect(lastQuery()).toMatchObject({ skip: 0, status: undefined }));
    // The superseded rows are not where job #484 is looked for.
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 20); }); });
    expect(toast.info).not.toHaveBeenCalled();
    expect(where()).toBe('job_id=484');

    await act(async () => { answerAll(response([row({ id: 484, original_filename: 'imported.xml' })])); });
    await screen.findByText('imported.xml');
    await waitFor(() => expect(isOpen(484)).toBe(true));
    await waitFor(() => expect(where()).toBe(''));
    expect(toast.info).not.toHaveBeenCalled();
  });
});

// Plan B4's "check first".  The list hook writes the address too (a new filter
// takes `page` out; a page past the end steps back), and so does the row
// focus: two writes in one tick lose the first, so each pair is driven here
// through the real router and the address is read at the end.
describe('Ingestion Results — the list’s own address writes and the row focus', () => {
  it('a link that changes the filter, still carries a page and names a row: first page, row opened, both gone from the address', async () => {
    api.getIngestionResults.mockImplementation(async (_projectId: number, q: { status?: string }) => (
      q.status === 'superseded'
        ? response([failed(478, { superseded_by_job_id: 484 })], 80)
        : response([row({ id: 484, original_filename: 'imported.xml' })], 80)
    ));
    renderPage('/parse-errors?status=superseded&page=2', '/parse-errors?page=2&job_id=484');
    await screen.findByText('file-478.xml');
    fireEvent.click(screen.getByRole('link', { name: 'the link' }));
    await screen.findByText('imported.xml');
    await waitFor(() => expect(isOpen(484)).toBe(true));
    await waitFor(() => expect(where()).toBe(''));
    // Past any write still on its way: the address stays as it is.
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 30); }); });
    expect(where()).toBe('');
    expect(isOpen(484)).toBe(true);
    // The new filter was asked for from its first page, and never from page 2.
    const asked = api.getIngestionResults.mock.calls
      .map(([, q]) => q as { status?: string; skip: number })
      .filter((q) => q.status === undefined);
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every((q) => q.skip === 0)).toBe(true);
    expect(toast.info).not.toHaveBeenCalled();
  });

  it('a page past the end with a row named: the row is said not to be there, and the list still steps back to its last page', async () => {
    api.getIngestionResults.mockImplementation(async (_projectId: number, q: { skip: number }) => (
      q.skip === 25 ? response([failed(5)], 30) : response([], 30)
    ));
    renderPage('/parse-errors?page=9&job_id=5');
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith(
      'Ingestion result for job #5 isn\'t on this page — search or page to it.', { id: 'pe-focus-5' },
    ));
    await screen.findByText('file-5.xml');
    await waitFor(() => expect(where()).toBe('page=2'));
    await act(async () => { await new Promise((resolve) => { setTimeout(resolve, 30); }); });
    expect(where()).toBe('page=2');
    expect(lastQuery()).toMatchObject({ skip: 25 });
    expect(toast.info).toHaveBeenCalledTimes(1);
  });

  it('Back from a row’s link returns to the filter and the page that were left', async () => {
    api.getIngestionResults.mockImplementation(async (_projectId: number, q: { status?: string }) => (
      q.status === 'superseded'
        ? response([failed(478, { superseded_by_job_id: 484 })], 80)
        : response([row({ id: 484, original_filename: 'imported.xml' })], 80)
    ));
    const Back = () => {
      const navigate = useRealNavigate();
      return <button type="button" onClick={() => navigate(-1)}>back</button>;
    };
    render(
      <MemoryRouter initialEntries={['/parse-errors?status=superseded&page=2']}>
        <TooltipProvider>
          <Routes>
            <Route path="/parse-errors" element={<><IngestionResults /><Where /><Back /></>} />
          </Routes>
        </TooltipProvider>
      </MemoryRouter>,
    );
    // The row's own "imported by job #484" link.
    fireEvent.click(await screen.findByRole('link', { name: 'job #484' }));
    await screen.findByText('imported.xml');
    await waitFor(() => expect(where()).toBe(''));
    fireEvent.click(screen.getByRole('button', { name: 'back' }));
    await screen.findByText('file-478.xml');
    expect(where()).toBe('status=superseded&page=2');
    expect(lastQuery()).toMatchObject({ skip: 25, status: 'superseded' });
  });
});

describe('Ingestion Results — View Details', () => {
  const detail = (over: Record<string, unknown> = {}) => ({
    id: 77, filename: 'file-5.xml', file_type: 'xml', file_size: 2048, error_type: 'XMLSyntaxError',
    error_message: 'not well-formed (line 3)', error_details: null, file_preview: null,
    user_message: 'The file is not valid XML.', status: 'unresolved', created_at: '2026-09-19T10:00:00Z', updated_at: null,
    ...over,
  });
  const open = async (id: number) => {
    fireEvent.click(within(tableRow(id)).getByRole('button', { name: 'Expand details' }));
    fireEvent.click(await screen.findByRole('button', { name: 'View Details' }));
  };

  beforeEach(() => {
    api.getIngestionResults.mockResolvedValue(response([failed(5, { parse_error_id: 77 }), failed(6)]));
  });

  it('asks for the row’s parse error — not the job’s number — and opens on what the server returned', async () => {
    api.getParseError.mockResolvedValue(detail());
    renderPage('/parse-errors');
    await screen.findByText('file-5.xml');
    expect(api.getParseError).not.toHaveBeenCalled();
    await open(5);
    const dialog = await screen.findByRole('dialog');
    expect(api.getParseError.mock.calls[0].slice(0, 2)).toEqual([1, 77]);
    expect(within(dialog).getByText('not well-formed (line 3)')).toBeInTheDocument();
    expect(within(dialog).getByText('The file is not valid XML.')).toBeInTheDocument();
    expect(within(dialog).getByText(/XMLSyntaxError/)).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('a lookup that fails is said and opens nothing', async () => {
    api.getParseError.mockRejectedValue({ response: { status: 404, data: { detail: 'Parse error not found' } } });
    renderPage('/parse-errors');
    await screen.findByText('file-5.xml');
    await open(5);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Parse error not found', { id: 'pe-detail-5' }));
    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();

    // Asked again, it is looked up again — and opens when the server answers.
    api.getParseError.mockResolvedValue(detail());
    fireEvent.click(screen.getByRole('button', { name: 'View Details' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(api.getParseError).toHaveBeenCalledTimes(2);
    expect(toast.error).toHaveBeenCalledTimes(1);
  });

  it('a row with no recorded parse error says so and asks for nothing', async () => {
    renderPage('/parse-errors');
    await screen.findByText('file-6.xml');
    await open(6);
    expect(toast.error).toHaveBeenCalledWith(
      'Ingestion #6 has no recorded parse error to open.', { id: 'pe-detail-6' },
    );
    expect(api.getParseError).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('closed and opened again, it is read again and shows the newer answer', async () => {
    api.getParseError.mockResolvedValueOnce(detail());
    renderPage('/parse-errors');
    await screen.findByText('file-5.xml');
    await open(5);
    const first = await screen.findByRole('dialog');
    fireEvent.click(within(first).getByRole('button', { name: 'Close dialog' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    api.getParseError.mockResolvedValueOnce(detail({ error_message: 'as it stands now' }));
    fireEvent.click(screen.getByRole('button', { name: 'View Details' }));
    const second = await screen.findByRole('dialog');
    expect(within(second).getByText('as it stands now')).toBeInTheDocument();
    expect(within(second).queryByText('not well-formed (line 3)')).toBeNull();
    expect(api.getParseError).toHaveBeenCalledTimes(2);
  });
});
