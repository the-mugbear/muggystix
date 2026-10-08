import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import HostBulkBar, { BULK_SELECT_CAP } from '../../components/hosts/HostBulkBar';

vi.mock('../../services/api', () => ({
  bulkTagHosts: vi.fn(),
  bulkAssignHosts: vi.fn(),
  bulkUnassignHosts: vi.fn(),
  bulkFollowHosts: vi.fn(),
  getMatchingHostIds: vi.fn(),
  listHostTags: vi.fn(),
  listProjectMembers: vi.fn(),
}));

import * as api from '../../services/api';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const renderBar = (totalMatching: number) =>
  render(
    <HostBulkBar
      selectedIds={[1, 2]}
      selectedIps={['10.0.0.1', '10.0.0.2']}
      totalMatching={totalMatching}
      queryContext={{}}
      onClear={vi.fn()}
      onApplied={vi.fn()}
    />,
  );

// The server resolves at most BULK_SELECT_CAP ids for "all matching".  Above
// it the bar used to promise "Select all 7,000 matching", count 7,000 selected
// and confirm 7,000 — then act on 5,000 and mention it in a toast afterwards.
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

  it('never calls a capped subset "all": the button, the count and the note name the cap up front', async () => {
    const user = userEvent.setup();
    const total = BULK_SELECT_CAP + 2000;
    renderBar(total);
    expect(screen.queryByRole('button', { name: /Select all/ })).not.toBeInTheDocument();

    await user.click(
      screen.getByRole('button', {
        name: `Select the first ${BULK_SELECT_CAP.toLocaleString()} of ${total.toLocaleString()} matching`,
      }),
    );
    expect(screen.getByText(`${BULK_SELECT_CAP.toLocaleString()} selected`)).toBeInTheDocument();
    expect(screen.getByText(/bulk actions stop there/)).toBeInTheDocument();
    expect(screen.queryByText(/Every host matching/)).not.toBeInTheDocument();
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
