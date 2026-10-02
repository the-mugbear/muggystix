/**
 * Review 2026-10-01 R32 / R34 — Scans had no role check at all: a project
 * viewer or auditor saw Upload, Retry, Cancel, Discard, Dismiss and Delete,
 * and learned from the 403.  And "Dismiss" on a failed job failed silently.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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
const role = vi.hoisted(() => ({ value: 'viewer' as string }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: role.value }, refreshProjects: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 5, role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));

import Scans from '../../pages/Scans';
import { TooltipProvider } from '../../components/ui/tooltip';

const scan = (id: number, filename: string) => ({
  id, filename, tool_name: 'nmap', scan_type: 'port_scan', created_at: '2026-09-19T10:00:00Z',
  total_hosts: 3, up_hosts: 3, new_hosts: 1, updated_hosts: 2, total_ports: 4, open_ports: 4,
});
const job = (id: number, status: string, filename: string) => ({
  id, original_filename: filename, status, file_size: 10, tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
  scan_id: null, parse_error_id: null, error_message: status === 'failed' ? 'bad file' : null, batch_id: null,
});

const renderPage = () => render(
  <MemoryRouter initialEntries={['/scans']}><TooltipProvider><Scans /></TooltipProvider></MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.setItem('nm.scans.queueOpen', '1');
  role.value = 'viewer';
  api.getRecentIngestionJobs.mockResolvedValue([job(1, 'failed', 'broken.xml'), job(2, 'queued', 'waiting.xml')]);
  api.getStagedIngestionJobs.mockResolvedValue([job(3, 'staged', 'staged.xml')]);
  api.getScansSummary.mockResolvedValue({ total_scans: 1, total_hosts: 3, up_hosts: 3, open_services: 4, tool_counts: { NMAP: 1 } });
  api.getScanInventoryMarker.mockResolvedValue({ count: 1, latest_id: 9 });
  api.getImportHistory.mockResolvedValue({
    items: [{ kind: 'scan', id: 9, at: '2026-09-19T11:00:00Z' }], total: 1, batch_total: 0, scan_total: 1, has_more: false,
  });
  api.getScans.mockResolvedValue([scan(9, 'newest.xml')]);
  api.getScanBatches.mockResolvedValue([]);
});

describe('Scans — a project viewer reads the import history', () => {
  it('shows the history and the queue without any write control', async () => {
    renderPage();
    await screen.findByText('newest.xml');
    await screen.findByText('broken.xml');
    await screen.findByText('staged.xml');

    expect(screen.queryByRole('button', { name: /Upload scans/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Review \d+ waiting/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Discard/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Retry failed ingestion/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Dismiss failed ingestion/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Cancel ingestion/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Review format and import/ })).toBeNull();
    // Reading stays: comparing scans changes nothing.
    expect(screen.getByRole('button', { name: /Compare scans/ })).toBeInTheDocument();
  });

  it('an auditor is a reader here too', async () => {
    role.value = 'auditor';
    renderPage();
    await screen.findByText('broken.xml');
    expect(screen.queryByRole('button', { name: /Upload scans/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Retry failed ingestion/ })).toBeNull();
  });

  it('an analyst gets the controls', async () => {
    role.value = 'analyst';
    renderPage();
    await screen.findByText('broken.xml');
    expect(screen.getByRole('button', { name: /Upload scans/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry failed ingestion for broken.xml/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Dismiss failed ingestion for broken.xml/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cancel ingestion for waiting.xml/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Discard staged upload staged.xml/ })).toBeInTheDocument();
  });

  // 5.328.2 — deleting a scan is the project ADMIN's on the server
  // (`deletion-impact` and `DELETE /scans/{id}`); an analyst was offered it
  // and got a 403 on the click.
  it.each([['analyst', false], ['admin', true]] as const)(
    'offers "Delete scan" in the row menu to a project %s: %s', async (as, offered) => {
      role.value = as;
      renderPage();
      await screen.findByText('newest.xml');
      const user = userEvent.setup({ skipHover: true });
      await user.click(screen.getByRole('button', { name: 'More actions for newest.xml' }));
      await screen.findByRole('menuitem', { name: /Open scan/ });
      expect(screen.queryAllByRole('menuitem', { name: /Delete scan/ })).toHaveLength(offered ? 1 : 0);
    },
  );
});

describe('Scans — a failed Dismiss is said (R34)', () => {
  it('toasts the server’s reason instead of doing nothing', async () => {
    role.value = 'analyst';
    api.dismissIngestionJob.mockRejectedValue({ response: { status: 403, data: { detail: 'Only the uploader may dismiss it.' } } });
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Dismiss failed ingestion for broken.xml/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Only the uploader may dismiss it.'));
  });
});
