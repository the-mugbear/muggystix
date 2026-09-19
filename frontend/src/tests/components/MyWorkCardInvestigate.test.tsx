import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import MyWorkCard from '../../components/MyWorkCard';
import type { InvestigationQueueResponse } from '../../services/api';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
const api = vi.hoisted(() => ({ updateTestPlanEntry: vi.fn(), followHost: vi.fn() }));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1 } }) }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const queue: InvestigationQueueResponse = {
  untouched_total: 40,
  queue_total: 2,
  tiers: ['Exploitable critical', 'Critical vulnerability', 'Exploit available', 'High-value service, new or changed', 'Scans disagree'],
  items: [
    {
      host_id: 7,
      ip_address: '10.0.0.7',
      hostname: 'dc01.corp.local',
      tier: 1,
      tier_label: 'Exploitable critical',
      reasons: [
        { kind: 'critical_exploitable', text: '1 critical vulnerability with a known public exploit' },
        { kind: 'high_value', text: 'SMB, RDP open' },
      ],
      evidence: { sources: ['nmap', 'nessus'], last_seen: new Date().toISOString(), confirmation: 'scanner' },
      next_action: { kind: 'review', text: 'Take it into review: exploitable critical on a host nobody has looked at.' },
    },
    {
      host_id: 8,
      ip_address: '10.0.0.8',
      hostname: null,
      tier: 4,
      tier_label: 'High-value service, new or changed',
      reasons: [{ kind: 'new_host', text: 'First seen 2 days ago' }],
      evidence: { sources: ['nmap'], last_seen: null, confirmation: 'scanner' },
      next_action: { kind: 'collect', text: 'No vulnerability data on this host — run a vulnerability scan against it.' },
    },
  ],
};

const onRetry = vi.fn();
const renderCard = (investigate: InvestigationQueueResponse | null) =>
  render(
    <MemoryRouter>
      <MyWorkCard
        queue={null}
        tasks={null}
        notes={null}
        findings={null}
        investigate={investigate}
        loading={false}
        error={null}
        onRetry={onRetry}
      />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.followHost.mockResolvedValue({ status: 'in_review' });
});

describe('MyWorkCard — Worth a look', () => {
  it('lists untouched hosts with their reasons, evidence and next step, ordered by stated tier', () => {
    renderCard(queue);
    expect(screen.getByText('Worth a look')).toBeInTheDocument();
    expect(screen.getByText('1 critical vulnerability with a known public exploit')).toBeInTheDocument();
    expect(screen.getByText('SMB, RDP open')).toBeInTheDocument();
    expect(screen.getByText('nmap, nessus')).toBeInTheDocument();
    expect(screen.getAllByText(/scanner-reported, unconfirmed/)).toHaveLength(2);
    expect(screen.getByText(/ordered by tier: Exploitable critical › Critical vulnerability/)).toBeInTheDocument();
    // No composite score anywhere: the row says its tier in words.
    expect(screen.getByText('Exploitable critical')).toBeInTheDocument();
  });

  it('"Review" takes the host into review under the caller and refreshes the card', async () => {
    renderCard(queue);
    fireEvent.click(screen.getAllByRole('button', { name: 'Review' })[0]);
    await waitFor(() => expect(api.followHost).toHaveBeenCalledWith(7, 'in_review'));
    await waitFor(() => expect(onRetry).toHaveBeenCalled());
  });

  it('a collect action offers the upload page', () => {
    renderCard(queue);
    fireEvent.click(screen.getByRole('button', { name: 'Upload evidence' }));
    expect(navigate).toHaveBeenCalledWith('/scans');
  });

  it('says so when nothing untouched has a reason', () => {
    renderCard({ ...queue, items: [], queue_total: 0, untouched_total: 12 });
    expect(screen.getByText(/12 untouched hosts, none with a weakness or change on record/)).toBeInTheDocument();
  });

  it('renders nothing for the section on an older backend without the block', () => {
    renderCard(null);
    expect(screen.queryByText('Worth a look')).not.toBeInTheDocument();
  });
});
