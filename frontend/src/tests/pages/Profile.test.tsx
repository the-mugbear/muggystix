import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { createQueryClient } from '../../lib/query';
import { heldByMutations, withClient } from '../helpers/heldByMutations';

const apiMock = vi.hoisted(() => ({
  listOwnSessions: vi.fn(),
  getOwnProjectMemberships: vi.fn(),
  updateOwnProfile: vi.fn(),
  changeOwnPassword: vi.fn(),
  revokeOwnSession: vi.fn(),
}));
vi.mock('../../services/api', () => apiMock);
const updateUser = vi.fn();
const logout = vi.fn();
const user = {
  id: 3,
  username: 'eval-ana',
  full_name: 'Ana Ortiz',
  role: 'member',
  created_at: '2026-09-01T10:00:00Z',
  last_login: '2026-09-22T20:49:37Z',
};
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user, updateUser, logout }),
}));
const selectProject = vi.fn();
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ selectProject, projects: [{ id: 5, name: 'Acme' }], currentProject: null }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
// 2FA has its own flow and tests; keep it out of this page's.
vi.mock('../../components/TwoFactorCard', () => ({ default: () => null }));

import Profile from '../../pages/Profile';

describe('Profile', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.getOwnProjectMemberships.mockResolvedValue([{
      project_id: 5, project_name: 'Acme', project_slug: 'acme', project_status: 'active',
      project_is_default: false, project_is_archived: false, role: 'analyst', joined_at: null,
    }]);
    apiMock.listOwnSessions.mockResolvedValue([]);
    apiMock.updateOwnProfile.mockResolvedValue(undefined);
  });

  const renderPage = () => render(<MemoryRouter><Profile /></MemoryRouter>);

  it('enables Save only when the full name differs from the saved one', async () => {
    renderPage();
    const save = screen.getByRole('button', { name: /Save Changes/ });
    expect(save).toBeDisabled();

    const input = screen.getByLabelText('Full Name');
    fireEvent.change(input, { target: { value: 'Ana M. Ortiz' } });
    expect(save).toBeEnabled();

    // Back to the saved value (whitespace aside) → nothing to save.
    fireEvent.change(input, { target: { value: ' Ana Ortiz ' } });
    expect(save).toBeDisabled();
    fireEvent.submit(input.closest('form')!);
    expect(apiMock.updateOwnProfile).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: 'Ana M. Ortiz' } });
    fireEvent.click(save);
    await waitFor(() =>
      expect(apiMock.updateOwnProfile).toHaveBeenCalledWith({ full_name: 'Ana M. Ortiz' }),
    );
  });

  it('heads the page with the full name’s initials, name and role', () => {
    renderPage();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Ana Ortiz');
    expect(screen.getByText('AO')).toBeInTheDocument();
    expect(screen.getByText('MEMBER')).toBeInTheDocument();
  });

  it('shows the username as text, not as an editable-looking field', () => {
    renderPage();
    expect(screen.getByTestId('profile-username')).toHaveTextContent('eval-ana');
    // The only text input on the form is Full Name.
    const inputs = screen.getAllByRole('textbox');
    expect(inputs).toHaveLength(1);
    expect(inputs[0]).toHaveAccessibleName('Full Name');
  });

  it('marks the current session and signs out (not a bare revoke) when it is revoked', async () => {
    const sessions = [
      { id: 11, ip_address: '10.0.0.5', user_agent: 'Firefox', created_at: '2026-09-22T10:00:00Z',
        last_activity: '2026-09-22T11:00:00Z', expires_at: '2026-09-23T10:00:00Z', current: false },
      { id: 12, ip_address: '10.0.0.9', user_agent: 'Chrome', created_at: '2026-09-22T10:00:00Z',
        last_activity: '2026-09-22T11:00:00Z', expires_at: '2026-09-23T10:00:00Z', current: true },
    ];
    apiMock.listOwnSessions.mockResolvedValue(sessions);
    apiMock.getOwnProjectMemberships.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('This session')).toBeInTheDocument();
    expect(screen.getAllByText('This session')).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Revoke this session and sign out' }));
    expect(await screen.findByText(/You will be signed out/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    expect(apiMock.revokeOwnSession).not.toHaveBeenCalled();
  });

  it('revokes another session by its id and takes it off the list', async () => {
    apiMock.listOwnSessions.mockResolvedValue([
      { id: 11, ip_address: '10.0.0.5', user_agent: 'Firefox', created_at: '2026-09-22T10:00:00Z',
        last_activity: '2026-09-22T11:00:00Z', expires_at: '2026-09-23T10:00:00Z', current: false },
      { id: 12, ip_address: '10.0.0.9', user_agent: 'Chrome', created_at: '2026-09-22T10:00:00Z',
        last_activity: '2026-09-22T11:00:00Z', expires_at: '2026-09-23T10:00:00Z', current: true },
    ]);
    apiMock.revokeOwnSession.mockResolvedValue(undefined);
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'Revoke session from 10.0.0.5' }));
    expect(await screen.findByText('This will sign out the session on 10.0.0.5.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }));

    await waitFor(() => expect(apiMock.revokeOwnSession).toHaveBeenCalledWith(11));
    // Removed from what is shown (the cache under the read's own key), not re-read.
    await waitFor(() => expect(screen.queryByText('10.0.0.5')).toBeNull());
    expect(screen.getByText('10.0.0.9')).toBeInTheDocument();
    expect(apiMock.listOwnSessions).toHaveBeenCalledTimes(1);
    expect(logout).not.toHaveBeenCalled();
  });

  // 1.15 — `POST /auth/change-password` revokes EVERY session of the user,
  // this browser's included (auth.py).  The dialog said "You'll stay signed
  // in" and left the page on a dead token: the next request was a 401.
  describe('changing the password', () => {
    const fill = async () => {
      fireEvent.click(screen.getByRole('button', { name: /Change Password/ }));
      const dialog = await screen.findByRole('dialog');
      fireEvent.change(screen.getByLabelText('Current Password'), { target: { value: 'old-Passw0rd!' } });
      fireEvent.change(screen.getByLabelText('New Password'), { target: { value: 'new-Passw0rd!42' } });
      fireEvent.change(screen.getByLabelText('Confirm New Password'), { target: { value: 'new-Passw0rd!42' } });
      return dialog;
    };
    const fillAndSubmit = async () => {
      renderPage();
      fireEvent.submit((await fill()).querySelector('form')!);
    };
    // The same, with the client in hand: what it still holds can be looked at.
    const fillAndSubmitWatched = async () => {
      const client = createQueryClient();
      render(<MemoryRouter><Profile /></MemoryRouter>, { wrapper: withClient(client) });
      fireEvent.submit((await fill()).querySelector('form')!);
      return client;
    };
    /** Neither password is held by a mutation any more. */
    const expectForgotten = async (client: ReturnType<typeof createQueryClient>) => {
      await waitFor(() => {
        const held = heldByMutations(client);
        expect(held).not.toContain('old-Passw0rd!');
        expect(held).not.toContain('new-Passw0rd!42');
      });
    };
    const expectEmptyFields = () => {
      expect(screen.getByLabelText('Current Password')).toHaveValue('');
      expect(screen.getByLabelText('New Password')).toHaveValue('');
      expect(screen.getByLabelText('Confirm New Password')).toHaveValue('');
    };

    it('says beforehand that it signs the reader out, and never that they stay signed in', async () => {
      renderPage();
      fireEvent.click(screen.getByRole('button', { name: /Change Password/ }));
      const dialog = await screen.findByRole('dialog');
      expect(dialog).toHaveTextContent(
        'Changing it signs you out everywhere, this browser included, and ends your agent sessions; sign in again with the new password.',
      );
      expect(dialog.textContent).not.toMatch(/stay signed in/);
    });

    it('signs this browser out after the change, and says why', async () => {
      apiMock.changeOwnPassword.mockResolvedValue(undefined);
      await fillAndSubmit();
      await waitFor(() => expect(apiMock.changeOwnPassword).toHaveBeenCalledWith({
        current_password: 'old-Passw0rd!', new_password: 'new-Passw0rd!42',
      }));
      await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
      expect(toast.success).toHaveBeenCalledWith('Password changed. Sign in again with the new password.');
    });

    it('stays signed in, with the reason shown, when the change is refused', async () => {
      apiMock.changeOwnPassword.mockRejectedValue({ response: { status: 400, data: { detail: 'Invalid current password' } } });
      await fillAndSubmit();
      expect(await screen.findByText('Invalid current password')).toBeInTheDocument();
      expect(logout).not.toHaveBeenCalled();
    });

    // The request carries both passwords, and the page stays mounted after
    // it: the library must hold neither once it has settled
    // (`SECRET_MUTATION` + `reset()`, lib/query).
    it('keeps neither password once the change has gone through, in the client or in the form', async () => {
      apiMock.changeOwnPassword.mockResolvedValue(undefined);
      const client = await fillAndSubmitWatched();
      await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
      await expectForgotten(client);

      // (Sign-out is mocked here, so the page is still up to look at.)
      fireEvent.click(screen.getByRole('button', { name: /Change Password/ }));
      await screen.findByRole('dialog');
      expectEmptyFields();
    });

    it('keeps neither password in the client after a refusal, and still says why', async () => {
      apiMock.changeOwnPassword.mockRejectedValue({ response: { status: 400, data: { detail: 'Invalid current password' } } });
      const client = await fillAndSubmitWatched();
      expect(await screen.findByText('Invalid current password')).toBeInTheDocument();
      await expectForgotten(client);
      expect(screen.getByText('Invalid current password')).toBeInTheDocument();
      // What was typed stays in the form, to correct.
      expect(screen.getByLabelText('Current Password')).toHaveValue('old-Passw0rd!');
    });

    it('empties the form when the dialog is left without changing anything', async () => {
      renderPage();
      await fill();
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(apiMock.changeOwnPassword).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: /Change Password/ }));
      await screen.findByRole('dialog');
      expectEmptyFields();
    });
  });

  it('switches project in place from a project association', async () => {
    renderPage();
    const button = await screen.findByRole('button', { name: 'Switch to Acme' });
    fireEvent.click(button);
    expect(selectProject).toHaveBeenCalledWith({ id: 5, name: 'Acme' });
  });
});
