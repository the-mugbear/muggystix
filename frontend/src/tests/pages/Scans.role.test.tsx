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
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: role.value } }),
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

// The queue's actions had no busy state: the control stayed live while its
// request was in flight, so a double click sent it twice.
describe('Scans — a queue action is sent once', () => {
  const held = () => {
    let release!: (v?: unknown) => void;
    const promise = new Promise((resolve) => { release = resolve; });
    return { promise, release };
  };
  const button = (name: RegExp) => screen.getByRole('button', { name });

  beforeEach(() => {
    role.value = 'analyst';
    api.getRecentIngestionJobs.mockResolvedValue([
      job(1, 'failed', 'broken.xml'), job(4, 'failed', 'other.xml'), job(2, 'queued', 'waiting.xml'),
    ]);
  });

  it.each([
    ['Retry', /Retry failed ingestion for broken.xml/, 'retryIngestionJob', 1],
    ['Dismiss', /Dismiss failed ingestion for broken.xml/, 'dismissIngestionJob', 1],
    ['Discard', /Discard staged upload staged.xml/, 'discardIngestionJob', 3],
  ] as const)('%s: a second click while the first is being answered sends nothing', async (_what, name, fn, id) => {
    const answer = held();
    api[fn].mockReturnValue(answer.promise);
    renderPage();
    await screen.findByText('broken.xml');
    await screen.findByText('staged.xml');

    fireEvent.click(button(name));
    await waitFor(() => expect(button(name)).toBeDisabled());
    fireEvent.click(button(name));
    expect(api[fn]).toHaveBeenCalledTimes(1);
    expect(api[fn]).toHaveBeenCalledWith(1, id);
    // Only this job's controls: the rest of the queue is still the reader's.
    expect(button(/Retry failed ingestion for other.xml/)).toBeEnabled();
    expect(button(/Cancel ingestion for waiting.xml/)).toBeEnabled();

    answer.release({});
    await waitFor(() => expect(button(name)).toBeEnabled());
  });

  it('both actions of a failed job wait for the one in flight, and two jobs can be in flight at once', async () => {
    const first = held();
    const second = held();
    api.retryIngestionJob.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    renderPage();
    await screen.findByText('broken.xml');

    fireEvent.click(button(/Retry failed ingestion for broken.xml/));
    await waitFor(() => expect(button(/Retry failed ingestion for broken.xml/)).toBeDisabled());
    expect(button(/Dismiss failed ingestion for broken.xml/)).toBeDisabled();

    // A second job is retried while the first is unanswered: the first stays busy.
    fireEvent.click(button(/Retry failed ingestion for other.xml/));
    await waitFor(() => expect(button(/Retry failed ingestion for other.xml/)).toBeDisabled());
    expect(button(/Retry failed ingestion for broken.xml/)).toBeDisabled();
    fireEvent.click(button(/Retry failed ingestion for broken.xml/));
    expect(api.retryIngestionJob).toHaveBeenCalledTimes(2);

    // The answer alone does not free the row: the queue is read again first,
    // because until then the row still shows the job as it was.
    const queue = held();
    api.getRecentIngestionJobs.mockReturnValueOnce(queue.promise);
    first.release({});
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('Re-queued for parsing'));
    expect(button(/Retry failed ingestion for broken.xml/)).toBeDisabled();
    queue.release([job(4, 'failed', 'other.xml'), job(1, 'queued', 'broken.xml')]);
    expect(await screen.findByRole('button', { name: /Cancel ingestion for broken.xml/ })).toBeEnabled();
    expect(button(/Retry failed ingestion for other.xml/)).toBeDisabled();
    second.release({});
  });

  it('Cancel: confirmed once, sent once', async () => {
    const answer = held();
    api.cancelIngestionJob.mockReturnValue(answer.promise);
    renderPage();
    await screen.findByText('waiting.xml');

    fireEvent.click(button(/Cancel ingestion for waiting.xml/));
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel job' }));
    await waitFor(() => expect(button(/Cancel ingestion for waiting.xml/)).toBeDisabled());
    expect(api.cancelIngestionJob).toHaveBeenCalledTimes(1);
    expect(button(/Retry failed ingestion for broken.xml/)).toBeEnabled();
    answer.release({});
    await waitFor(() => expect(button(/Cancel ingestion for waiting.xml/)).toBeEnabled());
  });

  it('"Discard N staged": the control and the staged rows’ own Discard wait for it', async () => {
    const answer = held();
    api.discardStagedJobs.mockReturnValue(answer.promise);
    renderPage();
    await screen.findByText('staged.xml');

    fireEvent.click(button(/^Discard 1 staged$/));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard 1' }));
    await waitFor(() => expect(button(/^Discard 1 staged$/)).toBeDisabled());
    expect(api.discardStagedJobs).toHaveBeenCalledTimes(1);
    expect(api.discardStagedJobs).toHaveBeenCalledWith(1, [3]);
    expect(button(/Discard staged upload staged.xml/)).toBeDisabled();
    expect(button(/Retry failed ingestion for broken.xml/)).toBeEnabled();

    answer.release({ discarded: 1 });
    await waitFor(() => expect(button(/^Discard 1 staged$/)).toBeEnabled());
  });
});
