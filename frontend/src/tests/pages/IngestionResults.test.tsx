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

// Screenshot review 2026-09-23 (v5.288.0).
describe('Ingestion Results — rows readable without expanding', () => {
  const dismissed = '2026-09-20T10:00:00Z';
  const failed = (id: number, message: string, name = `f${id}.xml`) =>
    row({
      id, status: 'failed', original_filename: name, dismissed_at: dismissed,
      error: { error_type: null, error_message: message, user_message: message },
    });

  it('says why a failed row was not imported, inline', async () => {
    api.getIngestionResults.mockResolvedValue(response([
      failed(1, 'Staged upload expired: not started within 24 hours.'),
      failed(2, 'Discarded before import'),
      failed(3, "Failed to parse the file 'nikto-all.txt'.\nTraceback: …"),
    ]));
    renderPage();
    await screen.findByText('f1.xml');
    const reasons = screen.getAllByTestId('failure-reason').map((el) => el.textContent);
    expect(reasons).toEqual([
      'Expired before import',
      'Discarded before import',
      "Failed to parse the file 'nikto-all.txt'.",
    ]);
  });

  it('shows the whole filename (wrapping), not an ellipsis', async () => {
    const name = 'eyewitness_with_screenshots_of_every_host_in_the_dmz_2026-09-22.zip';
    api.getIngestionResults.mockResolvedValue(response([row({ id: 4, original_filename: name })]));
    renderPage();
    const cell = await screen.findByText(name);
    expect(cell.className).not.toMatch(/truncate/);
    expect(cell).toHaveAttribute('title', name);
  });

  it('shows "—" for no ports/hosts and "<0.1s" for a sub-100 ms import', async () => {
    api.getIngestionResults.mockResolvedValue(response([
      row({
        id: 5, original_filename: 'names.txt', duration_seconds: 0.03,
        stats: { hosts_parsed: 0, hosts_up: 0, ports_found: 0, open_ports: 0, services_detected: 0 },
      }),
      row({
        id: 6, original_filename: 'sweep.xml', duration_seconds: 2,
        stats: { hosts_parsed: 4, hosts_up: 3, ports_found: 10, open_ports: 6, services_detected: 5 },
      }),
    ]));
    renderPage();
    const noPorts = (await screen.findByText('names.txt')).closest('tr')!;
    expect(within(noPorts).queryByText(/0\/0/)).not.toBeInTheDocument();
    expect(within(noPorts).queryByText('0.0s')).not.toBeInTheDocument();
    expect(within(noPorts).getByText('<0.1s')).toBeInTheDocument();
    const ports = screen.getByText('sweep.xml').closest('tr')!;
    expect(within(ports).getByText('6/10 open')).toBeInTheDocument();
    expect(within(ports).getByText('3/4 up')).toBeInTheDocument();
  });

  it('asks for 25 rows a page and sits in a section, not a card', async () => {
    renderPage();
    await waitFor(() => expect(api.getIngestionResults).toHaveBeenCalledWith(expect.objectContaining({ limit: 25 })));
    expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
  });

  it('the sort-direction button says what it does', async () => {
    renderPage();
    const button = await screen.findByRole('button', { name: /Sort direction: Newest first/ });
    expect(button).toHaveTextContent('Newest first');
    fireEvent.click(button);
    expect(await screen.findByRole('button', { name: /Sort direction: Oldest first/ })).toBeInTheDocument();
  });
});

// Local Network, 2026-09-24 (v5.289.0).
describe('Ingestion Results — superseded failures and specific reasons', () => {
  const generic = "Failed to parse the file 'smbmap-samba.txt'. The file format may not be supported or the file may be corrupted.";
  const smbmap = row({
    id: 478, parse_error_id: 18, status: 'failed', original_filename: 'smbmap-samba.txt',
    superseded_by_job_id: 484,
    failure_reason: 'SMBMap parser found 0 hosts in smbmap-samba.txt; file is empty or not smbmap output.',
    error: { error_type: 'parsing_error', error_message: 'SMBMap parser found 0 hosts in smbmap-samba.txt; file is empty or not smbmap output.', user_message: generic },
  });
  const withSuperseded = (items: unknown[]) => {
    const r = response(items);
    return { ...r, summary: { ...r.summary, total_superseded: 4 } };
  };

  it('says a later job imported the file, and gives the parser\'s cause rather than the generic sentence', async () => {
    api.getIngestionResults.mockResolvedValue(withSuperseded([smbmap]));
    renderPage();
    await screen.findByText('smbmap-samba.txt');
    const sup = screen.getByTestId('superseded-by');
    expect(sup).toHaveTextContent('Superseded — imported by job #484');
    expect(within(sup).getByRole('link', { name: 'job #484' })).toHaveAttribute('href', '/parse-errors?job_id=484');
    expect(screen.getByTestId('failure-reason')).toHaveTextContent('SMBMap parser found 0 hosts');
    expect(screen.getByTestId('failure-reason')).not.toHaveTextContent(/format may not be supported/);
  });

  it('has a Superseded count that filters, and dismisses exactly the superseded rows shown', async () => {
    api.getIngestionResults.mockResolvedValue(withSuperseded([
      smbmap,
      row({ id: 11, status: 'failed', original_filename: 'still-broken.xml' }),
      row({ id: 12, status: 'failed', original_filename: 'old.xml', superseded_by_job_id: 20, dismissed_at: '2026-09-01T00:00:00Z' }),
    ]));
    api.dismissSupersededJobs.mockResolvedValue({ dismissed: 1, job_ids: [478] });
    renderPage();
    const chip = await screen.findByRole('button', { name: /Superseded\s*4/ });
    fireEvent.click(chip);
    await waitFor(() => expect(api.getIngestionResults).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'superseded' }),
    ));

    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss 1 superseded' }));
    const dialog = await screen.findByRole('dialog');
    expect(api.dismissSupersededJobs).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Dismiss 1' }));
    await waitFor(() => expect(api.dismissSupersededJobs).toHaveBeenCalledWith([478]));
  });

  it('breaks a filename only after a separator', async () => {
    api.getIngestionResults.mockResolvedValue(response([
      row({ id: 5, original_filename: 'netexec-spider-172.30.77.10.json' }),
    ]));
    renderPage();
    const cell = await screen.findByText('netexec-spider-172.30.77.10.json');
    expect(cell.className).not.toMatch(/break-all/);
    // A break opportunity after every '-' and '.', none inside "10".
    expect(cell.innerHTML).toBe(
      'netexec-<wbr>spider-<wbr>172.<wbr>30.<wbr>77.<wbr>10.<wbr>json',
    );
  });

  it('gives every fixed column room for its header, under a minimum the content area holds', async () => {
    renderPage();
    await screen.findByText('No upload history yet');
    const table = document.querySelector('table')!;
    expect(table.className).toMatch(/min-w-\[1000px\]/);
    const widths = Array.from(table.querySelectorAll('thead th')).map((th) => th.className.match(/\bw-(\d+)\b/)?.[1]);
    // Filename (the third) takes the rest.
    expect(widths[2]).toBeUndefined();
    const px = widths.filter(Boolean).reduce((sum, w) => sum + Number(w) * 4, 0);
    expect(px).toBeLessThanOrEqual(1000 - 144);
    // "SERVICES" and "DURATION" do not fit an 80px column's 64px of text.
    const headers = Array.from(table.querySelectorAll('thead th'));
    for (const label of ['Services', 'Duration', 'Uploaded']) {
      const th = headers.find((h) => h.textContent === label)!;
      expect(Number(th.className.match(/\bw-(\d+)\b/)![1])).toBeGreaterThanOrEqual(24);
    }
  });
});
