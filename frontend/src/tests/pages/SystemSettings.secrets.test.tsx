/**
 * System settings → Users: the two requests that carry a password — creating
 * an account and resetting someone's password.
 *
 * The page stays mounted after both, so the library would keep what was sent
 * for as long as the administrator stays on it.  Besides what is sent and
 * what is said, the tests pin that the client holds no password once the
 * request has settled (`SECRET_MUTATION` + `reset()`, lib/query), and that a
 * dialog left without saving keeps none in its fields.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import { heldByMutations, withClient } from '../helpers/heldByMutations';

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
vi.mock('../../services/api', () => api);vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'ada' }, hasPermission: () => true }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../components/QueueHealthCard', () => ({ default: () => null }));
vi.mock('../../components/remediation/RemediationSettingsSection', () => ({ default: () => null }));
vi.mock('../../components/reports/ReportWritingGuidanceSection', () => ({ default: () => null }));
vi.mock('../../components/UserMembershipsDialog', () => ({ default: () => null }));

import SystemSettings from '../../pages/SystemSettings';

const PASSWORD = 'first-Passw0rd!given';
const account = (over: Record<string, unknown> = {}) => ({
  id: 7, username: 'grace', full_name: 'Grace Hopper', role: 'member', is_active: true,
  totp_enabled: false, created_at: '2026-09-01T10:00:00Z', last_login: null, created_by_id: 1,
  ...over,
});
const refused = (detail: string) => ({ response: { status: 400, data: { detail } } });

const show = () => {
  const client = createQueryClient();
  render(<MemoryRouter initialEntries={['/system-settings']}><SystemSettings /></MemoryRouter>, {
    wrapper: withClient(client),
  });
  return client;
};
/** The password is not held by a mutation any more. */
const expectForgotten = async (client: ReturnType<typeof createQueryClient>) => {
  await waitFor(() => expect(heldByMutations(client)).not.toContain(PASSWORD));
};

beforeEach(() => {
  vi.clearAllMocks();
  api.listUsers.mockResolvedValue([account()]);
});

describe('System settings — creating an account', () => {
  const openAndFill = async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Add User' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Username'), { target: { value: 'linus' } });
    fireEvent.change(within(dialog).getByLabelText('Full Name'), { target: { value: 'Linus T.' } });
    fireEvent.change(within(dialog).getByLabelText('Password'), { target: { value: PASSWORD } });
    fireEvent.change(within(dialog).getByLabelText('Confirm Password'), { target: { value: PASSWORD } });
    return dialog;
  };

  it('sends the account without the confirmation, lists it, and keeps the password nowhere', async () => {
    // The server answers the users-list row (5.365.0 / v2.476.0): what it says
    // is what the table holds — the page fills in nothing of its own.
    const answered = account({ id: 9, username: 'linus', full_name: 'Linus T.', created_by_id: 4, totp_enabled: true });
    api.registerUser.mockResolvedValue(answered);
    const client = show();
    await screen.findByText('@grace');
    const dialog = await openAndFill();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create User' }));

    expect(await screen.findByText('@linus')).toBeInTheDocument();
    expect(client.getQueryData<unknown[]>(['listUsers'])?.slice(-1)).toEqual([answered]);
    expect(api.registerUser).toHaveBeenCalledTimes(1);
    expect(api.registerUser).toHaveBeenCalledWith({
      username: 'linus', password: PASSWORD, full_name: 'Linus T.', role: 'member',
    });
    expect(toast.success).toHaveBeenCalledWith('User created.');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await expectForgotten(client);

    // The next account starts from an empty form.
    fireEvent.click(screen.getByRole('button', { name: 'Add User' }));
    const again = await screen.findByRole('dialog');
    expect(within(again).getByLabelText('Username')).toHaveValue('');
    expect(within(again).getByLabelText('Password')).toHaveValue('');
    expect(within(again).getByLabelText('Confirm Password')).toHaveValue('');
  });

  it('says why an account was refused, keeps the dialog, and keeps the password out of the client', async () => {
    api.registerUser.mockRejectedValue(refused('Username already registered'));
    const client = show();
    await screen.findByText('@grace');
    const dialog = await openAndFill();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create User' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Username already registered'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByText('@linus')).toBeNull();
    await expectForgotten(client);
    // What was typed stays in the form, to correct, and can be sent again.
    expect(within(dialog).getByLabelText('Password')).toHaveValue(PASSWORD);
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Create User' })).toBeEnabled());
  });

  it('forgets the password, and only the password, when the dialog is left', async () => {
    show();
    await screen.findByText('@grace');
    const dialog = await openAndFill();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(api.registerUser).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Add User' }));
    const again = await screen.findByRole('dialog');
    expect(within(again).getByLabelText('Username')).toHaveValue('linus');
    expect(within(again).getByLabelText('Full Name')).toHaveValue('Linus T.');
    expect(within(again).getByLabelText('Password')).toHaveValue('');
    expect(within(again).getByLabelText('Confirm Password')).toHaveValue('');
  });
});

describe('System settings — resetting a password', () => {
  const openAndFill = async () => {
    fireEvent.keyDown(await screen.findByRole('button', { name: 'More actions for grace' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Reset Password' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Reset Password: grace');
    fireEvent.change(within(dialog).getByLabelText('New Password'), { target: { value: PASSWORD } });
    fireEvent.change(within(dialog).getByLabelText('Confirm New Password'), { target: { value: PASSWORD } });
    return dialog;
  };

  it('sends the new password for that account, says so, and keeps it nowhere', async () => {
    api.resetUserPassword.mockResolvedValue(undefined);
    const client = show();
    const dialog = await openAndFill();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset Password' }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Password reset.'));
    expect(api.resetUserPassword).toHaveBeenCalledTimes(1);
    expect(api.resetUserPassword).toHaveBeenCalledWith(7, PASSWORD);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await expectForgotten(client);
  });

  it('says why a reset was refused, keeps the dialog, and keeps the password out of the client', async () => {
    api.resetUserPassword.mockRejectedValue(refused('Password was used recently'));
    const client = show();
    const dialog = await openAndFill();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset Password' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Password was used recently'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await expectForgotten(client);
    expect(within(dialog).getByLabelText('New Password')).toHaveValue(PASSWORD);
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Reset Password' })).toBeEnabled());
  });
});
