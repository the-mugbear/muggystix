/**
 * The forced password change (an account flagged `must_change_password`):
 * the current password, a new one that meets the policy, twice; then the
 * reader is signed out, because the server has revoked every session.
 *
 * The request carries both passwords.  Besides the flow as the reader sees
 * it, the tests pin that the client holds neither once the request has
 * settled (`SECRET_MUTATION` + `reset()`, lib/query).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import { heldByMutations, withClient } from '../helpers/heldByMutations';

const api = vi.hoisted(() => ({ changeOwnPassword: vi.fn() }));
vi.mock('../../services/api', () => api);const { logout, updateUser } = vi.hoisted(() => ({ logout: vi.fn(), updateUser: vi.fn() }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ logout, updateUser }) }));

import ForceChangePassword from '../../pages/ForceChangePassword';

const OLD = 'old-Passw0rd!first';
const NEW = 'new-Passw0rd!42second';

const show = () => {
  const client = createQueryClient();
  render(<ForceChangePassword />, { wrapper: withClient(client) });
  return client;
};
const fill = (current: string, next: string, again: string) => {
  fireEvent.change(screen.getByLabelText('Current Password'), { target: { value: current } });
  fireEvent.change(screen.getByLabelText('New Password'), { target: { value: next } });
  fireEvent.change(screen.getByLabelText('Confirm New Password'), { target: { value: again } });
};
const submit = () => screen.getByRole('button', { name: 'Change Password' });
/** Neither password is held by a mutation any more. */
const expectForgotten = async (client: ReturnType<typeof createQueryClient>) => {
  await waitFor(() => {
    const held = heldByMutations(client);
    expect(held).not.toContain(OLD);
    expect(held).not.toContain(NEW);
  });
};

beforeEach(() => vi.clearAllMocks());

describe('ForceChangePassword', () => {
  it('offers the change only for a new password that meets the policy and is typed twice', () => {
    show();
    expect(screen.getByRole('heading', { name: 'Password Change Required' })).toBeInTheDocument();
    expect(submit()).toBeDisabled();

    fill(OLD, 'short', 'short');
    expect(submit()).toBeDisabled();                        // the policy

    fill(OLD, NEW, `${NEW}x`);
    expect(submit()).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Passwords do not match.');

    fill(OLD, NEW, NEW);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(submit()).toBeEnabled();
    expect(api.changeOwnPassword).not.toHaveBeenCalled();
  });

  it('sends the two passwords, clears the flag and signs out; neither password is kept', async () => {
    api.changeOwnPassword.mockResolvedValue(undefined);
    const client = show();
    fill(OLD, NEW, NEW);
    fireEvent.click(submit());

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    // The confirmation is this page's own check: it is not sent.
    expect(api.changeOwnPassword).toHaveBeenCalledTimes(1);
    expect(api.changeOwnPassword).toHaveBeenCalledWith({ current_password: OLD, new_password: NEW });
    expect(updateUser).toHaveBeenCalledWith({ must_change_password: false });
    expect(updateUser.mock.invocationCallOrder[0]).toBeLessThan(logout.mock.invocationCallOrder[0]);
    await expectForgotten(client);
  });

  it('says why the change was refused, stays signed in, and keeps neither password', async () => {
    api.changeOwnPassword.mockRejectedValue({ response: { status: 400, data: { detail: 'Invalid current password' } } });
    const client = show();
    fill(OLD, NEW, NEW);
    fireEvent.click(submit());

    expect(await screen.findByText('Invalid current password')).toBeInTheDocument();
    expect(logout).not.toHaveBeenCalled();
    expect(updateUser).not.toHaveBeenCalled();
    await expectForgotten(client);
    // The message outlives the request's copy, and the form is ready again.
    expect(screen.getByText('Invalid current password')).toBeInTheDocument();
    expect(submit()).toBeEnabled();
    expect(screen.getByLabelText('Current Password')).toHaveValue(OLD);
  });

  it('offers Sign out to someone who does not know the current password', () => {
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(logout).toHaveBeenCalledTimes(1);
    expect(api.changeOwnPassword).not.toHaveBeenCalled();
  });
});
