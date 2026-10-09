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

import { readsOnScreen } from '../helpers/readsOnScreen';
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
  // Browser pass 2026-10-01 — the reject reason opened as a bare box: it is
  // named by its label, says what the note is for, and reads its hint out.
  it('names the reject reason, says what it is for, and sends it', async () => {
    rejectProposal.mockResolvedValue({ ...base, status: 'rejected' });
    renderItem(base);
    fireEvent.click(screen.getByRole('button', { name: /Reject…/ }));
    const reason = screen.getByRole('textbox', { name: 'Why reject it? (optional)' });
    expect(reason).toHaveFocus();
    expect(reason).toHaveAttribute('placeholder', expect.stringMatching(/^Note for the agent \(optional\), e\.g\. /));
    expect(reason).toHaveAccessibleDescription(/The agent that proposed it reads this/);
    fireEvent.change(reason, { target: { value: 'Cite the evidence.' } });
    fireEvent.click(screen.getByRole('button', { name: /^Reject$/ }));
    await waitFor(() => expect(rejectProposal).toHaveBeenCalledWith(7, 'Cite the evidence.'));
  });

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

describe('v5.316.1 — walkthrough fixes', () => {
  // 5.351.0 — a decision announced itself with a window event
  // (`nm:proposals-changed`) that the finding page listened for, beside a
  // callback that re-read and an invalidation of the count: up to three reads
  // of one list.  It now says which reads are out of date, once; whatever
  // shows them — the top bar's count, the Proposals page, the finding's own
  // pending list, the finding, its history and its images — reads again.
  const everyRead = {
    getProposalSummary: 'the top bar’s count', listProposals: 'the lists of proposals',
    getFinding: 'the finding', getFindingHistory: 'its history', getFindingImages: 'its images',
  };

  it('a decision tells the top bar at once, and every other read it changed — once each', async () => {
    const { reread, ReadsOnScreen } = readsOnScreen(everyRead);
    rejectProposal.mockResolvedValue({ ...base, status: 'rejected' });
    render(<MemoryRouter><ReadsOnScreen /><ProposalItem proposal={base} canDecide /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /^Reject…$/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Reject$/ }));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('the top bar’s count'));
    await waitFor(() => expect(reread).toHaveBeenCalledTimes(5));
    expect(reread.mock.calls.map(([what]) => what).sort()).toEqual(Object.values(everyRead).sort());
  });

  it('a page is told when the reads a decision changed have been read again', async () => {
    const { reread, ReadsOnScreen } = readsOnScreen(everyRead);
    acceptProposal.mockResolvedValue({ ...base, status: 'accepted' });
    let settledWith: number | null = null;
    const onDecided = vi.fn((_updated: Proposal, settled: Promise<void>) => {
      void settled.then(() => { settledWith = reread.mock.calls.length; });
    });
    render(<MemoryRouter><ReadsOnScreen /><ProposalItem proposal={base} canDecide onDecided={onDecided} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /^Accept$/ }));
    await waitFor(() => expect(settledWith).toBe(5));
    expect(onDecided).toHaveBeenCalledTimes(1);
    expect(onDecided.mock.calls[0][0]).toMatchObject({ id: 7, status: 'accepted' });
  });

  it('a refused decision reads nothing again', async () => {
    const { reread, ReadsOnScreen } = readsOnScreen(everyRead);
    acceptProposal.mockRejectedValue(new Error('no'));
    render(<MemoryRouter><ReadsOnScreen /><ProposalItem proposal={base} canDecide /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: /^Accept$/ }));
    expect(await screen.findByText(/no|Could not accept/)).toBeInTheDocument();
    expect(reread).not.toHaveBeenCalled();
  });

  it('on the finding’s own page the target is text, not a link back to it', () => {
    const pr = { ...base, kind: 'endpoint_status' as const, field: null, payload: { host_status: 'retest' } };
    render(<MemoryRouter><ProposalItem proposal={pr} canDecide onDecided={vi.fn()} showTarget linkTarget={false} /></MemoryRouter>);
    expect(screen.queryByRole('link', { name: /Mark/ })).not.toBeInTheDocument();
    expect(screen.getByText(/^Mark .* retest on/)).toBeInTheDocument();
  });
});

describe('5.317.1 — a rejection can say why (the agent reads it back)', () => {
  it('sends the reason as the decision note', async () => {
    const onDecided = vi.fn();
    rejectProposal.mockResolvedValue({ ...base, status: 'rejected' });
    renderItem(base, onDecided);
    fireEvent.click(screen.getByRole('button', { name: /^Reject…$/ }));
    fireEvent.change(screen.getByLabelText(/Why reject it/), {
      target: { value: '  Remove the relay claim until the raw artifact is verified.  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Reject$/ }));
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(rejectProposal).toHaveBeenCalledWith(7, 'Remove the relay claim until the raw artifact is verified.');
  });

  it('a reason is optional, and Cancel leaves it pending', async () => {
    rejectProposal.mockResolvedValue({ ...base, status: 'rejected' });
    renderItem(base);
    fireEvent.click(screen.getByRole('button', { name: /^Reject…$/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/ }));
    expect(rejectProposal).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /^Accept$/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Reject…$/ }));
    fireEvent.click(screen.getByRole('button', { name: /^Reject$/ }));
    await waitFor(() => expect(rejectProposal).toHaveBeenCalledWith(7, undefined));
  });
});

// Review 2026-10-01 B14 — what a decided proposal already carried and did not show.
describe('ProposalItem — after the decision', () => {
  it('says WHEN it was decided, with the absolute time on hover', () => {
    const decidedAt = new Date(Date.now() - 2 * 3600_000).toISOString();
    renderItem({ ...base, status: 'accepted', decided_by: 'Ben', decided_at: decidedAt });
    const line = screen.getByText(/Decided by Ben/);
    const time = line.querySelector('time');
    expect(time).not.toBeNull();
    expect(time?.getAttribute('datetime')).toBe(decidedAt);
    expect(time?.textContent).toMatch(/2h/);
    expect(time?.getAttribute('title')).toBeTruthy();
  });

  it('shows no time when the proposal carries none', () => {
    renderItem({ ...base, status: 'rejected', decided_by: 'Ben', decided_at: null, decision_note: 'not this' });
    const line = screen.getByText(/Decided by Ben/);
    expect(line.querySelector('time')).toBeNull();
    expect(line.textContent).toBe('Decided by Ben — not this');
  });

  it('links an accepted "new finding" proposal to the finding it created', () => {
    renderItem({
      ...base, kind: 'finding_create', status: 'accepted', finding_id: null, field: null,
      payload: { title: 'Default creds', severity: 'high', host_ids: [1] }, result_finding_id: 41,
      decided_by: 'Ben', decided_at: null,
    });
    expect(screen.getByRole('link', { name: 'Open the finding it created' })).toHaveAttribute('href', '/findings/41');
  });

  it('has no such link while pending, or when no finding came of it', () => {
    renderItem({
      ...base, kind: 'finding_create', status: 'pending', finding_id: null, field: null,
      payload: { title: 'Default creds', severity: 'high', host_ids: [1] }, result_finding_id: null,
    });
    expect(screen.queryByRole('link', { name: /finding it created/ })).toBeNull();
  });

  it('deep-links an endpoint-status proposal to its endpoint row on the finding', () => {
    renderItem({
      ...base, kind: 'endpoint_status', field: null, finding_id: 3, finding_host_id: 9,
      payload: { host_status: 'retest' },
      target: { finding_title: 'SMB signing', observation_title: null, host_id: 1, host_ip: '10.0.0.1' },
    });
    expect(screen.getByRole('link', { name: 'Show 10.0.0.1 on the finding' }))
      .toHaveAttribute('href', '/findings/3?endpoint=9#endpoints');
  });

  it('is null-safe: an endpoint proposal without its row id gets no endpoint link', () => {
    renderItem({
      ...base, kind: 'endpoint_status', field: null, finding_id: 3, finding_host_id: null,
      payload: { host_status: 'retest' },
    });
    expect(screen.queryByRole('link', { name: /on the finding$/ })).toBeNull();
  });

  // Walkthrough 2026-10-02 — the current text was a closed toggle, read after the draft.
  it('shows the current text beside the proposed one, without opening anything', () => {
    renderItem({ ...base, current_value: 'Relay to the file share.' });
    expect(screen.queryByText('Current text')).toBeNull();
    expect(screen.getByTestId('compare-current')).toHaveTextContent('Relay to the file share.');
    expect(screen.getByTestId('compare-proposed')).toHaveTextContent('Relay attacks.');
  });

  it('accept-and-edit can start from the current text instead of the draft', async () => {
    acceptProposal.mockResolvedValue({ ...base, status: 'accepted' });
    renderItem({ ...base, current_value: 'Relay to the file share.' });
    fireEvent.click(screen.getByRole('button', { name: /Accept and edit/ }));
    const box = screen.getByLabelText('Impact') as HTMLTextAreaElement;
    expect(box.value).toBe('Relay attacks.');
    fireEvent.click(screen.getByRole('button', { name: 'Start from the current text' }));
    expect(box.value).toBe('Relay to the file share.');
    fireEvent.click(screen.getByRole('button', { name: 'Start from the draft' }));
    expect(box.value).toBe('Relay attacks.');
    // Both texts stay in view while editing (browser pass 5.334.1).
    expect(screen.getByTestId('compare-current')).toHaveTextContent('Relay to the file share.');
    expect(screen.getByTestId('compare-proposed')).toHaveTextContent('Relay attacks.');
  });

  it('warns when the field changed after the draft was written', () => {
    renderItem({ ...base, current_value: 'Newer text.', base_value: 'Old text.', changed_since_proposed: true });
    expect(screen.getByRole('note')).toHaveTextContent(/changed after the draft was written/);
  });
});
