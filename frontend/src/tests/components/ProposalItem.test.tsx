/**
 * v5.316.0 — one proposal: what it would change, who proposed it, and the
 * decision controls.  Accept-and-edit sends the reviewer's text, not the
 * proposal's; a decided proposal shows its outcome and no controls.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const acceptProposal = vi.fn();
const rejectProposal = vi.fn();
vi.mock('../../services/api', () => ({
  acceptProposal: (...a: unknown[]) => acceptProposal(...a),
  rejectProposal: (...a: unknown[]) => rejectProposal(...a),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import ProposalItem, { describeProposal } from '../../components/proposals/ProposalItem';
import type { Proposal } from '../../services/api';

const base: Proposal = {
  id: 7, kind: 'finding_text', status: 'pending', source: 'agent',
  finding_id: 3, vulnerability_id: null, finding_host_id: null, field: 'impact',
  payload: { value: 'Relay attacks.' }, current_value: null,
  target: { finding_title: 'SMB signing not required', observation_title: null, host_id: null, host_ip: null },
  rationale: 'The section was empty.', evidence_ids: [12], agent_session_id: 88,
  proposed_by: 'Ana', agent_model: 'model-a', agent_client: 'claude-code 2.1', prompt_version: '3.4.0',
  created_at: null, decided_by: null, decided_at: null, decision_note: null, result_finding_id: null, error: null,
};

const renderItem = (pr: Proposal, onDecided = vi.fn()) => render(
  <MemoryRouter><ProposalItem proposal={pr} canDecide onDecided={onDecided} /></MemoryRouter>,
);

beforeEach(() => { acceptProposal.mockReset(); rejectProposal.mockReset(); });

describe('ProposalItem', () => {
  it('names its source and model, and cites its evidence', () => {
    renderItem(base);
    expect(screen.getByRole('link', { name: 'Agent session #88' })).toHaveAttribute('href', '/agent-sessions/88');
    expect(screen.getByText(/model-a/)).toBeInTheDocument();
    expect(screen.getByText(/Cites evidence #12/)).toBeInTheDocument();
    expect(screen.getByText('Relay attacks.')).toBeInTheDocument();
  });

  it('accepts with the reviewer’s edit, not the proposed text', async () => {
    const onDecided = vi.fn();
    acceptProposal.mockResolvedValue({ ...base, status: 'accepted' });
    renderItem(base, onDecided);
    fireEvent.click(screen.getByRole('button', { name: /Accept and edit/ }));
    const box = screen.getByLabelText('Impact') as HTMLTextAreaElement;
    expect(box.value).toBe('Relay attacks.');
    fireEvent.change(box, { target: { value: 'NTLM relay to any host.' } });
    fireEvent.click(screen.getByRole('button', { name: /Accept with my edit/ }));
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(acceptProposal).toHaveBeenCalledWith(7, { editedValue: 'NTLM relay to any host.' });
  });

  it('shows why an accept was refused and stays decidable', async () => {
    acceptProposal.mockRejectedValue(new Error('Only the finding’s author or a project admin can write its report text.'));
    renderItem(base);
    fireEvent.click(screen.getByRole('button', { name: /^Accept$/ }));
    expect(await screen.findByText(/author or a project admin|Could not accept/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Accept$/ })).toBeEnabled();
  });

  it('a decided proposal shows its outcome and no controls', () => {
    renderItem({ ...base, status: 'rejected', decided_by: 'Ben', decision_note: 'too thin' });
    expect(screen.getByText('rejected')).toBeInTheDocument();
    expect(screen.getByText(/Decided by Ben — too thin/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Accept/ })).not.toBeInTheDocument();
  });

  it('describes every kind in one line', () => {
    expect(describeProposal(base)).toBe('Impact for “SMB signing not required”');
    expect(describeProposal({ ...base, kind: 'observation_dismiss', field: null,
      target: { ...base.target, observation_title: 'SSL self-signed' } }))
      .toBe('Dismiss “SSL self-signed” as a false positive');
    expect(describeProposal({ ...base, kind: 'endpoint_status', field: null, payload: { host_status: 'remediated' },
      target: { ...base.target, host_ip: '10.0.0.5' } }))
      .toBe('Mark 10.0.0.5 remediated on “SMB signing not required”');
  });
});
