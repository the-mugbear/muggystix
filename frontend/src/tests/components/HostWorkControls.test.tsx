/**
 * Assign and tag from the host inspector (5.351.0).
 *
 * The inspector is also the Hosts page's side sheet.  A write here re-read
 * the host in the sheet (`getHost`) and nothing else, so the row behind it —
 * its assignee, its tags — and the filters' counts stayed as they were until
 * the list was next read.  A write now says those reads are out of date too.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  bulkAssignHosts: vi.fn(), bulkUnassignHosts: vi.fn(), bulkTagHosts: vi.fn(), listHostTags: vi.fn(),
  listProjectMembers: vi.fn(),
}));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 7, username: 'ana' } }) }));

import { readsOnScreen } from '../helpers/readsOnScreen';
import { AssigneeControl, TagControl } from '../../components/host-inspector/HostWorkControls';

// What is on screen behind the inspector when it is the Hosts page's side
// sheet.  (The host in the sheet is `['getHost', hostId]`, read again as
// before — by its own id, which a stand-in for "any read of that name" does
// not carry.)
const { reread, ReadsOnScreen } = readsOnScreen({
  getHosts: 'the rows behind it', getHostFilterData: 'the filters’ counts',
});
const everything = ['the filters’ counts', 'the rows behind it'];
const asked = () => reread.mock.calls.map(([what]) => what).sort();

beforeEach(() => {
  vi.clearAllMocks();
  api.listProjectMembers.mockResolvedValue([]);
  api.listHostTags.mockResolvedValue([]);
});

describe('HostWorkControls — a write is read back wherever the host is shown', () => {
  it('removing a tag reads the Hosts rows and the filters’ counts again — once each', async () => {
    api.bulkTagHosts.mockResolvedValue({ affected: 1 });
    render(<><ReadsOnScreen /><TagControl hostId={5} canEdit tags={[{ id: 3, name: 'web', color: null }]} /></>);
    fireEvent.click(screen.getByRole('button', { name: 'Remove tag web' }));
    await waitFor(() => expect(api.bulkTagHosts).toHaveBeenCalledWith([5], { tag_ids: [3], action: 'remove' }));
    await waitFor(() => expect(asked()).toEqual(everything));
  });

  it('“Assign to me” does too', async () => {
    const user = userEvent.setup();
    api.bulkAssignHosts.mockResolvedValue({ affected: 1 });
    render(<><ReadsOnScreen /><AssigneeControl hostId={5} canEdit assignees={[]} /></>);
    await user.click(screen.getByRole('button', { name: 'Assign this host' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Assign to me' }));
    await waitFor(() => expect(api.bulkAssignHosts).toHaveBeenCalledWith([5], 7));
    await waitFor(() => expect(asked()).toEqual(everything));
  });

  it('a refused write says why and reads nothing again', async () => {
    api.bulkTagHosts.mockRejectedValue(new Error('nope'));
    render(<><ReadsOnScreen /><TagControl hostId={5} canEdit tags={[{ id: 3, name: 'web', color: null }]} /></>);
    fireEvent.click(screen.getByRole('button', { name: 'Remove tag web' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(reread).not.toHaveBeenCalled();
  });
});
