import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import HostBulkBar from '../../components/hosts/HostBulkBar';

vi.mock('../../services/api', () => ({
  bulkTagHosts: vi.fn(),
  bulkAssignHosts: vi.fn(),
  bulkUnassignHosts: vi.fn(),
  bulkFollowHosts: vi.fn(),
  getMatchingHostIds: vi.fn(),
  listHostTags: vi.fn(),
  listProjectMembers: vi.fn(),
}));

// "Propose tests" ends in the Start Agent Session dialog; here it only has to
// show the task it was given.
vi.mock('../../hooks/useMyAssistSessions', () => ({
  useMyAssistSessions: () => ({ sessions: [], loading: false, failed: false, refresh: vi.fn() }),
}));
vi.mock('../../hooks/useCanStartAgentSession', () => ({ useCanStartAgentSession: () => true }));
vi.mock('../../components/StartAssistDialog', () => ({
  default: ({ instruction }: { instruction?: string }) => <div data-testid="agent-task">{instruction}</div>,
}));

import * as api from '../../services/api';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

/** The cap the server states in the list's answer (`bulk_select_cap`). */
const SERVER_CAP = 5000;

const renderBar = (totalMatching: number, bulkCap: number | null = SERVER_CAP) =>
  render(
    <HostBulkBar
      selectedIds={[1, 2]}
      selectedIps={['10.0.0.1', '10.0.0.2']}
      totalMatching={totalMatching}
      bulkCap={bulkCap}
      queryContext={{}}
      onClear={vi.fn()}
      onApplied={vi.fn()}
    />,
  );

// The server resolves at most `bulk_select_cap` ids for "all matching".  Above
// it the bar used to promise "Select all 7,000 matching", count 7,000 selected
// and confirm 7,000 — then act on 5,000 and mention it in a toast afterwards.
// 5.365.0 — the number is the server's, from the list's answer: the bar kept
// its own 5000 beside the server's.
describe('HostBulkBar — selection scope', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listHostTags.mockResolvedValue([]);
    mocked.listProjectMembers.mockResolvedValue([]);
  });

  it('distinguishes the checked rows from every matching host', async () => {
    const user = userEvent.setup();
    renderBar(120);
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    expect(screen.getByText('checked rows only')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Select all 120 matching' }));
    expect(screen.getByText('120 selected')).toBeInTheDocument();
    expect(screen.getByText(/Every host matching the current filters, on every page/)).toBeInTheDocument();
  });

  // "All matching" belongs to the page selection it was chosen over: another
  // number of checked rows is the checked rows again — and stays so when the
  // number comes back.
  it('a changed page selection leaves "all matching", and does not return to it', async () => {
    const user = userEvent.setup();
    const bar = (ids: number[]) => (
      <HostBulkBar
        selectedIds={ids}
        selectedIps={ids.map((id) => `10.0.0.${id}`)}
        totalMatching={120}
        bulkCap={SERVER_CAP}
        queryContext={{}}
        onClear={vi.fn()}
        onApplied={vi.fn()}
      />
    );
    const { rerender } = render(bar([1, 2]));
    await user.click(screen.getByRole('button', { name: 'Select all 120 matching' }));
    expect(screen.getByText('120 selected')).toBeInTheDocument();

    rerender(bar([1, 2, 3]));
    expect(screen.getByText('3 selected')).toBeInTheDocument();
    expect(screen.getByText('checked rows only')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Select all 120 matching' })).toBeInTheDocument();

    rerender(bar([1, 3]));
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    expect(screen.getByText('checked rows only')).toBeInTheDocument();
  });

  it('never calls a capped subset "all": the button, the count and the note name the cap up front', async () => {
    const user = userEvent.setup();
    const total = SERVER_CAP + 2000;
    renderBar(total);
    expect(screen.queryByRole('button', { name: /Select all/ })).not.toBeInTheDocument();

    await user.click(
      screen.getByRole('button', {
        name: `Select the first ${SERVER_CAP.toLocaleString()} of ${total.toLocaleString()} matching`,
      }),
    );
    expect(screen.getByText(`${SERVER_CAP.toLocaleString()} selected`)).toBeInTheDocument();
    expect(screen.getByText(/bulk actions stop there/)).toBeInTheDocument();
    expect(screen.queryByText(/Every host matching/)).not.toBeInTheDocument();
  });

  it('the cap is the one the server states, not a number of the bar’s own', async () => {
    const user = userEvent.setup();
    // A server that reaches 300: 500 matching is capped…
    const { unmount } = renderBar(500, 300);
    await user.click(screen.getByRole('button', { name: 'Select the first 300 of 500 matching' }));
    expect(screen.getByText('300 selected')).toBeInTheDocument();
    expect(screen.getByText(/^The first 300 of 500 matching hosts — bulk actions stop there/)).toBeInTheDocument();
    unmount();
    // …and one that reaches 8,000 selects all 7,000.
    renderBar(7000, 8000);
    await user.click(screen.getByRole('button', { name: 'Select all 7,000 matching' }));
    expect(screen.getByText('7,000 selected')).toBeInTheDocument();
    expect(screen.getByText(/Every host matching the current filters, on every page/)).toBeInTheDocument();
  });

  it('with no cap stated, "all matching" is not offered: the bar cannot say how many it would reach', () => {
    renderBar(7000, null);
    expect(screen.getByText('2 selected')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /matching$/ })).not.toBeInTheDocument();
    expect(screen.getByText('checked rows only')).toBeInTheDocument();
  });
});

// The hand-off to an agent names a FIXED list: the checked rows, or — over
// "all matching" — the ids the server resolves under the list's filters as
// the dialog opens.
describe('HostBulkBar — Propose tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listHostTags.mockResolvedValue([]);
    mocked.listProjectMembers.mockResolvedValue([]);
  });
  const renderWith = (queryContext: Record<string, string>) => render(
    <MemoryRouter>
      <HostBulkBar
        selectedIds={[1, 2]}
        selectedIps={['10.0.0.1', '10.0.0.2']}
        totalMatching={3}
        bulkCap={SERVER_CAP}
        queryContext={queryContext}
        onClear={vi.fn()}
        onApplied={vi.fn()}
      />
    </MemoryRouter>,
  );

  it('the checked rows are handed over as they are, with no request', async () => {
    const user = userEvent.setup();
    renderWith({ q: 'port:22' });
    await user.click(screen.getByRole('button', { name: /Propose tests/ }));
    expect(await screen.findByText(/^2 hosts/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Hand to your agent/ }));
    expect((await screen.findByTestId('agent-task')).textContent).toContain('host ids): 1, 2.');
    expect(mocked.getMatchingHostIds).not.toHaveBeenCalled();
  });

  it('"all matching" is resolved by the server under the list’s filters, at each opening', async () => {
    const user = userEvent.setup();
    mocked.getMatchingHostIds.mockResolvedValue({ ids: [7, 8, 9], total: 3, capped: false, cap: SERVER_CAP });
    renderWith({ q: 'port:22' });
    await user.click(screen.getByRole('button', { name: 'Select all 3 matching' }));
    await user.click(screen.getByRole('button', { name: /Propose tests/ }));
    expect(await screen.findByText(/^3 hosts/)).toBeInTheDocument();
    expect(mocked.getMatchingHostIds).toHaveBeenCalledTimes(1);
    expect(mocked.getMatchingHostIds.mock.calls[0][1]).toEqual({ q: 'port:22' });
    await user.click(screen.getByRole('button', { name: /Hand to your agent/ }));
    expect((await screen.findByTestId('agent-task')).textContent).toContain('host ids): 7, 8, 9.');
  });

  it('what the reader wrote stays for the next opening, and the list is asked for again', async () => {
    const user = userEvent.setup();
    mocked.getMatchingHostIds.mockResolvedValue({ ids: [7, 8, 9], total: 3, capped: false, cap: SERVER_CAP });
    renderWith({});
    await user.click(screen.getByRole('button', { name: 'Select all 3 matching' }));
    await user.click(screen.getByRole('button', { name: /Propose tests/ }));
    await screen.findByText(/^3 hosts/);
    await user.type(screen.getByLabelText(/What to test/), 'SMB signing');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByLabelText(/What to test/)).not.toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /Propose tests/ }));
    expect(await screen.findByLabelText(/What to test/)).toHaveValue('SMB signing');
    await waitFor(() => expect(mocked.getMatchingHostIds).toHaveBeenCalledTimes(2));
  });
});

// A roster that failed to load was turned into an empty list: the Assign menu
// offered nobody (or "No members") with nothing to say why.
describe('HostBulkBar — the Assign menu when the members could not be loaded', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetProjectMembersCache();
    mocked.listHostTags.mockResolvedValue([]);
  });

  it('says it could not load them, and Retry fills the same menu', async () => {
    const user = userEvent.setup();
    mocked.listProjectMembers.mockRejectedValueOnce(new Error('503'));
    renderBar(2);
    await user.click(screen.getByRole('button', { name: /Assign/ }));
    const retry = await screen.findByRole('menuitem', { name: /members could not be loaded\. Retry/ });
    expect(screen.queryByText('No members')).not.toBeInTheDocument();

    mocked.listProjectMembers.mockResolvedValue([
      { id: 9, project_id: 1, user_id: 77, username: 'ben', full_name: 'Ben Okafor', role: 'analyst', created_at: '' },
    ]);
    await user.click(retry);
    expect(await screen.findByRole('menuitem', { name: 'Ben Okafor' })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /could not be loaded/ })).not.toBeInTheDocument();
    expect(mocked.listProjectMembers).toHaveBeenCalledTimes(2);
  });
});
