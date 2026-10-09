/**
 * Collaboration — the author filter lives in the URL (5.329.0).
 *
 * Operations dropped its "My recent activity" column and links here instead
 * ("My activity" → /activity?author=me), so "mine" has to be a link: read on
 * arrival, resolved to the signed-in account, and kept across a reload.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 91 } }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 7, username: 'me' }, hasPermission: () => true }),
}));

const getNoteActivity = vi.fn();
const getFindingDiscussions = vi.fn();
vi.mock('../../services/api', () => ({
  getFindingDiscussions: (...a: unknown[]) => getFindingDiscussions(...a),
  getNoteActivity: (...a: unknown[]) => getNoteActivity(...a),
  listProjectMembers: vi.fn().mockResolvedValue([]),
  markActivitySeen: vi.fn().mockResolvedValue(undefined),
  getNotifications: vi.fn().mockResolvedValue({ notifications: [], total: 0, unread_count: 0 }),
  markNotificationsRead: vi.fn().mockResolvedValue(1),
  markAllNotificationsRead: vi.fn().mockResolvedValue(0),
}));

import Activity from '../../pages/Activity';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';

let search = '';
const Probe = () => {
  search = useLocation().search;
  return null;
};
const renderAt = (entry: string) => render(
  <MemoryRouter initialEntries={[entry]}><Probe /><Activity /></MemoryRouter>,
);

beforeEach(() => {
  resetProjectMembersCache();
  getNoteActivity.mockReset().mockResolvedValue({
    notes: [], total_notes: 0, status_counts: { open: 0, in_progress: 0, resolved: 0 },
    authors: [{ id: 2, name: 'alice' }, { id: 7, name: 'me' }],
  });
  getFindingDiscussions.mockReset().mockResolvedValue({ items: [], total: 0 });
});

describe('Collaboration — author filter in the URL', () => {
  it('?author=me asks both feeds for the signed-in account’s messages', async () => {
    renderAt('/activity?author=me');
    await waitFor(() => expect(getNoteActivity).toHaveBeenCalledWith(91, expect.objectContaining({ author_id: 7 }), expect.any(AbortSignal)));
    await waitFor(() => expect(getFindingDiscussions).toHaveBeenCalledWith(
      91, expect.objectContaining({ author_id: 7 }), expect.anything(),
    ));
    expect(await screen.findByRole('combobox', { name: 'Author' })).toHaveTextContent('Mine');
    // The link's parameter is kept as it was given.
    expect(search).toBe('?author=me');
  });

  it('?author=<id> filters to that author; no parameter asks for everyone', async () => {
    const first = renderAt('/activity?author=2');
    await waitFor(() => expect(getNoteActivity).toHaveBeenCalledWith(91, expect.objectContaining({ author_id: 2 }), expect.any(AbortSignal)));
    first.unmount();
    getNoteActivity.mockClear();
    renderAt('/activity');
    await waitFor(() => expect(getNoteActivity).toHaveBeenCalled());
    expect(getNoteActivity.mock.calls[0][0]).toBe(91);
    expect(getNoteActivity.mock.calls[0][1]).not.toHaveProperty('author_id');
  });

  it('ignores a value that is neither "me" nor an id', async () => {
    renderAt('/activity?author=%27%3B--');
    await waitFor(() => expect(getNoteActivity).toHaveBeenCalled());
    expect(getNoteActivity.mock.calls[0][0]).toBe(91);
    expect(getNoteActivity.mock.calls[0][1]).not.toHaveProperty('author_id');
  });
});
