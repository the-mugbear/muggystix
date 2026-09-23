/**
 * Collaboration → Activity — one row per thread, grouped by day, the latest
 * message shown once, the whole row opening the thread; no stat cards.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

const getNoteActivity = vi.fn();
vi.mock('../../services/api', () => ({
  getNoteActivity: (...a: unknown[]) => getNoteActivity(...a),
  markActivitySeen: vi.fn().mockResolvedValue(undefined),
  getNotifications: vi.fn().mockResolvedValue({ notifications: [], total: 0, unread_count: 0 }),
  markNotificationsRead: vi.fn().mockResolvedValue(1),
  markAllNotificationsRead: vi.fn().mockResolvedValue(0),
}));

import Activity from '../../pages/Activity';

const note = (over: Record<string, unknown>) => ({
  note_id: 1, host_id: 5, ip_address: '10.0.0.5', hostname: 'h5', body: 'a note', status: 'open',
  author_name: 'alice', author_id: 2, parent_id: null, thread_root_id: 1, thread_root_status: 'open',
  created_at: '2026-09-10T10:00:00Z', updated_at: null, host_note_count: 1, attachments: [], ...over,
});

const payload = (notes: unknown[], counts = { open: 3, in_progress: 0, resolved: 1 }) => ({
  notes, total_notes: notes.length, status_counts: counts, authors: [{ id: 2, name: 'alice' }],
});

beforeEach(() => {
  getNoteActivity.mockReset();
});

describe('Activity — threads as rows', () => {
  it('one row per thread, latest message once, the row opens the thread', async () => {
    getNoteActivity.mockResolvedValue(payload([
      // Thread A on host 5: root + a later reply.
      note({ note_id: 1, body: 'Investigated 10.0.0.5: needs follow-up.' }),
      note({ note_id: 2, parent_id: 1, body: 'Confirmed exposed service.', author_name: 'ben', created_at: '2026-09-10T12:00:00Z' }),
      // Thread B on host 6: a single note — shown once, never twice.
      note({ note_id: 9, host_id: 6, ip_address: '10.0.0.6', hostname: null, thread_root_id: 9, body: 'Only entry here.', created_at: '2026-09-08T09:00:00Z' }),
    ]));
    const { container } = render(<MemoryRouter><Activity /></MemoryRouter>);
    await screen.findByText('Confirmed exposed service.');

    const rows = container.querySelectorAll('a[data-thread]');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute('href', '/hosts/5#note-1');
    expect(rows[1]).toHaveAttribute('href', '/hosts/6#note-9');

    // The latest message is the row's text; the older one is not repeated.
    expect(within(rows[0] as HTMLElement).queryByText(/needs follow-up/)).not.toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText('2 entries')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText('with alice')).toBeInTheDocument();
    // (The old card printed it twice: "Latest update: …" and the entry itself.)
    expect(screen.getAllByText(/Only entry here\./)).toHaveLength(1);

    // Grouped by day, latest first; no per-row button and no stat cards.
    const days = [...container.querySelectorAll('section[aria-label] > h2')].map((h) => h.textContent);
    expect(days).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Open thread' })).not.toBeInTheDocument();
    expect(screen.queryByText('Hosts in View')).not.toBeInTheDocument();
    expect(container.querySelector('.bg-card.shadow-raised')).toBeNull();
  });

  it('counts the whole thread, not only the entries on the loaded page', async () => {
    getNoteActivity.mockResolvedValue(payload([
      note({ note_id: 4, parent_id: 1, body: 'Newest reply.', thread_note_count: 5, host_note_count: 5 }),
    ]));
    const { container } = render(<MemoryRouter><Activity /></MemoryRouter>);
    await screen.findByText('Newest reply.');
    const row = container.querySelector('a[data-thread]') as HTMLElement;
    expect(within(row).getByText('5 entries')).toBeInTheDocument();
  });

  it('the status counts filter the feed, as the stat cards did', async () => {
    getNoteActivity.mockResolvedValue(payload([note({})]));
    render(<MemoryRouter><Activity /></MemoryRouter>);
    const resolved = await screen.findByRole('button', { name: '1 resolved' });
    fireEvent.click(resolved);
    await waitFor(() => expect(getNoteActivity).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'resolved' })));
    expect(screen.getByRole('button', { name: '1 resolved' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('offers Load more while notes remain', async () => {
    getNoteActivity.mockResolvedValue({ ...payload([note({})]), total_notes: 150 });
    render(<MemoryRouter><Activity /></MemoryRouter>);
    const more = await screen.findByRole('button', { name: 'Load more (149 more)' });
    fireEvent.click(more);
    await waitFor(() => expect(getNoteActivity).toHaveBeenLastCalledWith(expect.objectContaining({ skip: 1 })));
  });
});
