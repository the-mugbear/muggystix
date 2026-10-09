/**
 * System settings → Users: the accounts could not be read.
 *
 * It was a toast over an empty table headed "User management 0": gone in
 * seconds, with nothing to press, on a page that then looked like an
 * installation without accounts.  It is said where the table would be, with
 * Retry — and an account created meanwhile is not shown as the only one.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../components/QueueHealthCard', () => ({ default: () => null }));
vi.mock('../../components/remediation/RemediationSettingsSection', () => ({ default: () => null }));
vi.mock('../../components/reports/ReportWritingGuidanceSection', () => ({ default: () => null }));
vi.mock('../../components/UserMembershipsDialog', () => ({ default: () => null }));

import SystemSettings from '../../pages/SystemSettings';

const account = (over: Record<string, unknown> = {}) => ({
  id: 7, username: 'grace', full_name: 'Grace Hopper', role: 'member', is_active: true,
  totp_enabled: false, created_at: '2026-09-01T10:00:00Z', last_login: null, created_by_id: 1,
  ...over,
});
const down = { response: { status: 503, data: { detail: 'The account store is not answering.' } } };
const show = () => render(<MemoryRouter initialEntries={['/system-settings']}><SystemSettings /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  api.listUsers.mockResolvedValue([account()]);
});

describe('System settings — the accounts could not be read', () => {
  it('says so in place of the table, with Retry — not an empty table, and not as a toast', async () => {
    api.listUsers.mockRejectedValueOnce(down);
    show();

    const alert = await screen.findByText('The account store is not answering.');
    expect(alert).toHaveAttribute('role', 'alert');
    // No table of nobody, and no count of 0 beside the heading.
    expect(screen.queryByRole('columnheader', { name: 'Last login' })).toBeNull();
    expect(screen.getByRole('heading', { name: /User management/ })).toHaveTextContent(/^User management$/);
    expect(toast.error).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('@grace')).toBeInTheDocument();
    expect(screen.queryByText('The account store is not answering.')).toBeNull();
    expect(api.listUsers).toHaveBeenCalledTimes(2);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('an account created while the list is unread is not shown as the only account', async () => {
    api.listUsers.mockRejectedValueOnce(down);
    api.registerUser.mockResolvedValue(account({ id: 9, username: 'linus', full_name: 'Linus T.' }));
    show();
    await screen.findByText('The account store is not answering.');

    fireEvent.click(screen.getByRole('button', { name: 'Add User' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Username'), { target: { value: 'linus' } });
    fireEvent.change(within(dialog).getByLabelText('Full Name'), { target: { value: 'Linus T.' } });
    fireEvent.change(within(dialog).getByLabelText('Password'), { target: { value: 'first-Passw0rd!given' } });
    fireEvent.change(within(dialog).getByLabelText('Confirm Password'), { target: { value: 'first-Passw0rd!given' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create User' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('User created.'));

    // The list is read (the write says it is out of date): every account, not a list of one.
    expect(await screen.findByText('@grace')).toBeInTheDocument();
    expect(screen.queryByText('The account store is not answering.')).toBeNull();
  });
});
