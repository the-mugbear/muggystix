/**
 * Ingestion Results (pages/ParseErrors.tsx) — first page-level test.
 *
 * v5.242.0: Operations' "Inspect import errors" landed on every upload, because
 * the page had no view of "what needs someone"; its five stat cards did nothing
 * when clicked; a partial import read "completed"; and the only Dismiss lived on
 * another page and refused partial imports.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
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
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo' }, refreshProjects: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'admin' }, hasPermission: () => true }),
}));
// The completed row's import-result block fetches a scan; not what this checks.
vi.mock('../../components/scans/ImportResult', () => ({ default: () => null }));

import IngestionResults from '../../pages/ParseErrors';
import { TooltipProvider } from '../../components/ui/tooltip';

const row = (over: Record<string, unknown>) => ({
  id: 1, parse_error_id: null, original_filename: 'scan.xml', status: 'completed',
  file_size: 1024, tool_name: 'nmap', scan_type: null, scan_id: null,
  created_at: '2026-09-19T10:00:00Z', started_at: null, completed_at: null,
  duration_seconds: 2, progress: null, stats: null, error: null, ...over,
});

const response = (items: unknown[]) => ({
  items,
  total: items.length,
  summary: {
    total_needs_attention: 2, total_staged: 0, total_completed: 7, total_failed: 3,
    total_queued: 0, total_processing: 0,
    total_hosts: 1204, total_hosts_up: 900, total_ports: 5000, total_open_ports: 4000,
  },
});

const renderPage = (url = '/parse-errors') =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <TooltipProvider>
        <Routes>
          <Route path="/parse-errors" element={<IngestionResults />} />
        </Routes>
      </TooltipProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.getIngestionResults.mockResolvedValue(response([]));
});

describe('Ingestion Results — the counts are the filter', () => {
  it('opens on the view Operations links to, and asks the server for it', async () => {
    renderPage('/parse-errors?status=needs_attention');
    const chip = await screen.findByRole('button', { name: /Needs attention\s*2/ });
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(api.getIngestionResults).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'needs_attention' }),
    ));
  });

  // The filter lives in ?status= (the first test enters through it). setupTests
  // stubs useLocation, so the URL itself is not observable here; what the page
  // does with it is.
  it('a count sets the filter, the server is asked for it, and clicking it again clears it', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Failed\s*3/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Failed\s*3/ })).toHaveAttribute('aria-pressed', 'true'));
    await waitFor(() => expect(api.getIngestionResults).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'failed' }),
    ));
    fireEvent.click(screen.getByRole('button', { name: /Failed\s*3/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /All uploads/ })).toHaveAttribute('aria-pressed', 'true'));
    await waitFor(() => expect(api.getIngestionResults).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: undefined }),
    ));
  });

  it('counts uploads, not per-scan history rows, and shows a transient state only while it holds something', async () => {
    renderPage();
    expect(await screen.findByRole('button', { name: /All uploads\s*10/ })).toBeInTheDocument();
    // "Total Hosts 1,204" summed history rows across scans; it is gone.
    expect(screen.queryByText(/1,204/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Queued/ })).not.toBeInTheDocument();
  });

  it('keeps a deep-linked filter visible even when its chip would be hidden', async () => {
    renderPage('/parse-errors?status=queued');
    expect(await screen.findByRole('button', { name: /queued\s*0/ })).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('Ingestion Results — a partial import', () => {
  const partial = row({
    id: 9, original_filename: '23-nmap-truncated.xml', partial: true, skipped_count: 0,
    parser_warnings: 'Incomplete XML — hosts after this point are MISSING, not down.',
  });

  it('reads as partial in the table, says what was lost, and can be dismissed here', async () => {
    api.getIngestionResults.mockResolvedValue(response([partial]));
    api.dismissIngestionJob.mockResolvedValue({});
    renderPage();

    const tableRow = (await screen.findByText('23-nmap-truncated.xml')).closest('tr')!;
    expect(within(tableRow).getByText('completed')).toBeInTheDocument();
    expect(within(tableRow).getByText('partial')).toBeInTheDocument();

    fireEvent.click(within(tableRow).getByRole('button', { name: 'Expand details' }));
    expect(await screen.findByText(/hosts after this point are MISSING/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(api.dismissIngestionJob).toHaveBeenCalledWith(9));
    // …and the list reloads, so the row shows its new state.
    await waitFor(() => expect(api.getIngestionResults.mock.calls.length).toBeGreaterThan(1));
  });

  it('a dismissed row says so and offers no second dismiss', async () => {
    api.getIngestionResults.mockResolvedValue(response([{ ...partial, dismissed_at: '2026-09-19T12:00:00Z' }]));
    renderPage();
    const tableRow = (await screen.findByText('23-nmap-truncated.xml')).closest('tr')!;
    expect(within(tableRow).getByText('dismissed')).toBeInTheDocument();
    fireEvent.click(within(tableRow).getByRole('button', { name: 'Expand details' }));
    expect(await screen.findByText(/no longer listed as blocked/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
  });

  // Clearing a folder of fixture failures was expand → Dismiss, per row.
  it('dismisses exactly the rows shown, after saying what that means', async () => {
    api.getIngestionResults.mockResolvedValue(response([
      partial,
      row({ id: 11, original_filename: 'expected-results.json', status: 'failed' }),
      row({ id: 12, original_filename: 'old.json', status: 'failed', dismissed_at: '2026-09-01T00:00:00Z' }),
    ]));
    api.dismissIngestionJob.mockResolvedValue({});
    renderPage('/parse-errors?status=needs_attention');

    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss the 2 shown' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/1 of them imported only part of their file/)).toBeInTheDocument();
    expect(api.dismissIngestionJob).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(api.dismissIngestionJob).toHaveBeenCalledTimes(2));
    // The ids on screen — not the already-dismissed row, not "everything matching".
    expect(api.dismissIngestionJob.mock.calls.map((c) => c[0])).toEqual([9, 11]);
  });

  it('offers no bulk dismiss on the unfiltered list', async () => {
    api.getIngestionResults.mockResolvedValue(response([partial]));
    renderPage();
    await screen.findByText('23-nmap-truncated.xml');
    expect(screen.queryByRole('button', { name: /Dismiss the/ })).not.toBeInTheDocument();
  });

  it('a clean completed import has nothing to dismiss', async () => {
    api.getIngestionResults.mockResolvedValue(response([row({ id: 3, original_filename: 'fine.xml' })]));
    renderPage();
    const tableRow = (await screen.findByText('fine.xml')).closest('tr')!;
    expect(within(tableRow).queryByText('partial')).not.toBeInTheDocument();
    fireEvent.click(within(tableRow).getByRole('button', { name: 'Expand details' }));
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
  });
});
