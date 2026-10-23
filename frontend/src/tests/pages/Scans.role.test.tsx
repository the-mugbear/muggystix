/**
 * Review 2026-10-01 R32 / R34 — Scans had no role check at all: a project
 * viewer or auditor saw Upload, Retry, Cancel, Discard, Dismiss and Delete,
 * and learned from the 403.  And "Dismiss" on a failed job failed silently.
 *
 * Since 5.418.0 (owner decision 2026-10-22) a failed upload is not a row of
 * the queue strip: it is a row of the import history, for analysts and above,
 * with its Retry and Dismiss (`UploadHistoryRow.test.tsx` covers them, the
 * refused Dismiss included).  The strip lists only what is still moving.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
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
const historyAnswer = (statusCounts: Record<string, number> | null) => ({
  items: [{ kind: 'scan', id: 9, at: '2026-09-19T11:00:00Z' }], total: 1, batch_total: 0, scan_total: 1, has_more: false,
  status_counts: statusCounts,
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
  api.getScansSummary.mockResolvedValue({
    total_scans: 1, total_hosts: 3, up_hosts: 3, open_services: 4, tool_counts: { NMAP: 1 },
    imports_need_attention: 2, imports_not_imported: 0,
  });
  api.getScanInventoryMarker.mockResolvedValue({ count: 1, latest_id: 9 });
  // What the server answers a viewer or an auditor: no status counts.
  api.getImportHistory.mockResolvedValue(historyAnswer(null));
  api.getScans.mockResolvedValue([scan(9, 'newest.xml')]);
  api.getScanBatches.mockResolvedValue([]);
});

describe('Scans — a project viewer reads the import history', () => {
  it('shows the history and the queue without any write control, and nothing about failures', async () => {
    renderPage();
    await screen.findByText('newest.xml');
    await screen.findByText('waiting.xml');
    await screen.findByText('staged.xml');

    expect(screen.queryByRole('button', { name: /Upload scans/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Review \d+ waiting/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Discard/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Cancel ingestion/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Review format and import/ })).toBeNull();
    // Reading stays: comparing scans changes nothing.
    expect(screen.getByRole('button', { name: /Compare scans/ })).toBeInTheDocument();
    // A failed upload is no queue row, and the strip says nothing of failures
    // to someone who cannot open them.
    const queue = screen.getByTestId('ingestion-queue');
    expect(within(queue).queryByText('broken.xml')).toBeNull();
    expect(within(queue).queryByTestId('queue-failed-line')).toBeNull();
    expect(queue).not.toHaveTextContent(/failed/);
  });

  it('an auditor is a reader here too', async () => {
    role.value = 'auditor';
    renderPage();
    await screen.findByText('waiting.xml');
    expect(screen.queryByRole('button', { name: /Upload scans/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Cancel ingestion/ })).toBeNull();
    expect(screen.queryByTestId('queue-failed-line')).toBeNull();
  });

  it('an analyst gets the controls, and the one line about failed uploads', async () => {
    role.value = 'analyst';
    api.getImportHistory.mockResolvedValue(historyAnswer({ needs_attention: 2, failed: 1, failed_dismissed: 0 }));
    renderPage();
    await screen.findByText('waiting.xml');
    expect(screen.getByRole('button', { name: /Upload scans/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cancel ingestion for waiting.xml/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Discard staged upload staged.xml/ })).toBeInTheDocument();
    expect(await screen.findByTestId('queue-failed-line')).toHaveTextContent('2 imports need attention — review them below');
    expect(screen.queryByRole('button', { name: /Retry failed ingestion/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Dismiss failed ingestion/ })).toBeNull();
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
    api.getRecentIngestionJobs.mockResolvedValue([job(2, 'queued', 'waiting.xml')]);
    api.getStagedIngestionJobs.mockResolvedValue([job(3, 'staged', 'staged.xml'), job(4, 'staged', 'other.xml')]);
  });

  it('Discard: a second click while the first is being answered sends nothing', async () => {
    const answer = held();
    api.discardIngestionJob.mockReturnValue(answer.promise);
    renderPage();
    await screen.findByText('staged.xml');

    fireEvent.click(button(/Discard staged upload staged.xml/));
    await waitFor(() => expect(button(/Discard staged upload staged.xml/)).toBeDisabled());
    fireEvent.click(button(/Discard staged upload staged.xml/));
    expect(api.discardIngestionJob).toHaveBeenCalledTimes(1);
    expect(api.discardIngestionJob).toHaveBeenCalledWith(1, 3);
    // Only this job's controls: the rest of the queue is still the reader's.
    expect(button(/Discard staged upload other.xml/)).toBeEnabled();
    expect(button(/Cancel ingestion for waiting.xml/)).toBeEnabled();

    answer.release({});
    await waitFor(() => expect(button(/Discard staged upload staged.xml/)).toBeEnabled());
  });

  it('two jobs can be in flight at once, and a row stays busy until the queue is read again', async () => {
    const first = held();
    const second = held();
    api.discardIngestionJob.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    renderPage();
    await screen.findByText('staged.xml');

    fireEvent.click(button(/Discard staged upload staged.xml/));
    await waitFor(() => expect(button(/Discard staged upload staged.xml/)).toBeDisabled());
    fireEvent.click(button(/Discard staged upload other.xml/));
    await waitFor(() => expect(button(/Discard staged upload other.xml/)).toBeDisabled());
    expect(button(/Discard staged upload staged.xml/)).toBeDisabled();
    expect(api.discardIngestionJob).toHaveBeenCalledTimes(2);

    // The answer alone does not free the row: the queue is read again first,
    // because until then the row still shows the job as it was.
    const queue = held();
    api.getStagedIngestionJobs.mockReturnValueOnce(queue.promise);
    first.release({});
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith('Staged upload discarded'));
    expect(button(/Discard staged upload staged.xml/)).toBeDisabled();
    queue.release([job(4, 'staged', 'other.xml')]);
    await waitFor(() => expect(screen.queryByText('staged.xml')).toBeNull());
    expect(button(/Discard staged upload other.xml/)).toBeDisabled();
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
    expect(button(/Discard staged upload staged.xml/)).toBeEnabled();
    answer.release({});
    await waitFor(() => expect(button(/Cancel ingestion for waiting.xml/)).toBeEnabled());
  });

  it('"Discard N staged": the control and the staged rows’ own Discard wait for it', async () => {
    const answer = held();
    api.discardStagedJobs.mockReturnValue(answer.promise);
    renderPage();
    await screen.findByText('staged.xml');

    fireEvent.click(button(/^Discard 2 staged$/));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard 2' }));
    await waitFor(() => expect(button(/^Discard 2 staged$/)).toBeDisabled());
    expect(api.discardStagedJobs).toHaveBeenCalledTimes(1);
    expect(api.discardStagedJobs).toHaveBeenCalledWith(1, [3, 4]);
    expect(button(/Discard staged upload staged.xml/)).toBeDisabled();
    expect(button(/Cancel ingestion for waiting.xml/)).toBeEnabled();

    answer.release({ discarded: 2 });
    await waitFor(() => expect(button(/^Discard 2 staged$/)).toBeEnabled());
  });
});
