/**
 * Deleting a scan removes the hosts only it saw, with the work people did on
 * them.  The dialog names those hosts and stays locked until the reader says
 * they reviewed them; with no such host it is the dialog it always was.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
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
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
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

const impact = (extra: Record<string, unknown> = {}) => ({
  scan_id: 9, filename: 'newest.xml', hosts_removed: 4, hosts_kept: 1,
  sample_removed_ips: ['10.0.0.5'], ports_removed: 6, vulnerabilities_removed: 0,
  web_interfaces_removed: 0, ...extra,
});

const withWork = (id: number, ip: string, work: Record<string, number>, hostname: string | null = null) => ({
  host_id: id, ip_address: ip, hostname, work,
});

const refusal = (count: number, message: string) => ({
  response: { status: 409, data: { detail: { error: 'hosts_with_work', hosts_with_work: count, message } } },
});

/** Opens the delete dialog for the newest scan and returns it once the preview has settled. */
async function openDeleteDialog() {
  render(
    <MemoryRouter initialEntries={['/scans']}>
      <TooltipProvider><Scans /></TooltipProvider>
    </MemoryRouter>,
  );
  const user = userEvent.setup({ skipHover: true });
  await screen.findByText('newest.xml');
  await user.click(screen.getByRole('button', { name: 'More actions for newest.xml' }));
  await user.click(await screen.findByRole('menuitem', { name: /Delete scan/ }));
  const dialog = await screen.findByRole('dialog');
  await waitFor(() => expect(within(dialog).queryByText(/Calculating exactly/)).not.toBeInTheDocument());
  return { user, dialog };
}

const deleteButton = (dialog: HTMLElement) => within(dialog).getByRole('button', { name: 'Delete' });

beforeEach(() => {
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
});

describe('Delete scan — hosts with no work on them', () => {
  it('is the dialog it was: no warning, no checkbox, and the delete sends no confirmation', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({ hosts_with_work: 0, hosts_with_work_sample: [] }));
    const { user, dialog } = await openDeleteDialog();

    expect(within(dialog).getByText(/seen only by this scan/)).toBeInTheDocument();
    expect(within(dialog).queryByTestId('hosts-with-work')).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument();
    expect(deleteButton(dialog)).toBeEnabled();

    await user.click(deleteButton(dialog));
    await waitFor(() => expect(api.deleteScan).toHaveBeenCalledTimes(1));
    expect(api.deleteScan).toHaveBeenCalledWith(9);
  });

  it('says how many DNS records and names go with the scan, and nothing when there are none', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({ dns_records_removed: 1204, dns_names_removed: 1 }));
    const { dialog } = await openDeleteDialog();

    expect(within(dialog).getByText(/DNS records from this scan/).textContent).toMatch(/^1,204 DNS records/);
    expect(within(dialog).getByText(/only this scan observed/).textContent).toMatch(/^1 name only/);
  });

  it('leaves the DNS lines out for a scan that observed no name, and for an older server', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({ dns_records_removed: 0 }));
    const { dialog } = await openDeleteDialog();

    expect(within(dialog).getByText(/seen only by this scan/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/DNS record/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/only this scan observed/)).not.toBeInTheDocument();
  });

  it('treats an older server, which sends neither field, as no work', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact());
    const { user, dialog } = await openDeleteDialog();

    expect(within(dialog).queryByTestId('hosts-with-work')).not.toBeInTheDocument();
    expect(deleteButton(dialog)).toBeEnabled();
    await user.click(deleteButton(dialog));
    await waitFor(() => expect(api.deleteScan).toHaveBeenCalledWith(9));
  });
});

describe('Delete scan — everything the scan brought', () => {
  it('states each figure apart: ports on removed and on kept hosts, observations removed and kept', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({
      ports_removed: 6, ports_removed_on_kept_hosts: 1, vulnerabilities_removed: 1204, vulnerabilities_kept: 2,
    }));
    const { dialog } = await openDeleteDialog();

    expect(within(dialog).getByText(/ports on those hosts/).textContent).toMatch(/^6 ports on those hosts/);
    expect(within(dialog).getByText(/only this\s+scan found, on hosts that stay/).textContent)
      .toMatch(/^1 port only this\s+scan found, on hosts that stay/);
    expect(within(dialog).getByText(/only this scan reported, on hosts that stay/).textContent)
      .toMatch(/^1,204 scanner observations only this scan reported, on hosts that stay/);
    expect(within(dialog).getByTestId('observations-kept')).toHaveTextContent(
      '2 scanner observations only this scan reported are kept: a finding or a proposal refers to them.',
    );
  });

  it('leaves out every line that is zero, and for an older server that sends none of them', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({ ports_removed: 0 }));
    const { dialog } = await openDeleteDialog();

    expect(within(dialog).queryByText(/ports? on those hosts/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/on hosts that stay/)).not.toBeInTheDocument();
    expect(within(dialog).queryByTestId('observations-kept')).not.toBeInTheDocument();
    expect(within(dialog).queryByTestId('import-running')).not.toBeInTheDocument();
  });

  it('says once what is not restored when hosts stay, and nothing when none does', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({ hosts_kept: 3 }));
    const first = await openDeleteDialog();
    expect(within(first.dialog).getByText(/another scan also has/).textContent)
      .toMatch(/^3 hosts another scan also has are kept\./);
    expect(within(first.dialog).getAllByTestId('not-restored')).toHaveLength(1);
    expect(within(first.dialog).getByTestId('not-restored')).toHaveTextContent(
      'What this scan overwrote on hosts, ports and observations that were already there '
      + '(OS, host name, service, script output, severity) is not restored: BlueStick keeps no previous value.',
    );
  });

  it('does not speak of overwritten values for a scan that touched no host that stays', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({ hosts_kept: 0 }));
    const { dialog } = await openDeleteDialog();

    expect(within(dialog).queryByTestId('not-restored')).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/another scan also has/)).not.toBeInTheDocument();
  });
});

describe('Delete scan — an import is running', () => {
  it('says which file before the click and keeps Delete disabled', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({
      import_running: true, import_running_filename: 'big-sweep.nessus',
    }));
    const { user, dialog } = await openDeleteDialog();

    expect(within(dialog).getByTestId('import-running')).toHaveTextContent(
      'An import of "big-sweep.nessus" is running in this project. Delete this scan when it finishes.',
    );
    expect(deleteButton(dialog)).toBeDisabled();
    await user.click(deleteButton(dialog));
    expect(api.deleteScan).not.toHaveBeenCalled();
  });

  it('shows the server\'s refusal in the dialog, reloads the preview and raises no failure toast', async () => {
    api.getScanDeletionImpact
      .mockResolvedValueOnce(impact())
      .mockResolvedValueOnce(impact({ import_running: true, import_running_filename: 'late.xml' }));
    api.deleteScan.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: {
          error: 'import_running',
          message: 'An import of "late.xml" is running in this project. Nothing was changed; try again when it finishes.',
        } },
      },
    });
    const { user, dialog } = await openDeleteDialog();

    await user.click(deleteButton(dialog));

    await waitFor(() => expect(api.getScanDeletionImpact).toHaveBeenCalledTimes(2));
    const notice = await within(dialog).findByTestId('import-running');
    expect(notice).toHaveTextContent(
      'An import of "late.xml" is running in this project. Nothing was changed; try again when it finishes.',
    );
    expect(notice).toHaveAttribute('role', 'alert');
    expect(toast.error).not.toHaveBeenCalled();
    await waitFor(() => expect(deleteButton(dialog)).toBeDisabled());
  });

  it('lets the reader try again when the import has finished by the time the preview reloads', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact());
    api.deleteScan.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: {
          error: 'import_running',
          message: 'An import is writing to this project right now. Nothing was changed; try again when it finishes.',
        } },
      },
    });
    const { user, dialog } = await openDeleteDialog();

    await user.click(deleteButton(dialog));
    expect(await within(dialog).findByTestId('import-running')).toHaveTextContent(/writing to this project right now/);
    await waitFor(() => expect(deleteButton(dialog)).toBeEnabled());

    await user.click(deleteButton(dialog));
    await waitFor(() => expect(api.deleteScan).toHaveBeenCalledTimes(2));
    expect(toast.success).toHaveBeenCalled();
  });
});

describe('Delete scan — hosts that carry work', () => {
  it('lists them with their work, links each to its page in a new tab, and waits for the tick', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({
      hosts_with_work: 5,
      hosts_with_work_sample: [
        withWork(41, '10.0.0.5', { notes: 2, tests: 1, evidence: 1 }, 'web01.corp.example'),
        withWork(42, '10.0.0.6', { findings: 1, widgets: 2 }),
      ],
    }));
    const { user, dialog } = await openDeleteDialog();

    const block = within(dialog).getByTestId('hosts-with-work');
    expect(block).toHaveTextContent('5 of the hosts this removes have work on them, which goes with them:');
    expect(block).toHaveTextContent('1 evidence record · 1 test · 2 notes');
    expect(block).toHaveTextContent('1 finding · 2 widgets');
    expect(block).toHaveTextContent('web01.corp.example');
    expect(block).toHaveTextContent('and 3 more');

    const link = within(block).getByRole('link', { name: '10.0.0.5' });
    expect(link).toHaveAttribute('href', '/hosts/41');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(within(block).getByRole('link', { name: '10.0.0.6' })).toHaveAttribute('href', '/hosts/42');

    // The block comes before the buttons, and the delete is locked.
    expect(block.compareDocumentPosition(deleteButton(dialog)) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(deleteButton(dialog)).toBeDisabled();
    await user.click(deleteButton(dialog));
    expect(api.deleteScan).not.toHaveBeenCalled();

    await user.click(within(block).getByRole('checkbox', { name: /I have reviewed these hosts/ }));
    expect(deleteButton(dialog)).toBeEnabled();
    await user.click(deleteButton(dialog));
    await waitFor(() => expect(api.deleteScan).toHaveBeenCalledWith(9, { confirmHostsWithWork: true }));
  });

  it('says nothing about "more" when the sample is every such host, and speaks of one host in the singular', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({
      hosts_with_work: 1,
      hosts_with_work_sample: [withWork(41, '10.0.0.5', { notes: 1 })],
    }));
    const { dialog } = await openDeleteDialog();

    const block = within(dialog).getByTestId('hosts-with-work');
    expect(block).toHaveTextContent('1 of the hosts this removes has work on it, which goes with it:');
    expect(block).not.toHaveTextContent(/more/);
    expect(within(block).getByRole('checkbox', { name: /I have reviewed this host; delete its work/ })).toBeInTheDocument();
  });

  it('keeps 50 hosts and a 200-character hostname inside the dialog', async () => {
    const longName = `${'a'.repeat(190)}.long.test`;
    api.getScanDeletionImpact.mockResolvedValue(impact({
      hosts_removed: 80,
      hosts_with_work: 72,
      hosts_with_work_sample: Array.from({ length: 50 }, (_, i) =>
        withWork(100 + i, `10.0.1.${i}`, { notes: 1, tests: 2, evidence: 3, findings: 4, tags: 5 }, longName)),
    }));
    const { dialog } = await openDeleteDialog();

    const list = within(dialog).getByTestId('hosts-with-work-list');
    expect(within(list).getAllByRole('listitem')).toHaveLength(50);
    expect(list).toHaveClass('max-h-40', 'overflow-y-auto', 'overflow-x-hidden');
    const row = within(list).getAllByRole('listitem')[0];
    expect(row).toHaveClass('min-w-0');
    const name = within(row).getByTitle(longName);
    expect(name).toHaveClass('truncate', 'min-w-0');
    // What is lost is the point of the warning: it wraps and is never cut off.
    const work = within(row).getByText(/4 findings · 3 evidence records · 2 tests · 1 note · 5 tags/);
    expect(work).toHaveClass('break-words');
    expect(work).not.toHaveClass('truncate');
    expect(within(dialog).getByTestId('hosts-with-work')).toHaveTextContent('and 22 more');
  });
});

describe('Delete scan — the server refuses for hosts with work', () => {
  it('reloads the preview, un-ticks the box and shows the server\'s message instead of a failure', async () => {
    api.getScanDeletionImpact
      .mockResolvedValueOnce(impact({
        hosts_with_work: 1,
        hosts_with_work_sample: [withWork(41, '10.0.0.5', { notes: 1 })],
      }))
      .mockResolvedValueOnce(impact({
        hosts_with_work: 2,
        hosts_with_work_sample: [
          withWork(41, '10.0.0.5', { notes: 1 }),
          withWork(42, '10.0.0.6', { tests: 1 }),
        ],
      }));
    api.deleteScan.mockRejectedValueOnce(refusal(2, '2 hosts this scan removes have work on them.'));
    const { user, dialog } = await openDeleteDialog();

    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(deleteButton(dialog));

    await waitFor(() => expect(api.getScanDeletionImpact).toHaveBeenCalledTimes(2));
    const block = await within(dialog).findByTestId('hosts-with-work');
    await waitFor(() => expect(within(block).getByRole('link', { name: '10.0.0.6' })).toBeInTheDocument());
    expect(within(block).getByRole('alert')).toHaveTextContent('2 hosts this scan removes have work on them.');
    expect(within(block).getByRole('checkbox')).not.toBeChecked();
    expect(deleteButton(dialog)).toBeDisabled();
    expect(toast.error).not.toHaveBeenCalled();

    // The reader reviews the new list and deletes.
    await user.click(within(block).getByRole('checkbox'));
    await user.click(deleteButton(dialog));
    await waitFor(() => expect(api.deleteScan).toHaveBeenCalledTimes(2));
    expect(api.deleteScan).toHaveBeenLastCalledWith(9, { confirmHostsWithWork: true });
  });

  it('a preview that could not be read is not "no work": the refusal asks for the tick', async () => {
    api.getScanDeletionImpact.mockRejectedValue(new Error('503'));
    api.deleteScan.mockRejectedValueOnce(refusal(3, '3 hosts this scan removes have work on them.'));
    const { user, dialog } = await openDeleteDialog();

    expect(within(dialog).getByText(/Couldn't load the removal summary/)).toBeInTheDocument();
    await user.click(deleteButton(dialog));
    expect(api.deleteScan).toHaveBeenCalledWith(9);

    const block = await within(dialog).findByTestId('hosts-with-work');
    expect(block).toHaveTextContent('3 hosts this scan removes have work on them.');
    expect(block).toHaveTextContent('3 of the hosts this removes have work on them, which goes with them.');
    expect(deleteButton(dialog)).toBeDisabled();
    await user.click(within(block).getByRole('checkbox'));
    await user.click(deleteButton(dialog));
    await waitFor(() => expect(api.deleteScan).toHaveBeenLastCalledWith(9, { confirmHostsWithWork: true }));
  });

  it('the delete waits while the preview is still loading', async () => {
    api.getScanDeletionImpact.mockReturnValue(new Promise(() => {}));
    render(
      <MemoryRouter initialEntries={['/scans']}>
        <TooltipProvider><Scans /></TooltipProvider>
      </MemoryRouter>,
    );
    const user = userEvent.setup({ skipHover: true });
    await screen.findByText('newest.xml');
    await user.click(screen.getByRole('button', { name: 'More actions for newest.xml' }));
    await user.click(await screen.findByRole('menuitem', { name: /Delete scan/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Calculating exactly/)).toBeInTheDocument();
    expect(deleteButton(dialog)).toBeDisabled();
  });

  it('any other failure keeps the dialog and says what failed', async () => {
    api.getScanDeletionImpact.mockResolvedValue(impact({ hosts_with_work: 0, hosts_with_work_sample: [] }));
    api.deleteScan.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'An import is using this project.' } } });
    const { user, dialog } = await openDeleteDialog();

    await user.click(deleteButton(dialog));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('An import is using this project.'));
    expect(api.getScanDeletionImpact).toHaveBeenCalledTimes(1);
    expect(within(dialog).queryByTestId('hosts-with-work')).not.toBeInTheDocument();
  });
});
