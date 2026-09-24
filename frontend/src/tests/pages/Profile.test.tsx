import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock('../../services/api', () => ({ default: apiMock }));

const updateUser = vi.fn();
const user = {
  id: 3,
  username: 'eval-ana',
  full_name: 'Ana Ortiz',
  role: 'member',
  created_at: '2026-09-01T10:00:00Z',
  last_login: '2026-09-22T20:49:37Z',
};
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user, updateUser }),
}));
const selectProject = vi.fn();
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ selectProject, projects: [{ id: 5, name: 'Acme' }], currentProject: null }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));
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

  it('switches project in place from a project association', async () => {
    renderPage();
    const button = await screen.findByRole('button', { name: 'Switch to Acme' });
    fireEvent.click(button);
    expect(selectProject).toHaveBeenCalledWith({ id: 5, name: 'Acme' });
  });
});
