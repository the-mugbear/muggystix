import React from 'react';
import { useQueries } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
const api = vi.hoisted(() => ({
  listFindings: vi.fn(),
  getFinding: vi.fn(),
  setFindingStatus: vi.fn(),
  setFindingEndpointStatus: vi.fn(),
  createFinding: vi.fn(),
}));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ hasPermission: () => true }) }));
const projectRole = vi.hoisted(() => ({ value: 'analyst' as string }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: projectRole.value } }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../components/FindingHistoryButton', () => ({ FindingHistoryButton: () => null }));

import { readsOnScreen } from '../helpers/readsOnScreen';
import HostFindingsCard from '../../components/HostFindingsCard';

const HOST = 5;
const row = (id: number, hostId: number, status: string, fqdn: string | null = null) => ({
  id, host_id: hostId, ip_address: `10.0.0.${hostId}`, hostname: null, fqdn, host_status: status,
});
const finding = (over: Record<string, unknown>) => ({
  id: 7, title: 'Weak TLS', severity: 'high', status: 'confirmed', source: 'scanner',
  host_count: 1, hosts: [row(31, HOST, 'open')], evidence_annotation_id: null, ...over,
});

// The trail behind a finding's history button (mocked away above): a status
// or endpoint change from this card appended to it, so that read is out of
// date — the popover showed the trail as first read until the page was left.
const { reread: historyReread, ReadsOnScreen } = readsOnScreen({ getFindingHistory: 'history' });

const renderCard = () => render(
  <MemoryRouter><ReadsOnScreen /><HostFindingsCard hostId={HOST} /></MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  projectRole.value = 'analyst';
});

describe('HostFindingsCard', () => {
  it('a writer adds a finding on a host that has none, and is offered the write-up', async () => {
    const user = userEvent.setup();
    const made = finding({ id: 41, title: 'Shared local admin password', severity: 'medium', status: 'open', source: 'manual' });
    api.listFindings.mockResolvedValueOnce({ items: [] }).mockResolvedValue({ items: [made] });
    api.createFinding.mockResolvedValue(made);
    renderCard();

    expect(await screen.findByText('No finding is recorded on this host.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Add finding' }));
    const submit = screen.getByRole('button', { name: 'Add finding' });
    expect(submit).toBeDisabled();                       // a finding needs a title
    await user.type(screen.getByLabelText('Finding title'), '  Shared local admin password ');
    await user.click(submit);

    await waitFor(() => expect(api.createFinding).toHaveBeenCalledWith({
      title: 'Shared local admin password', severity: 'medium', status: 'open', host_ids: [HOST],
    }));
    // The list is read again, and the form is gone.
    expect(await screen.findByText('Shared local admin password')).toBeInTheDocument();
    expect(screen.queryByLabelText('Finding title')).toBeNull();
    const [, options] = toast.success.mock.calls[0];
    options.action.onClick();
    expect(navigate).toHaveBeenCalledWith('/findings/41?edit=report-text');
  });

  it('a refused finding says why and keeps what was typed', async () => {
    const user = userEvent.setup();
    api.listFindings.mockResolvedValue({ items: [] });
    api.createFinding.mockRejectedValue({ response: { data: { detail: 'Hosts [5] are not in this project.' } } });
    renderCard();
    await user.click(await screen.findByRole('button', { name: 'Add finding' }));
    await user.type(screen.getByLabelText('Finding title'), 'Open share');
    await user.click(screen.getByRole('button', { name: 'Add finding' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Hosts [5] are not in this project.');
    expect(screen.getByLabelText('Finding title')).toHaveValue('Open share');
  });

  it('someone who cannot write sees no section on a host without findings, and no Add finding on one with', async () => {
    projectRole.value = 'viewer';
    api.listFindings.mockResolvedValue({ items: [] });
    const { unmount } = renderCard();
    await waitFor(() => expect(api.listFindings).toHaveBeenCalled());
    expect(screen.queryByText('Findings')).toBeNull();
    unmount();

    api.listFindings.mockResolvedValue({ items: [finding({})] });
    renderCard();
    expect(await screen.findByText('Weak TLS')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add finding' })).toBeNull();
  });

  // Code review 2026-10-09: a failed read was shown as "No finding is
  // recorded on this host" to a writer, and as no section at all to a reader.
  it.each(['analyst', 'viewer'])('a failed read is said, with Retry — never "no finding" (%s)', async (role) => {
    projectRole.value = role;
    api.listFindings.mockRejectedValueOnce(new Error('boom')).mockResolvedValue({ items: [finding({})], total: 1 });
    renderCard();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/boom|Could not load this host’s findings/);
    expect(screen.queryByText('No finding is recorded on this host.')).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Weak TLS')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('says when the host has more findings than it shows', async () => {
    api.listFindings.mockResolvedValue({ items: [finding({})], total: 240 });
    renderCard();
    expect(await screen.findByText('Showing the first 1 of 240 findings on this host.')).toBeInTheDocument();
  });

  it('says when a finding\'s state on this host could not be read in full', async () => {
    // A cut preview that is all this host's rows needs the whole finding; that read fails.
    const listed = finding({ host_count: 9, hosts: [1, 2, 3, 4, 5].map((n) => row(40 + n, HOST, 'open', `n${n}.example.com`)) });
    api.listFindings.mockResolvedValue({ items: [listed], total: 1 });
    api.getFinding.mockRejectedValue(new Error('boom'));
    renderCard();
    expect(await screen.findByText(/state on 1 finding could not be read in full/)).toBeInTheDocument();
    expect(screen.getByText('Weak TLS')).toBeInTheDocument();
  });

  it('a finding shared with other hosts is not re-judged from here: the control is THIS host\'s state', async () => {
    const user = userEvent.setup();
    const shared = finding({ host_count: 3, hosts: [row(31, HOST, 'open'), row(32, 6, 'open'), row(33, 8, 'open')] });
    api.listFindings.mockResolvedValue({ items: [shared] });
    api.setFindingEndpointStatus.mockResolvedValue({ ...shared, hosts: [row(31, HOST, 'false_positive'), row(32, 6, 'open'), row(33, 8, 'open')] });
    renderCard();

    // The issue's status is read here and changed on the finding's page.
    const issue = await screen.findByRole('button', { name: /Weak TLS: Confirmed across 3 hosts — open the finding/ });
    expect(issue).toHaveTextContent('Confirmed · 3 hosts');
    expect(screen.queryByLabelText('Status for Weak TLS')).not.toBeInTheDocument();

    await user.click(screen.getByLabelText('State of Weak TLS on this host'));
    await user.click(await screen.findByRole('option', { name: 'False positive here' }));

    await waitFor(() => expect(api.setFindingEndpointStatus).toHaveBeenCalledWith(7, 31, 'false_positive'));
    // Only this host's row — never the other hosts', never the issue.
    expect(api.setFindingEndpointStatus).toHaveBeenCalledTimes(1);
    expect(api.setFindingStatus).not.toHaveBeenCalled();
    // The change is on the finding's history: that trail is read again.
    await waitFor(() => expect(historyReread).toHaveBeenCalledTimes(1));

    await user.click(issue);
    expect(navigate).toHaveBeenCalledWith('/findings/7');
  });

  it('sets every named endpoint this host has on the finding, and no other host\'s', async () => {
    const user = userEvent.setup();
    const shared = finding({
      host_count: 2,
      hosts: [row(31, HOST, 'open', 'a.example.com'), row(34, HOST, 'open', 'b.example.com'), row(32, 6, 'open')],
    });
    api.listFindings.mockResolvedValue({ items: [shared] });
    api.setFindingEndpointStatus.mockResolvedValue(shared);
    renderCard();

    await user.click(await screen.findByLabelText('State of Weak TLS on this host'));
    await user.click(await screen.findByRole('option', { name: 'Remediated here' }));
    await waitFor(() => expect(api.setFindingEndpointStatus).toHaveBeenCalledTimes(2));
    expect(api.setFindingEndpointStatus.mock.calls.map((c) => c[1]).sort()).toEqual([31, 34]);
  });

  it('a finding that is only about this host keeps the issue status control', async () => {
    const user = userEvent.setup();
    const own = finding({ status: 'open' });
    api.listFindings.mockResolvedValue({ items: [own] });
    api.setFindingStatus.mockResolvedValue({ ...own, status: 'confirmed' });
    renderCard();

    await user.click(await screen.findByLabelText('Status for Weak TLS'));
    expect(historyReread).not.toHaveBeenCalled();   // nothing is read again before a change
    await user.click(await screen.findByRole('option', { name: 'Confirmed' }));
    await waitFor(() => expect(api.setFindingStatus).toHaveBeenCalledWith(7, 'confirmed'));
    expect(api.setFindingEndpointStatus).not.toHaveBeenCalled();
    // The status change is on the finding's history: that trail is read again.
    await waitFor(() => expect(historyReread).toHaveBeenCalledTimes(1));
  });

  // The inspector shows, beside each scanner row, the state of the finding
  // that covers it — from the HOST's read.  A change made in this card left
  // those rows saying the old state until the host was reopened.
  describe('a change here re-reads the open host', () => {
    const hostRead = vi.fn();
    const OpenHosts: React.FC = () => {
      useQueries({
        queries: [HOST, 6].map((id) => ({
          queryKey: ['getHost', id], queryFn: () => { hostRead(id); return 1; }, initialData: 0, staleTime: Infinity,
        })),
      });
      return null;
    };
    const renderWithHost = () => render(
      <MemoryRouter><OpenHosts /><HostFindingsCard hostId={HOST} /></MemoryRouter>,
    );
    beforeEach(() => hostRead.mockClear());

    it('an issue status change: this host once, no other host', async () => {
      const user = userEvent.setup();
      const own = finding({ status: 'open' });
      api.listFindings.mockResolvedValue({ items: [own] });
      api.setFindingStatus.mockResolvedValue({ ...own, status: 'confirmed' });
      renderWithHost();

      await user.click(await screen.findByLabelText('Status for Weak TLS'));
      expect(hostRead).not.toHaveBeenCalled();
      await user.click(await screen.findByRole('option', { name: 'Confirmed' }));
      await waitFor(() => expect(hostRead).toHaveBeenCalledWith(HOST));
      expect(hostRead).toHaveBeenCalledTimes(1);
    });

    it('this host’s state on a shared finding', async () => {
      const user = userEvent.setup();
      const shared = finding({ host_count: 2, hosts: [row(31, HOST, 'open'), row(32, 6, 'open')] });
      api.listFindings.mockResolvedValue({ items: [shared] });
      api.setFindingEndpointStatus.mockResolvedValue({ ...shared, hosts: [row(31, HOST, 'false_positive'), row(32, 6, 'open')] });
      renderWithHost();

      await user.click(await screen.findByLabelText('State of Weak TLS on this host'));
      await user.click(await screen.findByRole('option', { name: 'False positive here' }));
      await waitFor(() => expect(hostRead).toHaveBeenCalledWith(HOST));
      expect(hostRead).toHaveBeenCalledTimes(1);
    });

    it('a change that stopped part-way: the rows that did change are on the host', async () => {
      const user = userEvent.setup();
      const shared = finding({
        host_count: 2,
        hosts: [row(31, HOST, 'open', 'a.example.com'), row(34, HOST, 'open', 'b.example.com'), row(32, 6, 'open')],
      });
      api.listFindings.mockResolvedValue({ items: [shared] });
      api.setFindingEndpointStatus.mockResolvedValueOnce(shared).mockRejectedValue(new Error('boom'));
      renderWithHost();

      await user.click(await screen.findByLabelText('State of Weak TLS on this host'));
      await user.click(await screen.findByRole('option', { name: 'Remediated here' }));
      await waitFor(() => expect(toast.error).toHaveBeenCalled());
      await waitFor(() => expect(hostRead).toHaveBeenCalledWith(HOST));
      expect(hostRead).toHaveBeenCalledTimes(1);
    });

    it('a refused status change re-reads nothing', async () => {
      const user = userEvent.setup();
      api.listFindings.mockResolvedValue({ items: [finding({ status: 'open' })] });
      api.setFindingStatus.mockRejectedValue(new Error('refused'));
      renderWithHost();

      await user.click(await screen.findByLabelText('Status for Weak TLS'));
      await user.click(await screen.findByRole('option', { name: 'Confirmed' }));
      await waitFor(() => expect(toast.error).toHaveBeenCalled());
      expect(hostRead).not.toHaveBeenCalled();
    });
  });

  // Branch review 2026-10-01 M13 — the inspector stays mounted across hosts.
  describe('stepping to another host', () => {
    const other = 6;
    const titled = (title: string, hostId: number) => finding({ title, hosts: [row(31, hostId, 'open')] });
    const Card = ({ hostId }: { hostId: number }) => <MemoryRouter><HostFindingsCard hostId={hostId} /></MemoryRouter>;

    it('a slow list for the host just left never replaces the new host’s findings', async () => {
      let releaseFirst!: (v: unknown) => void;
      api.listFindings.mockImplementation(({ host_id }: { host_id: number }) => (host_id === HOST
        ? new Promise((resolve) => { releaseFirst = resolve; })
        : Promise.resolve({ items: [titled('Second host finding', other)] })));
      const { rerender } = render(<Card hostId={HOST} />);
      rerender(<Card hostId={other} />);
      expect(await screen.findByText('Second host finding')).toBeInTheDocument();
      await act(async () => { releaseFirst({ items: [titled('First host finding', HOST)] }); });
      expect(screen.getByText('Second host finding')).toBeInTheDocument();
      expect(screen.queryByText('First host finding')).toBeNull();
    });

    it('does not show the previous host’s findings while the new host’s load', async () => {
      api.listFindings.mockImplementation(({ host_id }: { host_id: number }) => (host_id === HOST
        ? Promise.resolve({ items: [titled('First host finding', HOST)] })
        : new Promise(() => undefined)));
      const { rerender } = render(<Card hostId={HOST} />);
      expect(await screen.findByText('First host finding')).toBeInTheDocument();
      rerender(<Card hostId={other} />);
      expect(screen.queryByText('First host finding')).toBeNull();
    });

    it('a save that returns after the step changes nothing in the new host’s list', async () => {
      const user = userEvent.setup();
      const own = titled('First host finding', HOST);
      let releaseSave!: (v: unknown) => void;
      api.listFindings.mockImplementation(({ host_id }: { host_id: number }) => Promise.resolve({
        // The same finding id is on both hosts' lists (a shared issue).
        items: [host_id === HOST ? { ...own, status: 'open' } : { ...own, title: 'As listed for the second host', hosts: [row(32, other, 'open')], status: 'open' }],
      }));
      api.setFindingStatus.mockReturnValue(new Promise((resolve) => { releaseSave = resolve; }));
      const { rerender } = render(<Card hostId={HOST} />);
      await user.click(await screen.findByLabelText('Status for First host finding'));
      await user.click(await screen.findByRole('option', { name: 'Confirmed' }));
      await waitFor(() => expect(api.setFindingStatus).toHaveBeenCalled());

      rerender(<Card hostId={other} />);
      expect(await screen.findByText('As listed for the second host')).toBeInTheDocument();
      await act(async () => { releaseSave({ ...own, status: 'confirmed' }); });
      // The first host's copy of the finding did not replace this host's row.
      expect(screen.getByText('As listed for the second host')).toBeInTheDocument();
      expect(screen.queryByText('First host finding')).toBeNull();
    });
  });

  // Review 2026-10-01 C2 — a list row carries at most five endpoints.  With
  // `host_id` the server leads the preview with THIS host's rows, so a cut
  // preview still holds this host's state and the card reads no finding whole
  // (it used to: one GET /findings/{id} per shared finding).
  it('reads this host\'s state from a cut preview that leads with it — no read per finding', async () => {
    const user = userEvent.setup();
    const preview = [row(31, HOST, 'remediated'), row(1, 101, 'open'), row(2, 102, 'open'), row(3, 103, 'open'), row(4, 104, 'open')];
    const listed = finding({ host_count: 300, hosts: preview });
    api.listFindings.mockResolvedValue({ items: [listed] });
    api.setFindingEndpointStatus.mockResolvedValue({ ...listed, hosts: [row(31, HOST, 'retest'), ...preview.slice(1)] });
    renderCard();

    const state = await screen.findByLabelText('State of Weak TLS on this host');
    expect(state).toHaveTextContent('Remediated here');
    // (The second argument is the query's abort signal.)
    expect(api.listFindings).toHaveBeenCalledWith({ host_id: HOST, limit: 100 }, expect.any(AbortSignal));
    expect(api.getFinding).not.toHaveBeenCalled();

    await user.click(state);
    await user.click(await screen.findByRole('option', { name: 'Retest here' }));
    await waitFor(() => expect(api.setFindingEndpointStatus).toHaveBeenCalledWith(7, 31, 'retest'));
    expect(api.getFinding).not.toHaveBeenCalled();
  });

  // The one case the preview cannot answer: it is cut AND every row in it is
  // this host's (more named endpoints here than the preview holds), so the
  // rows beyond it would be left out of the state and of a change to it.
  it('reads a finding whole only when the cut preview is all this host\'s named endpoints', async () => {
    const user = userEvent.setup();
    const names = (n: number, status: string) =>
      Array.from({ length: n }, (_, i) => row(40 + i, HOST, status, `v${i}.corp`));
    const listed = finding({ host_count: 7, hosts: names(5, 'open') });
    const whole = finding({ host_count: 7, hosts: [...names(6, 'open'), row(90, 6, 'open')] });
    api.listFindings.mockResolvedValue({ items: [listed] });
    api.getFinding.mockResolvedValue(whole);
    api.setFindingEndpointStatus.mockResolvedValue(whole);
    renderCard();

    const state = await screen.findByLabelText('State of Weak TLS on this host');
    await waitFor(() => expect(api.getFinding).toHaveBeenCalledWith(7, expect.any(AbortSignal)));
    await user.click(state);
    await user.click(await screen.findByRole('option', { name: 'Retest here' }));
    // Every one of this host's six rows — the sixth was not in the preview.
    await waitFor(() => expect(api.setFindingEndpointStatus).toHaveBeenCalledTimes(6));
    expect(api.setFindingEndpointStatus).toHaveBeenCalledWith(7, 45, 'retest');
    expect(api.setFindingEndpointStatus).not.toHaveBeenCalledWith(7, 90, 'retest');
  });

  it('does not read findings whose preview is already every endpoint', async () => {
    api.listFindings.mockResolvedValue({ items: [finding({})] });
    renderCard();
    await screen.findByText('Weak TLS');
    expect(api.getFinding).not.toHaveBeenCalled();
  });
});
