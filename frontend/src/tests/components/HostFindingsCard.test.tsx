import { render, screen, waitFor } from '@testing-library/react';
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
}));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ hasPermission: () => true }) }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../components/FindingHistoryButton', () => ({ FindingHistoryButton: () => null }));

import HostFindingsCard from '../../components/HostFindingsCard';

const HOST = 5;
const row = (id: number, hostId: number, status: string, fqdn: string | null = null) => ({
  id, host_id: hostId, ip_address: `10.0.0.${hostId}`, hostname: null, fqdn, host_status: status,
});
const finding = (over: Record<string, unknown>) => ({
  id: 7, title: 'Weak TLS', severity: 'high', status: 'confirmed', source: 'scanner',
  host_count: 1, hosts: [row(31, HOST, 'open')], evidence_annotation_id: null, ...over,
});

const renderCard = () => render(<MemoryRouter><HostFindingsCard hostId={HOST} /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('HostFindingsCard', () => {
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
    await user.click(await screen.findByRole('option', { name: 'Confirmed' }));
    await waitFor(() => expect(api.setFindingStatus).toHaveBeenCalledWith(7, 'confirmed'));
    expect(api.setFindingEndpointStatus).not.toHaveBeenCalled();
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
    expect(api.listFindings).toHaveBeenCalledWith({ host_id: HOST, limit: 100 });
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
    await waitFor(() => expect(api.getFinding).toHaveBeenCalledWith(7));
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
