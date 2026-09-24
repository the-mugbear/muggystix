/**
 * The Scans page's import history (v5.239.0): upload batches and single files
 * in ONE chronological table, in the server's order, with a prominent
 * Grouped-by-upload / All-files selector.  First page-level test of Scans.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Everything the page (and the components it mounts) takes from the barrel.
// Unlisted functions resolve to an empty list so a new import cannot break
// this test for a reason unrelated to what it checks.
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

import Scans from '../../pages/Scans';
import { TooltipProvider } from '../../components/ui/tooltip';

const scan = (id: number, filename: string) => ({
  id, filename, tool_name: 'nmap', scan_type: 'port_scan', created_at: '2026-09-19T10:00:00Z',
  total_hosts: 3, up_hosts: 3, new_hosts: 1, updated_hosts: 2, total_ports: 4, open_ports: 4,
});
const batch = (id: number, label: string) => ({
  id, label, recon_session_id: null, created_by: 'me', files: 12, tools: ['nmap'], hosts: 40,
  new_hosts: 5, open_ports: 90, first_uploaded: '2026-09-19T09:00:00Z', last_uploaded: '2026-09-19T09:30:00Z',
  pending_files: 0, failed_files: 0,
});

const renderPage = (path = '/scans') =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <TooltipProvider><Scans /></TooltipProvider>
    </MemoryRouter>,
  );

/** The page lead's sentence (PostureLead's paragraph), as one string. */
const leadText = () => document.querySelector('.border-l-4 > p')?.textContent ?? '';

beforeEach(() => {
  vi.clearAllMocks();
  api.getRecentIngestionJobs.mockResolvedValue([]);
  api.getStagedIngestionJobs.mockResolvedValue([]);
  api.getScansSummary.mockResolvedValue({ total_scans: 3, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 3 } });
  api.getScanInventoryMarker.mockResolvedValue({ count: 3, latest_id: 9 });
  api.getImportHistory.mockResolvedValue({
    items: [
      { kind: 'scan', id: 9, at: '2026-09-19T11:00:00Z' },
      { kind: 'batch', id: 4, at: '2026-09-19T09:30:00Z' },
      { kind: 'scan', id: 3, at: '2026-09-19T08:00:00Z' },
    ],
    total: 3, batch_total: 1, scan_total: 2, has_more: false,
  });
  // Each endpoint answers in its own order; the page must not use it.
  api.getScans.mockResolvedValue([scan(3, 'older.xml'), scan(9, 'newest.xml')]);
  api.getScanBatches.mockResolvedValue([batch(4, 'DMZ sweep')]);
});

describe('Scans — filter by uploader (v5.281.0)', () => {
  const summary = (uploaders: { user_id: number; username: string; files: number }[]) => ({
    total_scans: 3, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 3 }, uploaders,
  });

  it('offers the uploader filter only when there is more than one uploader', async () => {
    api.getScansSummary.mockResolvedValue(summary([{ user_id: 1, username: 'ana', files: 3 }]));
    const { unmount } = renderPage();
    await screen.findByText('newest.xml');
    expect(screen.queryByRole('combobox', { name: /filter scans by uploader/i })).not.toBeInTheDocument();
    unmount();

    api.getScansSummary.mockResolvedValue(summary([
      { user_id: 1, username: 'ana', files: 2 },
      { user_id: 7, username: 'ben', files: 1 },
    ]));
    renderPage();
    expect(await screen.findByRole('combobox', { name: /filter scans by uploader/i })).toHaveTextContent('Uploaded by anyone');
  });

  it('a link with an uploader filters the history, its rows and the totals', async () => {
    api.getScansSummary.mockResolvedValue(summary([
      { user_id: 1, username: 'ana', files: 2 },
      { user_id: 7, username: 'ben', files: 1 },
    ]));
    renderPage('/scans?uploaded_by=7');
    expect(await screen.findByRole('combobox', { name: /filter scans by uploader/i })).toHaveTextContent('ben (1)');
    await waitFor(() => {
      expect(api.getImportHistory).toHaveBeenCalledWith(expect.objectContaining({ uploadedBy: 7 }));
      expect(api.getScansSummary).toHaveBeenCalledWith(expect.objectContaining({ uploadedBy: 7 }));
      expect(api.getScanBatches).toHaveBeenCalledWith(expect.objectContaining({ uploadedBy: 7 }));
    });
  });

  it('shows the uploader\'s full name, keeping the id as the value', async () => {
    api.getScansSummary.mockResolvedValue({
      total_scans: 3, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 3 },
      uploaders: [
        { user_id: 1, username: 'ana', full_name: 'Ana Analyst', files: 2 },
        { user_id: 7, username: 'ben', full_name: null, files: 1 },
      ],
    });
    renderPage('/scans?uploaded_by=1');
    const chooser = await screen.findByRole('combobox', { name: /filter scans by uploader/i });
    expect(chooser).toHaveTextContent('Ana Analyst (2)');
    expect(chooser).not.toHaveTextContent('ana (2)');
  });
});

// Screenshot 2026-09-23: "Sep 7, 2026, 04:16 P…" / "uploaded · run time un…".
describe('Scans — the When column', () => {
  it('wraps the time and its note instead of cutting them off', async () => {
    renderPage();
    await screen.findByText('newest.xml');
    const note = screen.getAllByText('uploaded · run time unknown')[0];
    expect(note).not.toHaveClass('truncate');
    expect(note).toHaveClass('break-words');
    expect(note.previousElementSibling).not.toHaveClass('truncate');
  });
});

describe('Scans — import history', () => {
  it('lists batches and single files in one table, in the server\'s order', async () => {
    renderPage();
    const newest = await screen.findByText('newest.xml');
    const table = newest.closest('table') as HTMLTableElement;
    const labels = within(table).getAllByText(/newest\.xml|DMZ sweep|older\.xml/).map((el) => el.textContent);
    expect(labels).toEqual(['newest.xml', 'DMZ sweep', 'older.xml']);
    // One table: the batch is a row of it, not a card of its own.
    expect(within(table).getByText('Upload batch · Uploaded by me')).toBeInTheDocument();
    expect(screen.queryByText('Individual uploads')).not.toBeInTheDocument();

    // Rows are fetched by id for the page the server ordered.
    expect(api.getScans).toHaveBeenCalledWith(0, 2, { ids: [9, 3] });
    expect(api.getScanBatches).toHaveBeenCalledWith(expect.objectContaining({ ids: [4] }));
    // v5.270.0 — how much is loaded sits at the end of the one filter row.
    expect(screen.getByText(/^3 uploads · 1 batch, 2 single files/)).toBeInTheDocument();
  });

  it('the view selector switches to one flat, sortable list of every file', async () => {
    renderPage();
    await screen.findByText('newest.xml');
    const selector = screen.getByRole('group', { name: 'How the import history is listed' });
    expect(within(selector).getByRole('button', { name: 'Grouped by upload' })).toHaveAttribute('aria-pressed', 'true');
    // Chronological by definition: no sort controls in the grouped view.
    expect(screen.queryByRole('button', { name: /Sort by Scan/ })).not.toBeInTheDocument();

    api.getScans.mockResolvedValue([scan(9, 'newest.xml'), scan(21, 'in-batch.xml'), scan(3, 'older.xml')]);
    fireEvent.click(within(selector).getByRole('button', { name: 'All files' }));

    expect(await screen.findByText('in-batch.xml')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('DMZ sweep')).not.toBeInTheDocument());
    expect(api.getScans).toHaveBeenLastCalledWith(0, 250, expect.objectContaining({ unbatched: false }));
    expect(screen.getByRole('button', { name: /Sort by Scan/ })).toBeInTheDocument();
    // One time column that sorts by either time.
    expect(screen.getByRole('button', { name: /Sort by Ran/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sort by Uploaded/ })).toBeInTheDocument();
  });
});

// v5.270.0 — the page is a lead sentence and a section, not stat cards and
// boxes; the table's actions are quiet.
describe('Scans — layout', () => {
  it('opens with one sentence and no stat cards or card-wrapped table', async () => {
    renderPage();
    await screen.findByText('newest.xml');
    expect(screen.queryByText('Hosts up')).not.toBeInTheDocument();
    expect(screen.queryByText('Open services')).not.toBeInTheDocument();
    expect(screen.queryByText('Queue active')).not.toBeInTheDocument();
    expect(leadText()).toMatch(/files? imported · nothing failed · last import/);
    expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
  });

  it('points at failed imports from the lead, and shows the queue only while it holds something', async () => {
    api.getRecentIngestionJobs.mockResolvedValue([
      { id: 71, status: 'failed', original_filename: 'broken.xml', created_at: '2026-09-19T10:00:00Z', message: 'not XML' },
    ]);
    renderPage();
    const link = await screen.findByRole('link', { name: /1 failed import needs attention/ });
    expect(link).toHaveAttribute('href', '/parse-errors?status=needs_attention');
    expect(screen.getByTestId('ingestion-queue')).toHaveTextContent('1 failed');
  });

  // v5.271.0 — a closed review of 26 files: the queue read the 25 most recent
  // jobs, so the oldest file had no Review and no Discard, and the only
  // visible action on the strip was "Discard 25 staged".
  it('reviews and discards every staged file, not just those among the recent jobs', async () => {
    const staged = (id: number) => ({
      id, status: 'staged', original_filename: `file-${id}.txt`, file_size: 300, batch_id: 4,
      created_at: `2026-09-19T10:0${id}:00Z`,
    });
    api.getRecentIngestionJobs.mockResolvedValue([staged(3), staged(2)]);
    api.getStagedIngestionJobs.mockResolvedValue([staged(3), staged(2), staged(1)]);
    api.getJobDetection.mockImplementation(async (jobId: number) => ({
      job_id: jobId, filename: `file-${jobId}.txt`,
      candidates: [{ file_type: 'naabu_output', label: 'Naabu host:port text', basis: 'structure', rank: 0 }],
      primary: 'naabu_output', needs_choice: false, reason: null,
      preview: { raw: '', sample: [] }, formats: [],
    }));
    renderPage();

    const queue = await screen.findByTestId('ingestion-queue');
    expect(queue).toHaveTextContent('3 waiting for review');
    expect(within(queue).getByRole('button', { name: 'Discard 3 staged' })).toBeInTheDocument();

    fireEvent.click(within(queue).getByRole('button', { name: 'Review 3 waiting' }));
    const dialog = await screen.findByRole('dialog');
    // The oldest file — the one outside the recent list — is in the review.
    expect(await within(dialog).findByText('file-1.txt')).toBeInTheDocument();
    await waitFor(() => expect(api.getJobDetection).toHaveBeenCalledTimes(3));
    expect(api.uploadFile).not.toHaveBeenCalled();
    expect(await within(dialog).findByRole('button', { name: 'Import 3 ready files' })).toBeEnabled();
  });

  it('has no queue box when nothing is queued, waiting or failed', async () => {
    renderPage();
    await screen.findByText('newest.xml');
    expect(screen.queryByTestId('ingestion-queue')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ingestion queue' })).not.toBeInTheDocument();
  });

  // Screenshot 2026-09-23: "nothing failed" beside a batch whose 31 files had
  // all expired — the lead read only the 25 most recent jobs.
  it('counts the whole project\'s failures from the summary, not the recent queue', async () => {
    api.getScansSummary.mockResolvedValue({
      total_scans: 3, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 3 },
      imports_need_attention: 78, imports_not_imported: 31,
    });
    renderPage();
    const link = await screen.findByRole('link', { name: /78 failed or partial imports need attention/ });
    expect(link).toHaveAttribute('href', '/parse-errors?status=needs_attention');
    expect(screen.queryByText(/nothing failed/)).not.toBeInTheDocument();
  });

  it('never says "nothing failed" while dismissed failures exist; says they were never imported', async () => {
    api.getScansSummary.mockResolvedValue({
      total_scans: 3, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 3 },
      imports_need_attention: 0, imports_not_imported: 31,
    });
    renderPage();
    const link = await screen.findByRole('link', { name: '31 never imported' });
    expect(link).toHaveAttribute('href', '/parse-errors?status=failed');
    expect(leadText()).not.toMatch(/nothing failed/);
  });

  // Screenshot 2026-09-23: a run-on sentence listing every possible reason
  // ("discarded, expired before review, or a dismissed failure") whatever
  // had actually happened, plus a two-line paragraph under it.
  it('names the actual reasons files were never imported, with counts, and keeps the lead short', async () => {
    api.getScansSummary.mockResolvedValue({
      total_scans: 40, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 40 },
      imports_need_attention: 0, imports_not_imported: 33,
      imports_not_imported_by_reason: { expired: 31, discarded: 2 },
    });
    renderPage();
    await screen.findByRole('link', { name: '33 never imported' });
    const text = leadText();
    expect(text).toMatch(/imported · 33 never imported \(31 expired before review, 2 discarded\) · last import/);
    expect(text).not.toMatch(/dismissed failure/);
    expect(text).not.toMatch(/none needs attention/);
    // The explanation is an info tip, not a paragraph under the lead.
    expect(screen.getByRole('button', { name: 'About these figures' })).toBeInTheDocument();
    expect(screen.queryByText(/Every imported file counts/)).not.toBeInTheDocument();
  });

  it('says "nothing failed" only when the project has no failed job at all', async () => {
    api.getScansSummary.mockResolvedValue({
      total_scans: 3, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 3 },
      imports_need_attention: 0, imports_not_imported: 0,
    });
    renderPage();
    await waitFor(() => expect(leadText()).toMatch(/imported · nothing failed/));
  });

  it('says so when the queue could not be read — never "nothing failed"', async () => {
    api.getRecentIngestionJobs.mockRejectedValue(new Error('503'));
    renderPage();
    expect(await screen.findByText(/the ingestion queue could not be checked/)).toBeInTheDocument();
    expect(screen.queryByText(/nothing failed/)).not.toBeInTheDocument();
  });

  it('the filename opens the scan; delete is in the row menu, not a button on the row', async () => {
    renderPage();
    const name = await screen.findByText('newest.xml');
    expect(name.closest('a')).toHaveAttribute('href', '/scans/9');
    expect(screen.queryByRole('button', { name: /^View$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Delete scan newest\.xml/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Hosts' })[0]).toHaveAttribute('href', expect.stringContaining('/hosts?scan_ids='));
    const user = userEvent.setup({ skipHover: true });
    await user.click(screen.getByRole('button', { name: 'More actions for newest.xml' }));
    await user.click(await screen.findByRole('menuitem', { name: /Delete scan/ }));
    // Delete still goes through its confirmation (the impact dialog).
    await waitFor(() => expect(api.getScanDeletionImpact).toHaveBeenCalledWith(9));
    expect(api.deleteScan).not.toHaveBeenCalled();
  });
});

// Review 2026-09-23 R11: a failed load read "No scans uploaded yet".
describe('Scans — a failed load', () => {
  it('says the load failed, not that the project is empty, and retries', async () => {
    api.getImportHistory.mockRejectedValueOnce(new Error('boom'));
    renderPage();
    const alert = await screen.findByTestId('history-error');
    expect(alert).toHaveTextContent(/Could not load the import history/);
    expect(screen.queryByText('No scans uploaded yet')).not.toBeInTheDocument();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('newest.xml')).toBeInTheDocument();
    expect(screen.queryByTestId('history-error')).not.toBeInTheDocument();
  });
});
