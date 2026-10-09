/**
 * Collaboration — finding comments and host-note threads are ONE feed
 * (v5.295.0). They were two blocks: finding comments above the notes with a
 * relative age and "N comments", the notes under day headings with a time of
 * day and "N entries", and the page's counts ignored the finding comments.
 */
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 91 } }),
}));

const getNoteActivity = vi.fn();
const getFindingDiscussions = vi.fn();
vi.mock('../../services/api', () => ({
  getFindingDiscussions: (...a: unknown[]) => getFindingDiscussions(...a),
  getNoteActivity: (...a: unknown[]) => getNoteActivity(...a),
  listProjectMembers: vi.fn().mockResolvedValue([{ username: 'eval-ana', full_name: 'Ana' }]),
  markActivitySeen: vi.fn().mockResolvedValue(undefined),
  getNotifications: vi.fn().mockResolvedValue({ notifications: [], total: 0, unread_count: 0 }),
  markNotificationsRead: vi.fn().mockResolvedValue(1),
  markAllNotificationsRead: vi.fn().mockResolvedValue(0),
}));

import Activity from '../../pages/Activity';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';

const note = (over: Record<string, unknown>) => ({
  note_id: 1, host_id: 5, ip_address: '10.0.0.5', hostname: 'h5', body: 'a note', status: 'open',
  author_name: 'alice', author_id: 2, parent_id: null, thread_root_id: 1, thread_root_status: 'open',
  created_at: '2026-09-10T10:00:00Z', updated_at: null, host_note_count: 1, attachments: [], ...over,
});

const payload = (notes: unknown[], extra: Record<string, unknown> = {}) => ({
  notes, total_notes: notes.length, status_counts: { open: 1, in_progress: 0, resolved: 0 },
  authors: [{ id: 2, name: 'alice' }], ...extra,
});

const discussion = (over: Record<string, unknown>) => ({
  finding_id: 37, title: 'SMB Signing Not Required', severity: 'high', status: 'confirmed',
  comment_count: 1, last_activity_at: '2026-09-10T11:00:00Z', participants: ['ana'],
  latest: { note_id: 88, body: 'Verified relay. @eval-ana please retest.', author_name: 'ana', actor_type: 'user', created_at: '2026-09-10T11:00:00Z' },
  ...over,
});

beforeEach(() => {
  resetProjectMembersCache();
  getNoteActivity.mockReset();
  getFindingDiscussions.mockReset().mockResolvedValue({ items: [], total: 0 });
});

const rowsIn = (container: HTMLElement) =>
  [...container.querySelectorAll('a[data-thread], a[data-discussion]')] as HTMLElement[];

describe('Collaboration — one feed', () => {
  it('interleaves finding discussions with host threads, latest first, in the same day groups', async () => {
    getNoteActivity.mockResolvedValue(payload([
      note({ note_id: 1, body: 'newest host note', created_at: '2026-09-10T12:00:00Z' }),
      note({ note_id: 2, host_id: 6, ip_address: '10.0.0.6', thread_root_id: 2, body: 'older host note', created_at: '2026-09-10T09:00:00Z' }),
    ]));
    getFindingDiscussions.mockResolvedValue({ total: 1, items: [discussion({})] });
    const { container } = render(<MemoryRouter><Activity /></MemoryRouter>);
    await screen.findByText('newest host note');
    await waitFor(() => expect(rowsIn(container)).toHaveLength(3));

    // 12:00 note, 11:00 finding, 09:00 note — one list, not a finding block on top.
    const order = rowsIn(container).map((a) => a.getAttribute('href'));
    expect(order).toEqual(['/hosts/5#note-1', '/findings/37#note-88', '/hosts/6#note-2']);
    const sections = container.querySelectorAll('section[aria-label]');
    expect(sections).toHaveLength(1);
    expect(screen.queryByRole('heading', { name: /comments on findings/i })).not.toBeInTheDocument();
  });

  it('both kinds show a status chip, the time of day and a count in "messages"', async () => {
    const at = '2026-09-10T11:00:00Z';
    getNoteActivity.mockResolvedValue(payload([note({ created_at: at })]));
    getFindingDiscussions.mockResolvedValue({ total: 1, items: [discussion({ last_activity_at: at })] });
    const { container } = render(<MemoryRouter><Activity /></MemoryRouter>);
    await waitFor(() => expect(rowsIn(container)).toHaveLength(2));
    const clock = new Date(at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    for (const row of rowsIn(container)) {
      expect(row.querySelector('time')?.textContent).toBe(clock);
      expect(within(row).getByText('1 message')).toBeInTheDocument();
    }
    const finding = container.querySelector('a[data-discussion]') as HTMLElement;
    expect(within(finding).getByText('Confirmed')).toBeInTheDocument();
    expect(within(finding).getByText('ana')).toBeInTheDocument();
  });

  it('marks @mentions of members in both kinds of row', async () => {
    getNoteActivity.mockResolvedValue(payload([note({ body: '@eval-ana can you confirm the banner?' })]));
    getFindingDiscussions.mockResolvedValue({ total: 1, items: [discussion({})] });
    const { container } = render(<MemoryRouter><Activity /></MemoryRouter>);
    await waitFor(() => expect(container.querySelectorAll('[title="Mentions eval-ana"]')).toHaveLength(2));
  });

  it('the header counts finding discussions too', async () => {
    getNoteActivity.mockResolvedValue(payload([note({})]));
    getFindingDiscussions.mockResolvedValue({ total: 1, items: [discussion({})] });
    render(<MemoryRouter><Activity /></MemoryRouter>);
    await screen.findByText('a note');
    await waitFor(() => expect(screen.getByLabelText('Discussions in view')).toHaveTextContent(
      '1 host-note thread on 1 host · 1 finding discussion in view',
    ));
  });

  // 5.351.0 — a figure whose rows have not been read is not known: the header
  // said "0 host-note threads on 0 hosts · 0 finding discussions" while a
  // first load or a new filter was on its way, and after a read that failed.
  it('the header’s counts are “…” until their rows have been read, never 0', async () => {
    let answerNotes!: (v: unknown) => void;
    let answerDiscussions!: (v: unknown) => void;
    getNoteActivity.mockReturnValue(new Promise((resolve) => { answerNotes = resolve; }));
    getFindingDiscussions.mockReturnValue(new Promise((resolve) => { answerDiscussions = resolve; }));
    render(<MemoryRouter><Activity /></MemoryRouter>);
    const header = screen.getByLabelText('Discussions in view');
    expect(header).toHaveTextContent('… host-note threads on … hosts · … finding discussions in view');

    // Each figure is known as soon as ITS rows are.
    await act(async () => { answerNotes(payload([note({})])); });
    await waitFor(() => expect(header).toHaveTextContent(
      '1 host-note thread on 1 host · … finding discussions in view',
    ));
    await act(async () => { answerDiscussions({ total: 0, items: [] }); });
    // A list that was read and holds nothing IS zero.
    await waitFor(() => expect(header).toHaveTextContent(
      '1 host-note thread on 1 host · 0 finding discussions in view',
    ));
  });

  it('a count that could not be read is “—”, not 0 and not still loading', async () => {
    getNoteActivity.mockRejectedValue(new Error('down'));
    getFindingDiscussions.mockRejectedValue(new Error('down'));
    render(<MemoryRouter><Activity /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText('Discussions in view')).toHaveTextContent(
      '— host-note threads on — hosts · — finding discussions in view',
    ));
  });

  // 5.325.0 — a host note has no status: nothing filters by one, so finding
  // discussions are never left out of the feed. (Replaces "a note status
  // leaves finding discussions out, and says so with a way back".)
  it('has no note-status filter or counts, and a note row carries no status chip', async () => {
    getNoteActivity.mockResolvedValue(payload([note({})]));
    getFindingDiscussions.mockResolvedValue({ total: 1, items: [discussion({})] });
    const { container } = render(<MemoryRouter><Activity /></MemoryRouter>);
    await waitFor(() => expect(rowsIn(container)).toHaveLength(2));
    expect(screen.queryByRole('combobox', { name: 'Host note status' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Host notes by status')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /\d+ (open|resolved|in progress)$/ })).not.toBeInTheDocument();
    const noteRow = container.querySelector('a[data-thread]') as HTMLElement;
    expect(noteRow).not.toHaveTextContent(/Open|Resolved|In Progress/);
    expect(getNoteActivity.mock.calls.every(([, params]) => !('status' in (params ?? {})))).toBe(true);
  });

  it('while notes remain to load, an older finding discussion waits instead of jumping the queue', async () => {
    getNoteActivity.mockResolvedValue(payload(
      [note({ created_at: '2026-09-10T12:00:00Z', body: 'loaded note' })],
      { total_notes: 150 },
    ));
    getFindingDiscussions.mockResolvedValue({
      total: 2,
      items: [
        discussion({ finding_id: 1, title: 'Newer finding', last_activity_at: '2026-09-10T13:00:00Z' }),
        discussion({ finding_id: 2, title: 'Older finding', last_activity_at: '2026-09-01T09:00:00Z' }),
      ],
    });
    render(<MemoryRouter><Activity /></MemoryRouter>);
    await screen.findByText('Newer finding');
    expect(screen.queryByText('Older finding')).not.toBeInTheDocument();
    expect(screen.getByText('1 older finding discussion will appear in order as more notes load.')).toBeInTheDocument();
  });

  it('search and author reach both sources', async () => {
    getNoteActivity.mockResolvedValue(payload([note({})]));
    render(<MemoryRouter><Activity /></MemoryRouter>);
    await screen.findByText('a note');
    fireEvent.change(screen.getByLabelText('Search discussions'), { target: { value: 'relay' } });
    await waitFor(() => {
      expect(getNoteActivity).toHaveBeenLastCalledWith(91, expect.objectContaining({ search: 'relay' }), expect.any(AbortSignal));
      expect(getFindingDiscussions).toHaveBeenLastCalledWith(91, expect.objectContaining({ search: 'relay' }), expect.anything());
    });
  });
});
