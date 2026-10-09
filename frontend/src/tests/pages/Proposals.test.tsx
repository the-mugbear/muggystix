/**
 * v5.317.3 — the Proposals page keeps what "Show more" loaded: a decision
 * re-reads as many rows as are shown, where it used to re-read only the
 * first page and drop the reviewer back to it.
 */
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const listProposals = vi.fn();
const getProposalSummary = vi.fn();
const rejectProposal = vi.fn();
const acceptProposal = vi.fn();
const decideProposals = vi.fn();
vi.mock('../../services/api', () => ({
  listProposals: (...a: unknown[]) => listProposals(...a),
  getProposalSummary: (...a: unknown[]) => getProposalSummary(...a),
  rejectProposal: (...a: unknown[]) => rejectProposal(...a),
  acceptProposal: (...a: unknown[]) => acceptProposal(...a),
  decideProposals: (...a: unknown[]) => decideProposals(...a),
  // The real value (services/api/proposals.ts — the route's `max_length`);
  // `proposalBulkLimit.test.ts` pins the two to each other.
  PROPOSAL_BULK_MAX: 200,
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: () => true }),
}));
// The PROJECT role decides who may accept or reject (review 2026-10-01 R32).
const projectRole = vi.hoisted(() => ({ value: 'analyst' as string }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: projectRole.value } }),
}));
const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
// The poll's re-read, run by hand: a polled query (`pollEvery`) reads again
// when the tab becomes visible, which is what this event says.
const pollTick = () => { window.dispatchEvent(new Event('visibilitychange')); };

import { LIST_CURSOR_CLASS } from '../../hooks/useListCursor';
import Proposals from '../../pages/Proposals';
import type { Proposal } from '../../services/api';

// jsdom has no scrollIntoView; the list cursor calls it on its row.
const scrollIntoView = vi.fn();
Element.prototype.scrollIntoView = scrollIntoView;

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
  projectRole.value = 'analyst';
  navigate.mockReset();
  listProposals.mockReset();
  getProposalSummary.mockReset().mockResolvedValue({ pending: 120, by_kind: { endpoint_status: 120 } });
  rejectProposal.mockReset();
  acceptProposal.mockReset();
  decideProposals.mockReset();
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
    expect(listProposals).toHaveBeenCalledWith(expect.objectContaining({ mine: true }), expect.any(AbortSignal));
    expect(await screen.findByText(/to\s+your findings/)).toBeInTheDocument();
  });

  it('shows a project admin everyone’s, and the notification’s scope=mine wins', async () => {
    getProposalSummary.mockResolvedValue(summary(true));
    listProposals.mockResolvedValue({ total: 120, items: page(1, 50), has_more: true });
    const { unmount } = render(<MemoryRouter><Proposals /></MemoryRouter>);
    await waitFor(() => expect(listProposals).toHaveBeenCalled());
    expect(listProposals).toHaveBeenLastCalledWith(expect.objectContaining({ mine: undefined }), expect.any(AbortSignal));
    unmount();

    listProposals.mockClear();
    render(<MemoryRouter initialEntries={['/proposals?agent_session_id=7&scope=mine']}><Proposals /></MemoryRouter>);
    await waitFor(() => expect(listProposals).toHaveBeenCalled());
    expect(listProposals).toHaveBeenLastCalledWith(expect.objectContaining({ mine: true, agent_session_id: 7 }), expect.any(AbortSignal));
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
    expect(listProposals).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, limit: 50 }), expect.any(AbortSignal));

    rejectProposal.mockResolvedValue({ ...row(1), status: 'rejected' });
    listProposals.mockClear();
    getProposalSummary.mockClear();
    fireEvent.click(screen.getAllByRole('button', { name: /Reject…/ })[0]);
    fireEvent.click(screen.getByRole('button', { name: /^Reject$/ }));
    await waitFor(() => expect(rejectProposal).toHaveBeenCalled());
    // Both loaded pages are read again, a page at a time (it was one request
    // of `limit: 100` — the old hook's `maxReload`), and all 100 rows stay.
    await waitFor(() => expect(listProposals).toHaveBeenCalledWith(expect.objectContaining({ offset: 50, limit: 50 }), expect.any(AbortSignal)));
    expect(listProposals).toHaveBeenCalledWith(expect.objectContaining({ offset: 0, limit: 50 }), expect.any(AbortSignal));
    await waitFor(() => expect(document.querySelectorAll('[data-proposal]')).toHaveLength(100));
    // 5.351.0 — ONE re-read: the decision says which reads are out of date
    // itself.  (The row's callback re-read the list and the count, and the
    // decision invalidated the count again: the count was asked for twice.)
    expect(listProposals).toHaveBeenCalledTimes(2);
    expect(getProposalSummary).toHaveBeenCalledTimes(1);
  });
});

// Review 2026-10-01 R33 — reproduced live: with the first request delayed, the
// page showed a pending proposal, Accept and Reject included, under the
// "Accepted" filter, and "Accept all shown" acted on it.
describe('Proposals page — the latest filter wins', () => {
  const answer = (status: Proposal['status'], n: number) => ({
    total: n, has_more: false, items: page(status === 'pending' ? 1 : 500, n).map((r) => ({ ...r, status })),
  });

  it('a slow "pending" response arriving after the filter changed to "accepted" does not replace the accepted rows', async () => {
    let releasePending!: (v: unknown) => void;
    const slowPending = new Promise((resolve) => { releasePending = resolve; });
    listProposals.mockImplementation(({ status }: { status: string }) =>
      (status === 'pending' ? slowPending : Promise.resolve(answer('accepted', 2))));
    getProposalSummary.mockResolvedValue({
      pending: 3, by_kind: {}, pending_mine: 3, by_kind_mine: {}, viewer_is_project_admin: true,
    });

    const Shell = () => {
      const [, setParams] = useSearchParams();
      return (
        <>
          <button type="button" onClick={() => setParams({ status: 'accepted', scope: 'all' })}>to accepted</button>
          <Proposals />
        </>
      );
    };
    render(<MemoryRouter initialEntries={['/proposals?scope=all']}><Shell /></MemoryRouter>);
    await waitFor(() => expect(listProposals).toHaveBeenCalledWith(expect.objectContaining({ status: 'pending' }), expect.any(AbortSignal)));

    fireEvent.click(screen.getByRole('button', { name: 'to accepted' }));
    await waitFor(() => expect(document.querySelectorAll('[data-proposal]')).toHaveLength(2));

    // The first request answers now, with three pending rows.
    await act(async () => { releasePending(answer('pending', 3)); await Promise.resolve(); });
    await new Promise((r) => setTimeout(r, 20));

    expect(document.querySelectorAll('[data-proposal]')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /^Accept$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Accept all shown/ })).toBeNull();
    expect(decideProposals).not.toHaveBeenCalled();
  });

  it('says a failed load is a failure, not "None."', async () => {
    getProposalSummary.mockResolvedValue({ pending: 0, by_kind: {}, viewer_is_project_admin: true });
    listProposals.mockRejectedValue(new Error('down'));
    render(<MemoryRouter initialEntries={['/proposals?scope=all']}><Proposals /></MemoryRouter>);
    expect(await screen.findByText('Could not load the proposals.')).toBeInTheDocument();
    expect(screen.queryByText('None.')).toBeNull();
  });
});

describe('Proposals page — a filter value it does not know', () => {
  const Address = () => {
    const [params] = useSearchParams();
    return <output data-testid="address">{params.toString()}</output>;
  };
  const open = (search: string) =>
    render(<MemoryRouter initialEntries={[`/proposals${search}`]}><Proposals /><Address /></MemoryRouter>);

  beforeEach(() => {
    getProposalSummary.mockResolvedValue({ pending: 2, by_kind: {}, viewer_is_project_admin: true });
    listProposals.mockResolvedValue({ total: 2, items: page(1, 2), has_more: false });
  });

  it('`?status=all` is the pending list, never a request for a status the API refuses', async () => {
    open('?status=all&scope=all');
    await waitFor(() => expect(listProposals).toHaveBeenCalled());
    expect(listProposals.mock.calls.every(([q]) => q.status === 'pending')).toBe(true);
    expect(screen.queryByText('Could not load the proposals.')).toBeNull();
    expect(screen.getByRole('heading', { name: /Waiting for a decision/ })).toBeInTheDocument();
    // The address is corrected, and the filter that was valid stays.
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent(/^scope=all$/));
  });

  it('an unknown kind is every kind, and a valid status beside it is kept', async () => {
    open('?status=accepted&kind=nonsense&scope=all');
    await waitFor(() => expect(listProposals).toHaveBeenCalled());
    expect(listProposals.mock.calls.every(([q]) => q.status === 'accepted' && q.kind === undefined)).toBe(true);
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent(/^status=accepted&scope=all$/));
  });

  it('leaves a valid address alone', async () => {
    open('?status=rejected&kind=finding_text&scope=all');
    await waitFor(() => expect(listProposals).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'rejected', kind: 'finding_text' }), expect.any(AbortSignal),
    ));
    expect(screen.getByTestId('address')).toHaveTextContent('status=rejected&kind=finding_text&scope=all');
  });
});

// Browser pass 2026-10-01 — the bulk reason was a bare box in the dialog.
describe('Proposals page — the bulk reject reason', () => {
  it('is named, says what it is for, and is sent with the decision', async () => {
    listProposals.mockResolvedValue({ total: 2, items: page(1, 2), has_more: false });
    decideProposals.mockResolvedValue({ decided: [1, 2], failed: [] });
    render(<MemoryRouter initialEntries={['/proposals?scope=all']}><Proposals /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: /Reject all shown/ }));
    const reason = await screen.findByRole('textbox', { name: 'Why reject them? (optional)' });
    expect(reason).toHaveAttribute('placeholder', expect.stringMatching(/^Note for the agent \(optional\), e\.g\. /));
    expect(reason).toHaveAccessibleDescription(/The agents that proposed them read the decision/);
    fireEvent.change(reason, { target: { value: 'No evidence cited.' } });
    fireEvent.click(within(reason.closest('[role="dialog"]') as HTMLElement).getByRole('button', { name: 'Reject all shown' }));
    await waitFor(() => expect(decideProposals).toHaveBeenCalledWith([1, 2], 'reject', 'No evidence cited.'));
  });
});

// Review 2026-10-02 H4 — the route takes at most 200 ids and refuses a longer
// list whole; the page sent every loaded pending proposal.
describe('Proposals page — a bulk decision is at most 200, and says so', () => {
  const ids = (n: number) => Array.from({ length: n }, (_, i) => i + 1);
  const open = async (n: number) => {
    listProposals.mockResolvedValue({ total: n, items: page(1, n), has_more: false });
    decideProposals.mockImplementation(async (sent: number[]) => ({ decided: sent, failed: [] }));
    render(<MemoryRouter initialEntries={['/proposals?scope=all']}><Proposals /></MemoryRouter>);
    await screen.findByText(/j<\/kbd>|move,/);
  };

  it.each([
    ['accept', 'Accept'] as const,
    ['reject', 'Reject'] as const,
  ])('%s: with 250 shown, sends the first 200 in list order in ONE request', async (action, verb) => {
    await open(250);
    expect(screen.queryByRole('button', { name: `${verb} all shown` })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: `${verb} the first 200 of 250 shown` }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(`${verb} the first 200 of 250 proposals shown?`)).toBeInTheDocument();
    expect(dialog).toHaveTextContent('these are the first 200 of the 250 shown');
    expect(dialog).toHaveTextContent('The other 50 stay pending');
    const before = listProposals.mock.calls.length;
    fireEvent.click(within(dialog).getByRole('button', { name: `${verb} the first 200` }));
    await waitFor(() => expect(decideProposals).toHaveBeenCalledTimes(1));
    expect(decideProposals.mock.calls[0][0]).toEqual(ids(200));
    expect(decideProposals.mock.calls[0][1]).toBe(action);
    // The list is read again, so the rest can follow.
    await waitFor(() => expect(listProposals.mock.calls.length).toBeGreaterThan(before));
    expect(decideProposals).toHaveBeenCalledTimes(1);
  });

  it('with exactly 200 shown, nothing changes: "all shown", every id', async () => {
    await open(200);
    fireEvent.click(screen.getByRole('button', { name: 'Accept all shown' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Accept 200 proposals?')).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/the first/);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Accept all shown' }));
    await waitFor(() => expect(decideProposals).toHaveBeenCalledTimes(1));
    expect(decideProposals.mock.calls[0][0]).toEqual(ids(200));
  });
});

describe('Proposals page — who may decide (R32)', () => {
  it('shows a project viewer the proposals without Accept, Reject or the bulk actions', async () => {
    projectRole.value = 'viewer';
    listProposals.mockResolvedValue({ total: 3, items: page(1, 3), has_more: false });
    render(<MemoryRouter initialEntries={['/proposals?scope=all']}><Proposals /></MemoryRouter>);
    await waitFor(() => expect(document.querySelectorAll('[data-proposal]')).toHaveLength(3));
    expect(screen.queryByRole('button', { name: /^Accept$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Reject/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /all shown/ })).toBeNull();
    // …and the keys do nothing for them.
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'a' });
    expect(acceptProposal).not.toHaveBeenCalled();
  });

  it('shows an analyst the controls', async () => {
    listProposals.mockResolvedValue({ total: 3, items: page(1, 3), has_more: false });
    render(<MemoryRouter initialEntries={['/proposals?scope=all']}><Proposals /></MemoryRouter>);
    await waitFor(() => expect(screen.getAllByRole('button', { name: /^Accept$/ })).toHaveLength(3));
    expect(screen.getByRole('button', { name: /Accept all shown/ })).toBeInTheDocument();
  });
});

// B16 — the review queue is worked from the keyboard.
describe('Proposals page — keyboard review', () => {
  const load = async () => {
    listProposals.mockResolvedValue({ total: 3, items: page(1, 3), has_more: false });
    render(<MemoryRouter initialEntries={['/proposals?scope=all']}><Proposals /></MemoryRouter>);
    await waitFor(() => expect(document.querySelectorAll('[data-proposal]')).toHaveLength(3));
  };
  const cursorRow = () => document.querySelector('[data-list-cursor="true"]')?.getAttribute('data-proposal');

  it('j / k move a visible cursor and Enter opens the endpoint on its finding', async () => {
    await load();
    expect(cursorRow()).toBeUndefined();
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'j' });
    expect(cursorRow()).toBe('2');
    // Walkthrough 2026-10-01 — the cursor must be SEEN: exactly one row carries
    // the shared highlight (a fill and a ring), and it is scrolled into view.
    const marked = document.querySelectorAll('[data-list-cursor="true"]');
    expect(marked).toHaveLength(1);
    for (const cls of LIST_CURSOR_CLASS.split(' ')) expect(marked[0].className).toContain(cls);
    expect(document.querySelector('[data-proposal="1"]')!.className).not.toContain('ring-');
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    fireEvent.keyDown(window, { key: 'k' });
    expect(cursorRow()).toBe('1');
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(navigate).toHaveBeenCalledWith('/findings/3?endpoint=9#endpoints');
  });

  it('`a` accepts the cursor row and `r` opens its reject reason — never without a cursor', async () => {
    await load();
    acceptProposal.mockResolvedValue({ ...row(2), status: 'accepted' });
    fireEvent.keyDown(window, { key: 'a' });
    expect(acceptProposal).not.toHaveBeenCalled();

    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'a' });
    await waitFor(() => expect(acceptProposal).toHaveBeenCalledWith(2, {}));

    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'r' });
    const reason = await screen.findByLabelText(/Why reject it/);
    expect(reason.id).toBe('reject-3');
    expect(rejectProposal).not.toHaveBeenCalled();  // the reason is asked first
  });

  it('never fires while typing in a field', async () => {
    await load();
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'r' });
    const reason = await screen.findByLabelText(/Why reject it/);
    fireEvent.keyDown(reason, { key: 'a' });
    fireEvent.keyDown(reason, { key: 'j' });
    expect(acceptProposal).not.toHaveBeenCalled();
    expect(cursorRow()).toBe('1');
  });

  // Review 2026-10-01 S1 — Radix typeahead does not stop the key: with the
  // Status select focused (or its list, or a menu, open) `a` moved the
  // widget's highlight AND accepted the proposal under the cursor.
  it.each([
    ['a Select trigger', () => screen.getByRole('combobox', { name: /Status/ })],
    ['an open list', () => {
      const list = document.createElement('div');
      list.setAttribute('role', 'listbox');
      const option = document.createElement('div');
      list.appendChild(option);
      document.body.appendChild(list);
      return option;
    }],
    ['a menu', () => {
      const menu = document.createElement('div');
      menu.setAttribute('role', 'menu');
      document.body.appendChild(menu);
      return menu;
    }],
    ['popper content', () => {
      const popper = document.createElement('div');
      popper.setAttribute('data-radix-popper-content-wrapper', '');
      document.body.appendChild(popper);
      return popper;
    }],
  ])('`a`, `r` and `j` do nothing while %s has the key', async (_name, target) => {
    await load();
    fireEvent.keyDown(window, { key: 'j' });
    const el = target();
    fireEvent.keyDown(el, { key: 'a' });
    fireEvent.keyDown(el, { key: 'r' });
    fireEvent.keyDown(el, { key: 'j' });
    expect(acceptProposal).not.toHaveBeenCalled();
    expect(screen.queryByLabelText(/Why reject it/)).toBeNull();
    // (On the real trigger the Select's own typeahead answers the letter by
    // changing the filter, which clears the cursor — that is the widget's.)
    if (el.getAttribute('role') !== 'combobox') expect(cursorRow()).toBe('1');
    if (el.parentElement === document.body) el.remove(); else el.closest('[role="listbox"]')?.remove();
  });

  it('a held key (auto-repeat) accepts nothing', async () => {
    await load();
    acceptProposal.mockResolvedValue({ ...row(1), status: 'accepted' });
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'a', repeat: true });
    fireEvent.keyDown(window, { key: 'r', repeat: true });
    expect(acceptProposal).not.toHaveBeenCalled();
    expect(screen.queryByLabelText(/Why reject it/)).toBeNull();
    // The press itself still counts.
    fireEvent.keyDown(window, { key: 'a' });
    await waitFor(() => expect(acceptProposal).toHaveBeenCalledTimes(1));
  });

  it('a second `a` while the first decision is in flight does nothing', async () => {
    await load();
    let settle: (p: Proposal) => void = () => undefined;
    acceptProposal.mockReturnValue(new Promise<Proposal>((resolve) => { settle = resolve; }));
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'a' });
    await waitFor(() => expect(acceptProposal).toHaveBeenCalledTimes(1));
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'a' });
    expect(acceptProposal).toHaveBeenCalledTimes(1);
    await act(async () => { settle({ ...row(1), status: 'accepted' }); });
  });

  // S2 — the list is newest first and re-reads itself: the cursor stays on
  // its PROPOSAL when one arrives above it.
  it('keeps the cursor on its proposal when a re-read puts a new one above it', async () => {
    await load();
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'j' });
    expect(cursorRow()).toBe('2');
    listProposals.mockResolvedValue({ total: 4, items: [row(99), ...page(1, 3)], has_more: false });
    await act(async () => { pollTick(); });
    await waitFor(() => expect(document.querySelectorAll('[data-proposal]')).toHaveLength(4));
    expect(cursorRow()).toBe('2');
    acceptProposal.mockResolvedValue({ ...row(2), status: 'accepted' });
    fireEvent.keyDown(window, { key: 'a' });
    await waitFor(() => expect(acceptProposal).toHaveBeenCalledWith(2, {}));
  });

  it('moves to the row that took its place when the cursor’s proposal is gone', async () => {
    await load();
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'j' });
    listProposals.mockResolvedValue({ total: 2, items: [row(1), row(3)], has_more: false });
    await act(async () => { pollTick(); });
    await waitFor(() => expect(document.querySelectorAll('[data-proposal]')).toHaveLength(2));
    expect(cursorRow()).toBe('3');
  });
});

// M9 — `?agent_session_id=abc` was sent as NaN and shown as "session #NaN".
describe('Proposals page — a session id that is not a number', () => {
  const Address = () => {
    const [params] = useSearchParams();
    return <output data-testid="address">{params.toString()}</output>;
  };

  it('is ignored and taken out of the address', async () => {
    listProposals.mockResolvedValue({ total: 2, items: page(1, 2), has_more: false });
    render(
      <MemoryRouter initialEntries={['/proposals?agent_session_id=abc&scope=all']}><Proposals /><Address /></MemoryRouter>,
    );
    await waitFor(() => expect(listProposals).toHaveBeenCalled());
    expect(listProposals.mock.calls.every(([q]) => q.agent_session_id === undefined)).toBe(true);
    expect(screen.queryByText(/NaN/)).toBeNull();
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent(/^scope=all$/));
  });

  it('keeps a real one', async () => {
    listProposals.mockResolvedValue({ total: 2, items: page(1, 2), has_more: false });
    render(
      <MemoryRouter initialEntries={['/proposals?agent_session_id=5&scope=all']}><Proposals /><Address /></MemoryRouter>,
    );
    await waitFor(() => expect(listProposals).toHaveBeenCalledWith(expect.objectContaining({ agent_session_id: 5 }), expect.any(AbortSignal)));
    expect(screen.getByText(/From agent session #5/)).toBeInTheDocument();
    expect(screen.getByTestId('address')).toHaveTextContent('agent_session_id=5&scope=all');
  });
});
