/**
 * Where a notification opens (2026-10-01).  Two kinds had no link of their
 * own: a host test assigned to you fell through to the bare host page (or did
 * nothing when its tests were on several hosts), and a proposal on no finding
 * yet opened `/proposals?scope=mine` — the one view that hides it.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  getNotifications: (...a: unknown[]) => getNotifications(...a),
  markNotificationsRead: vi.fn().mockResolvedValue(1),
  markAllNotificationsRead: vi.fn().mockResolvedValue(0),
}));

import { readsOnScreen } from '../helpers/readsOnScreen';
import Activity from '../../pages/Collaboration';
import { notificationHref } from '../../utils/notificationLinks';

const note = (over: Record<string, unknown>) => ({
  id: 1, type: 'assignment', title: 'A notification', body: null, source_type: null, source_id: null,
  host_id: null, finding_id: null, actor_id: 2, actor_username: 'ana', read_at: null,
  created_at: '2026-10-01T00:00:00Z', ...over,
});

const open = async (n: Record<string, unknown>) => {
  getNotifications.mockResolvedValue({ notifications: [n], total_unread: 1 });
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

// 5.351.0 — the page told the top bar through a window event
// (`nm:notifications-marked-read`) that Layout listened for.  Marking read
// now says the bell's count is out of date; this stands in for the bell's
// read, which is the reader's whatever the project (its key names none).
describe('Activity — the bell follows a notification marked read', () => {
  const bell = () => readsOnScreen({ getUnreadNotificationCount: 'the bell’s count' });
  const openPage = async (ReadsOnScreen: React.FC) => {
    getNotifications.mockResolvedValue({
      notifications: [note({ id: 1, title: 'First' }), note({ id: 2, title: 'Second' })], total_unread: 2,
    });
    render(<MemoryRouter initialEntries={['/activity']}><ReadsOnScreen /><Activity /></MemoryRouter>);
    await screen.findByRole('button', { name: /First/ });
  };

  it('opening one reads the count again, once, and takes it out of the panel', async () => {
    const { reread, ReadsOnScreen } = bell();
    await openPage(ReadsOnScreen);
    expect(reread).not.toHaveBeenCalled();
    // The server's answer once it is read (the list is read again too).
    getNotifications.mockResolvedValue({ notifications: [note({ id: 2, title: 'Second' })], total_unread: 1 });
    fireEvent.click(screen.getByRole('button', { name: /First/ }));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('the bell’s count'));
    expect(reread).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: /First/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Second/ })).toBeInTheDocument();
  });

  it('“Mark all read” reads the count again, once', async () => {
    const { reread, ReadsOnScreen } = bell();
    await openPage(ReadsOnScreen);
    getNotifications.mockResolvedValue({ notifications: [], total_unread: 0 });
    fireEvent.click(screen.getByRole('button', { name: 'Mark all read' }));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('the bell’s count'));
    expect(reread).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: /Second/ })).not.toBeInTheDocument();
  });
});

// Review 2026-10-14, H11: the list is the first 50; the heading printed its
// length as the total, and the 51st never came up after a dismissal.
describe('Activity — more unread notifications than the list holds', () => {
  it('says the server’s total, and how many are shown', async () => {
    const fifty = Array.from({ length: 50 }, (_, i) => note({ id: i + 1, title: `Note ${i + 1}` }));
    getNotifications.mockResolvedValue({ notifications: fifty, total_unread: 60 });
    render(<MemoryRouter initialEntries={['/activity']}><Activity /></MemoryRouter>);
    expect(await screen.findByText('60 unread notifications (the latest 50 shown)')).toBeInTheDocument();
  });

  it('a dismissal reads the list again, so the next one comes up', async () => {
    const fifty = Array.from({ length: 50 }, (_, i) => note({ id: i + 1, title: `Note ${i + 1}` }));
    getNotifications.mockResolvedValue({ notifications: fifty, total_unread: 51 });
    render(<MemoryRouter initialEntries={['/activity']}><Activity /></MemoryRouter>);
    await screen.findByRole('button', { name: /Note 1(?!\d)/ });
    getNotifications.mockResolvedValue({
      notifications: [...fifty.slice(1), note({ id: 51, title: 'Note 51' })], total_unread: 50,
    });
    fireEvent.click(screen.getByRole('button', { name: /Note 1(?!\d)/ }));
    expect(await screen.findByRole('button', { name: /Note 51/ })).toBeInTheDocument();
    expect(screen.getByText('50 unread notifications')).toBeInTheDocument();
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

  // 5.395.0 — a host now awaits a retest: the finding, at that host's row,
  // even when the notification also names the host.
  it('a retest notification opens the finding at its host', () => {
    expect(href({ type: 'retest', source_type: 'finding_retest', source_id: 779, finding_id: 355, host_id: 5 }))
      .toBe('/findings/355?endpoint=779#endpoints');
  });

  // Third review 2026-10-15, N13: one notification per person for an action
  // over several findings — it opens the project's retest list.
  it('a notification about retests on several findings opens the retest list', () => {
    expect(href({ type: 'retest', source_type: 'finding_retests', source_id: null }))
      .toBe('/findings?host_state=retest&status=all');
  });

  it('a finding on the proposal notification still wins over the session', () => {
    expect(href({ type: 'proposal', source_type: 'agent_session_new', source_id: 88, finding_id: 7 }))
      .toBe('/findings/7#proposals');
  });
});
