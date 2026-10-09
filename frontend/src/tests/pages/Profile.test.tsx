import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock('../../services/api', () => ({ default: apiMock }));

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
    apiMock.get.mockImplementation((url: string) =>
      Promise.resolve({
        data: url === '/users/profile/projects'
          ? [{
            project_id: 5, project_name: 'Acme', project_slug: 'acme', project_status: 'active',
            project_is_default: false, project_is_archived: false, role: 'analyst', joined_at: null,
          }]
          : [],
      }),
    );
    apiMock.put.mockResolvedValue({ data: {} });
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
    expect(apiMock.put).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: 'Ana M. Ortiz' } });
    fireEvent.click(save);
    await waitFor(() =>
      expect(apiMock.put).toHaveBeenCalledWith('/users/profile', { full_name: 'Ana M. Ortiz' }),
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
    apiMock.get.mockImplementation((url: string) =>
      Promise.resolve({ data: url === '/auth/sessions' ? sessions : [] }),
    );
    renderPage();

    expect(await screen.findByText('This session')).toBeInTheDocument();
    expect(screen.getAllByText('This session')).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Revoke this session and sign out' }));
    expect(await screen.findByText(/You will be signed out/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    expect(apiMock.delete).not.toHaveBeenCalled();
  });

  // 1.15 — `POST /auth/change-password` revokes EVERY session of the user,
  // this browser's included (auth.py).  The dialog said "You'll stay signed
  // in" and left the page on a dead token: the next request was a 401.
  describe('changing the password', () => {
    const fillAndSubmit = async () => {
      renderPage();
      fireEvent.click(screen.getByRole('button', { name: /Change Password/ }));
      const dialog = await screen.findByRole('dialog');
      fireEvent.change(screen.getByLabelText('Current Password'), { target: { value: 'old-Passw0rd!' } });
      fireEvent.change(screen.getByLabelText('New Password'), { target: { value: 'new-Passw0rd!42' } });
      fireEvent.change(screen.getByLabelText('Confirm New Password'), { target: { value: 'new-Passw0rd!42' } });
      fireEvent.submit(dialog.querySelector('form')!);
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
      apiMock.post.mockResolvedValue({ data: {} });
      await fillAndSubmit();
      await waitFor(() => expect(apiMock.post).toHaveBeenCalledWith('/auth/change-password', {
        current_password: 'old-Passw0rd!', new_password: 'new-Passw0rd!42',
      }));
      await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
      expect(toast.success).toHaveBeenCalledWith('Password changed. Sign in again with the new password.');
    });

    it('stays signed in, with the reason shown, when the change is refused', async () => {
      apiMock.post.mockRejectedValue({ response: { status: 400, data: { detail: 'Invalid current password' } } });
      await fillAndSubmit();
      expect(await screen.findByText('Invalid current password')).toBeInTheDocument();
      expect(logout).not.toHaveBeenCalled();
    });
  });

  it('switches project in place from a project association', async () => {
    renderPage();
    const button = await screen.findByRole('button', { name: 'Switch to Acme' });
    fireEvent.click(button);
    expect(selectProject).toHaveBeenCalledWith({ id: 5, name: 'Acme' });
  });
});
