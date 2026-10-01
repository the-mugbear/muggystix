/**
 * Where a notification opens (2026-10-01).  Two kinds had no link of their
 * own: a host test assigned to you fell through to the bare host page (or did
 * nothing when its tests were on several hosts), and a proposal on no finding
 * yet opened `/proposals?scope=mine` — the one view that hides it.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
const getNotifications = vi.fn();
vi.mock('../../services/api', () => ({
  getNoteActivity: vi.fn().mockResolvedValue({ notes: [], total_notes: 0, status_counts: {}, authors: [] }),
  getFindingDiscussions: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  markActivitySeen: vi.fn().mockResolvedValue(undefined),
  getNotifications: (...a: unknown[]) => getNotifications(...a),
  markNotificationsRead: vi.fn().mockResolvedValue(1),
  markAllNotificationsRead: vi.fn().mockResolvedValue(0),
}));

import Activity from '../../pages/Activity';
import { notificationHref } from '../../utils/notificationLinks';

const note = (over: Record<string, unknown>) => ({
  id: 1, type: 'assignment', title: 'A notification', body: null, source_type: null, source_id: null,
  host_id: null, finding_id: null, actor_id: 2, actor_username: 'ana', read_at: null,
  created_at: '2026-10-01T00:00:00Z', ...over,
});

const open = async (n: Record<string, unknown>) => {
  getNotifications.mockResolvedValue({ notifications: [n], total: 1, unread_count: 1 });
  render(<MemoryRouter initialEntries={['/activity?mentions=mine']}><Activity /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(String(n.title)) }));
};

beforeEach(() => { navigate.mockReset(); getNotifications.mockReset(); });

describe('Activity — opening a notification', () => {
  it('a host test assigned to you opens the test on its host', async () => {
    await open(note({ title: 'A test was assigned to you', source_type: 'host_test', source_id: 31, host_id: 5 }));
    expect(navigate).toHaveBeenCalledWith('/hosts/5#host-test-31');
  });

  it('tests on several hosts (no host on the notification) open My work', async () => {
    await open(note({ title: '4 tests were assigned to you', source_type: 'host_test', source_id: 31, host_id: null }));
    expect(navigate).toHaveBeenCalledWith('/operations');
  });

  it('a proposal on no finding yet opens the session’s proposals for EVERYONE’s findings', async () => {
    await open(note({
      type: 'proposal', title: 'An agent proposed a new finding', source_type: 'agent_session_new', source_id: 88,
      finding_id: null,
    }));
    expect(navigate).toHaveBeenCalledWith('/proposals?agent_session_id=88&scope=all');
  });
});

describe('notificationHref', () => {
  const href = (over: Record<string, unknown>) => notificationHref(note(over) as never);

  it('keeps the existing links', () => {
    expect(href({ type: 'proposal', finding_id: 7 })).toBe('/findings/7#proposals');
    expect(href({ type: 'proposal', source_type: 'agent_session', source_id: 88 }))
      .toBe('/proposals?agent_session_id=88&scope=mine');
    expect(href({ type: 'proposal' })).toBe('/proposals?scope=mine');
    expect(href({ source_type: 'scan', source_id: 4 })).toBe('/hosts?scan_ids=4');
    expect(href({ source_type: 'report_job', source_id: 4 })).toBe('/hosts?reports=1&job=4');
    expect(href({ type: 'mention', source_type: 'note', source_id: 9, finding_id: 7 })).toBe('/findings/7#note-9');
    expect(href({ type: 'mention', source_type: 'note', source_id: 9, host_id: 5 })).toBe('/hosts/5#note-9');
    expect(href({ host_id: 5 })).toBe('/hosts/5');
    expect(href({})).toBeNull();
  });

  it('a host-test notification never falls through to the bare host page', () => {
    expect(href({ source_type: 'host_test', source_id: 31, host_id: 5 })).toBe('/hosts/5#host-test-31');
    expect(href({ source_type: 'host_test', source_id: null, host_id: 5 })).toBe('/hosts/5#host-detail-proposed-tests');
    expect(href({ source_type: 'host_test', source_id: 31, host_id: null })).toBe('/operations');
  });

  it('a finding on the proposal notification still wins over the session', () => {
    expect(href({ type: 'proposal', source_type: 'agent_session_new', source_id: 88, finding_id: 7 }))
      .toBe('/findings/7#proposals');
  });
});
