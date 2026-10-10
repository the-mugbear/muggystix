/**
 * The Scans page follows scans it did not start itself (an agent's, another
 * tab's) by a small marker — how many scans, and the newest one's id — read
 * every 15 s (v5.207.0):
 *   - the first answer is the baseline, not a change;
 *   - when it moves, the lists, the counts and the queue are read again, once;
 *   - a delete made HERE moves it too, and already reads the lists again: the
 *     marker's value after the delete is the new baseline, not a second reason.
 *
 * Written before the marker became one hook (plan B18, 2026-10-10) and green
 * on the page as it was.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
  useProject: () => ({ currentProject: { id: 1, name: 'Demo' } }),
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

const renderPage = () => render(
  <MemoryRouter initialEntries={['/scans']}>
    <TooltipProvider><Scans /></TooltipProvider>
  </MemoryRouter>,
);
const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const reads = () => ({
  history: api.getImportHistory.mock.calls.length,
  summary: api.getScansSummary.mock.calls.length,
  queue: api.getRecentIngestionJobs.mock.calls.length,
  staged: api.getStagedIngestionJobs.mock.calls.length,
});

describe('Scans — the inventory marker', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.clearAllMocks();
    api.getRecentIngestionJobs.mockResolvedValue([]);
    api.getStagedIngestionJobs.mockResolvedValue([]);
    api.getScansSummary.mockResolvedValue({ total_scans: 1, total_hosts: 9, up_hosts: 9, open_services: 12, tool_counts: { NMAP: 1 } });
    api.getScanInventoryMarker.mockResolvedValue({ count: 1, latest_id: 9 });
    api.getImportHistory.mockResolvedValue({
      items: [{ kind: 'scan', id: 9, at: '2026-09-19T11:00:00Z' }],
      total: 1, batch_total: 0, scan_total: 1, has_more: false,
    });
    api.getScans.mockResolvedValue([scan(9, 'newest.xml')]);
    api.getScanBatches.mockResolvedValue([]);
    api.deleteScan.mockReset();
    api.deleteScan.mockResolvedValue({});
    api.getScanDeletionImpact.mockReset();
    api.getScanDeletionImpact.mockResolvedValue({
      scan_id: 9, filename: 'newest.xml', hosts_removed: 4, hosts_kept: 1, sample_removed_ips: ['10.0.0.5'],
      ports_removed: 6, vulnerabilities_removed: 0, web_interfaces_removed: 0, hosts_with_work: 0, hosts_with_work_sample: [],
    });
  });
  afterEach(() => { vi.useRealTimers(); });

  it('the first answer is the baseline; a marker that moved reads the lists, the counts and the queue again — once', async () => {
    renderPage();
    await screen.findByText('newest.xml');
    await waitFor(() => expect(api.getScanInventoryMarker).toHaveBeenCalledTimes(1));
    await pass(50);
    const opened = reads();

    // The same marker, a poll later: nothing is read again.
    await pass(15_000);
    expect(api.getScanInventoryMarker).toHaveBeenCalledTimes(2);
    expect(reads()).toEqual(opened);

    // An agent imported a file.
    api.getScanInventoryMarker.mockResolvedValue({ count: 2, latest_id: 10 });
    await pass(15_000);
    await waitFor(() => expect(reads()).toEqual({
      history: opened.history + 1, summary: opened.summary + 1, queue: opened.queue + 1, staged: opened.staged + 1,
    }));

    // It stays where it moved to: once was enough.
    await pass(15_000);
    await pass(15_000);
    expect(reads()).toEqual({
      history: opened.history + 1, summary: opened.summary + 1, queue: opened.queue + 1, staged: opened.staged + 1,
    });
  });

  it('a newer scan with the same count (one deleted, one imported) is a move too', async () => {
    renderPage();
    await screen.findByText('newest.xml');
    await pass(50);
    const opened = reads();
    api.getScanInventoryMarker.mockResolvedValue({ count: 1, latest_id: 10 });
    await pass(15_000);
    await waitFor(() => expect(reads().history).toBe(opened.history + 1));
  });

  it('a delete made here reads the lists again once: the marker it moved is the new baseline', async () => {
    // (Under fake timers Radix's menu has not yet released the pointer when
    // its item is clicked: the check is about the widget, not this page.)
    const user = userEvent.setup({ skipHover: true, advanceTimers: vi.advanceTimersByTime, pointerEventsCheck: 0 });
    renderPage();
    await screen.findByText('newest.xml');
    await pass(50);
    const opened = reads();
    const markerReads = api.getScanInventoryMarker.mock.calls.length;

    await user.click(screen.getByRole('button', { name: 'More actions for newest.xml' }));
    await user.click(await screen.findByRole('menuitem', { name: /Delete scan/ }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).queryByText(/Calculating exactly/)).not.toBeInTheDocument());
    // The scan is gone on the server: so is its count, and the marker says so.
    api.getScanInventoryMarker.mockResolvedValue({ count: 0, latest_id: null });
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(api.deleteScan).toHaveBeenCalledTimes(1));

    // The delete reads the inventory again, and asks for the marker at once.
    await waitFor(() => expect(reads().history).toBeGreaterThan(opened.history));
    await waitFor(() => expect(api.getScanInventoryMarker).toHaveBeenCalledTimes(markerReads + 1));
    await pass(50);
    const afterDelete = reads();
    // ONCE, and the queue — which the delete did not change — not at all.
    // (Before the marker was one hook this held in the browser only: under
    // the tests' scheduler the page heard of the marker's new value before
    // the delete had made it the baseline, and read everything a second time.)
    expect(afterDelete.history).toBe(opened.history + 1);
    expect(afterDelete.queue).toBe(opened.queue);

    // The next polls report the marker the delete left: not a change.
    await pass(15_000);
    await pass(15_000);
    expect(reads()).toEqual(afterDelete);
  });
});
