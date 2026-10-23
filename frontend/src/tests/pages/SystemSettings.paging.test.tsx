/**
 * System settings → Users pages (review 2026-10-23, H-DB3).
 *
 * The page read `GET /users/` with no paging, and the server answered its
 * first 100 accounts in no order: an installation with more showed 100 and
 * said nothing.  The page now asks one page at a time, counts EVERY account
 * in the heading, says which are on screen, and keeps the page in the address.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The real router: setupTests replaces useLocation with a fixed one.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const api = vi.hoisted(() => ({
  listUsers: vi.fn(),
  registerUser: vi.fn(),
  updateUserAccount: vi.fn(),
  deleteUser: vi.fn(),
  resetUserPassword: vi.fn(),
  resetUserTwoFactor: vi.fn(),
}));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'ada' }, hasPermission: () => true }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../components/QueueHealthCard', () => ({ default: () => null }));
vi.mock('../../components/remediation/RemediationSettingsSection', () => ({ default: () => null }));
vi.mock('../../components/reports/ReportWritingGuidanceSection', () => ({ default: () => null }));
vi.mock('../../components/reports/ReportDefaultsSection', () => ({ default: () => null }));
vi.mock('../../components/UserMembershipsDialog', () => ({ default: () => null }));

import SystemSettings from '../../pages/SystemSettings';

const TOTAL = 150;
const account = (i: number) => ({
  id: 100 + i, username: `acct-${String(i).padStart(3, '0')}`, full_name: null, role: 'member',
  is_active: true, totp_enabled: false, created_at: '2026-09-01T10:00:00Z', last_login: null, created_by_id: 1,
});
const answer = ({ limit, offset }: { limit: number; offset: number }) => {
  const items = Array.from({ length: Math.max(0, Math.min(limit, TOTAL - offset)) }, (_, k) => account(offset + k));
  return Promise.resolve({ items, total: TOTAL, limit, offset, returned: items.length, has_more: offset + items.length < TOTAL });
};

const Where = () => <span data-testid="where">{useLocation().search}</span>;
const show = (path = '/system-settings') => render(
  <MemoryRouter initialEntries={[path]}><SystemSettings /><Where /></MemoryRouter>,
);

beforeEach(() => {
  vi.clearAllMocks();
  api.listUsers.mockImplementation(answer);
});

describe('System settings — the users list pages', () => {
  it('counts every account, says which are on screen, and asks for the next page', async () => {
    show();
    expect(await screen.findByText('@acct-000')).toBeInTheDocument();
    expect(api.listUsers).toHaveBeenCalledWith({ offset: 0, limit: 100 }, expect.anything());
    expect(screen.getByRole('heading', { name: /User management/ })).toHaveTextContent('150');
    expect(screen.getByText('1–100 of 150')).toBeInTheDocument();
    expect(screen.queryByText('@acct-100')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Next 100 users' }));
    expect(await screen.findByText('@acct-149')).toBeInTheDocument();
    expect(api.listUsers).toHaveBeenLastCalledWith({ offset: 100, limit: 100 }, expect.anything());
    expect(screen.getByText('101–150 of 150')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('?page=2');
  });

  it('opens the page the address names', async () => {
    show('/system-settings?page=2');
    expect(await screen.findByText('@acct-100')).toBeInTheDocument();
    await waitFor(() => expect(api.listUsers).toHaveBeenCalledWith({ offset: 100, limit: 100 }, expect.anything()));
  });
});
