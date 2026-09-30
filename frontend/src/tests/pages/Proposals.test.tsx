/**
 * v5.317.3 — the Proposals page keeps what "Show more" loaded: a decision
 * re-reads as many rows as are shown, where it used to re-read only the
 * first page and drop the reviewer back to it.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const listProposals = vi.fn();
const getProposalSummary = vi.fn();
const rejectProposal = vi.fn();
vi.mock('../../services/api', () => ({
  listProposals: (...a: unknown[]) => listProposals(...a),
  getProposalSummary: (...a: unknown[]) => getProposalSummary(...a),
  rejectProposal: (...a: unknown[]) => rejectProposal(...a),
  acceptProposal: vi.fn(),
  decideProposals: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ hasPermission: () => true }),
}));
vi.mock('../../hooks/useVisibilityPoll', () => ({ useVisibilityPoll: () => undefined }));

import Proposals from '../../pages/Proposals';
import type { Proposal } from '../../services/api';

const row = (id: number): Proposal => ({
  id, kind: 'endpoint_status', status: 'pending', source: 'agent',
  finding_id: 3, vulnerability_id: null, finding_host_id: 9, field: null,
  payload: { host_status: 'retest' }, current_value: null,
  target: { finding_title: `Finding ${id}`, observation_title: null, host_id: 1, host_ip: '10.0.0.1' },
  rationale: null, evidence_ids: [], agent_session_id: 5, proposed_by: 'Ana', agent_model: null,
  agent_client: null, prompt_version: null, created_at: null, decided_by: null, decided_at: null,
  decision_note: null, result_finding_id: null, error: null,
});
const page = (from: number, n: number) => Array.from({ length: n }, (_, i) => row(from + i));

beforeEach(() => {
  listProposals.mockReset();
  getProposalSummary.mockReset().mockResolvedValue({ pending: 120, by_kind: { endpoint_status: 120 } });
  rejectProposal.mockReset();
});

describe('Proposals page — whose findings (5.318.0)', () => {
  const summary = (admin: boolean) => ({
    pending: 120, by_kind: { endpoint_status: 120 }, pending_mine: 3, by_kind_mine: { endpoint_status: 3 },
    viewer_is_project_admin: admin,
  });

  it('shows an analyst the proposals on their own findings by default', async () => {
    getProposalSummary.mockResolvedValue(summary(false));
    listProposals.mockResolvedValue({ total: 3, items: page(1, 3), has_more: false });
    render(<MemoryRouter><Proposals /></MemoryRouter>);
    await waitFor(() => expect(listProposals).toHaveBeenCalled());
    expect(listProposals).toHaveBeenCalledWith(expect.objectContaining({ mine: true }));
    expect(await screen.findByText(/to\s+your findings/)).toBeInTheDocument();
  });

  it('shows a project admin everyone’s, and the notification’s scope=mine wins', async () => {
    getProposalSummary.mockResolvedValue(summary(true));
    listProposals.mockResolvedValue({ total: 120, items: page(1, 50), has_more: true });
    const { unmount } = render(<MemoryRouter><Proposals /></MemoryRouter>);
    await waitFor(() => expect(listProposals).toHaveBeenCalled());
    expect(listProposals).toHaveBeenLastCalledWith(expect.objectContaining({ mine: undefined }));
    unmount();

    listProposals.mockClear();
    render(<MemoryRouter initialEntries={['/proposals?agent_session_id=7&scope=mine']}><Proposals /></MemoryRouter>);
    await waitFor(() => expect(listProposals).toHaveBeenCalled());
    expect(listProposals).toHaveBeenLastCalledWith(expect.objectContaining({ mine: true, agent_session_id: 7 }));
  });
});

describe('Proposals page', () => {
  it('re-reads every loaded row after a decision, not just the first page', async () => {
    listProposals.mockImplementation(async ({ offset, limit }: { offset: number; limit: number }) => ({
      total: 120, items: page(offset + 1, Math.min(limit, 120 - offset)), has_more: true,
    }));
    render(<MemoryRouter><Proposals /></MemoryRouter>);
    await waitFor(() => expect(screen.getAllByRole('button', { name: /Reject…/ })).toHaveLength(50));

    fireEvent.click(screen.getByRole('button', { name: /Show more/ }));
    await waitFor(() => expect(screen.getAllByRole('button', { name: /Reject…/ })).toHaveLength(100));
    expect(listProposals).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, limit: 50 }));

    rejectProposal.mockResolvedValue({ ...row(1), status: 'rejected' });
    fireEvent.click(screen.getAllByRole('button', { name: /Reject…/ })[0]);
    fireEvent.click(screen.getByRole('button', { name: /^Reject$/ }));
    await waitFor(() => expect(rejectProposal).toHaveBeenCalled());
    await waitFor(() => expect(listProposals).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 0, limit: 100 }),
    ));
  });
});
