import React from 'react';
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
  markActivitySeen: vi.fn().mockResolvedValue(undefined),
  getNotifications: vi.fn().mockResolvedValue({ notifications: [], total: 0, unread_count: 0 }),
  markNotificationsRead: vi.fn().mockResolvedValue(1),
  markAllNotificationsRead: vi.fn().mockResolvedValue(0),
}));

import Activity from '../../pages/Activity';

describe('Activity page smoke', () => {
  it('renders without crashing', async () => {
    render(<MemoryRouter><Activity /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Collaboration')).toBeInTheDocument());
  });
});
