import React from 'react';
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
});
