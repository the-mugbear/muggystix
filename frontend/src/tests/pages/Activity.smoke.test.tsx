import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock('../../services/api', () => ({
  getNoteActivity: vi.fn().mockResolvedValue({
    notes: [
      {
        note_id: 1, host_id: 5, ip_address: '10.0.0.5', hostname: 'h5',
        body: 'a note', status: 'open', author_name: 'alice', author_id: 2,
        parent_id: null, thread_root_id: 1, thread_root_status: 'open',
        created_at: '2026-06-14T00:00:00Z', updated_at: null, host_note_count: 1,
      },
    ],
    total_notes: 1,
    status_counts: { open: 1, in_progress: 0, resolved: 0 },
    authors: [{ id: 2, name: 'alice' }],
  }),
  getFindingDiscussions: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  markActivitySeen: vi.fn().mockResolvedValue(undefined),
  getNotifications: vi.fn().mockResolvedValue({ notifications: [], total: 0, unread_count: 0 }),
  markNotificationsRead: vi.fn().mockResolvedValue(1),
  markAllNotificationsRead: vi.fn().mockResolvedValue(0),
}));

import Activity, { excerpt } from '../../pages/Activity';

describe('Activity page smoke', () => {
  it('renders without crashing', async () => {
    render(<MemoryRouter><Activity /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Collaboration')).toBeInTheDocument());
  });

  // Report 2026-09-26: arriving from the Mentions button with nothing unread
  // landed on the plain feed with no word about mentions.
  it('says there is nothing unread when opened from the Mentions button', async () => {
    render(<MemoryRouter initialEntries={['/activity?mentions=mine']}><Activity /></MemoryRouter>);
    expect(await screen.findByText(/No unread notifications/)).toBeInTheDocument();
  });

  // Analyst walkthrough 2026-10-10: a preview read "## [seed] Assessment SMB
  // … **null session**" — the note renders as Markdown where it lives.
  it('a preview drops Markdown marks and keeps the words and the mention', () => {
    expect(excerpt('## Assessment\n\nSMB accepts a **null session**. `445/tcp` open — see [the note](https://x.test), @alice\n- one\n1. two'))
      .toBe('Assessment SMB accepts a null session. 445/tcp open — see the note, @alice one two');
    expect(excerpt('snake_case and 2 * 3 stay')).toBe('snake_case and 2 * 3 stay');
    expect(excerpt('x'.repeat(300))).toHaveLength(201);
  });

  it('does not show that line on a plain visit', async () => {
    render(<MemoryRouter><Activity /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Collaboration')).toBeInTheDocument());
    expect(screen.queryByText(/No unread notifications/)).toBeNull();
  });
});
