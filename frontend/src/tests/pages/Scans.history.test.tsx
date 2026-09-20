/**
 * The Scans page's import history (v5.239.0): upload batches and single files
 * in ONE chronological table, in the server's order, with a prominent
 * Grouped-by-upload / All-files selector.  First page-level test of Scans.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/scans']}>
      <TooltipProvider><Scans /></TooltipProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.getRecentIngestionJobs.mockResolvedValue([]);
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
    expect(screen.getByText(/Showing 3 uploads: 1 batch, 2 single files/)).toBeInTheDocument();
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
  });
});
