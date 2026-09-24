/**
 * /settings/projects (v5.290.0): creating a project makes it the active one
 * and opens its Scope page; the name field says how close it is to the limit.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// The real navigate: setupTests replaces useNavigate with a no-op.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));

const createProjectMock = vi.hoisted(() => vi.fn());
vi.mock('../../services/api', () => ({ createProject: createProjectMock }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
const adoptProject = vi.fn();
const selectProject = vi.fn();
const current = { id: 3, name: 'Old engagement', description: null, status: 'active', start_date: null, end_date: null, member_count: 2 };
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: [current],
    currentProject: current,
    selectProject,
    adoptProject,
    refreshProjects: vi.fn(),
    isLoading: false,
  }),
}));

import AllProjects from '../../pages/AllProjects';

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/settings/projects']}>
      <Routes>
        <Route path="/settings/projects" element={<AllProjects />} />
        <Route path="/scopes" element={<p>Scope page</p>} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => vi.clearAllMocks());

describe('All projects', () => {
  it('switches to a project it just created and opens its Scope page, saying so', async () => {
    const created = { ...current, id: 9, name: 'Acme Q4', member_count: 1, my_role: 'admin' };
    createProjectMock.mockResolvedValue(created);
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /New project/ }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: '  Acme Q4 ' } });
    fireEvent.click(screen.getByRole('button', { name: /Create project/ }));

    expect(await screen.findByText('Scope page')).toBeInTheDocument();
    expect(createProjectMock).toHaveBeenCalledWith('Acme Q4', undefined);
    expect(adoptProject).toHaveBeenCalledWith(created);
    expect(toastMock.success).toHaveBeenCalledWith(expect.stringMatching(/^Created Acme Q4 and switched to it/));
  });

  it('stays put and says why when creation fails', async () => {
    createProjectMock.mockRejectedValue(new Error('A project with this name already exists'));
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /New project/ }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Old engagement' } });
    fireEvent.click(screen.getByRole('button', { name: /Create project/ }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(adoptProject).not.toHaveBeenCalled();
    expect(screen.queryByText('Scope page')).toBeNull();
  });

  it('counts the name\'s characters once it nears the 100-character limit', () => {
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /New project/ }));
    const name = screen.getByLabelText('Name');
    expect(name).toHaveAttribute('maxLength', '100');
    fireEvent.change(name, { target: { value: 'x'.repeat(80) } });
    expect(screen.queryByText('80/100')).toBeNull();
    fireEvent.change(name, { target: { value: 'x'.repeat(81) } });
    expect(screen.getByText('81/100')).toBeInTheDocument();
  });

  it('uses the full content width like the other hub pages', () => {
    const { container } = renderPage();
    expect(container.firstElementChild?.className).not.toMatch(/mx-auto|max-w-6xl/);
  });
});
