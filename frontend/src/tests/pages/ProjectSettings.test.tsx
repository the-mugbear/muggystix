/**
 * Project settings (5.265.0) — one project (the current one), sections not
 * cards, and only what the caller's role allows.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
const updateProjectMock = vi.hoisted(() => vi.fn());
// The barrel's typed wrappers, each over the same raw mock so the URL
// assertions below still say which endpoint was called.
vi.mock('../../services/api', () => ({
  updateProject: updateProjectMock,
  getProjectMembers: (pid: number) => apiMock.get(`/projects/${pid}/members`).then((r: { data: unknown }) => r.data),
  getUserDirectory: () => apiMock.get('/users/directory').then((r: { data: unknown }) => r.data),
  addProjectMember: (pid: number, uid: number, role: string) =>
    apiMock.post(`/projects/${pid}/members`, { user_id: uid, role }).then((r: { data: unknown }) => r.data),
  updateProjectMemberRole: (pid: number, uid: number, role: string) =>
    apiMock.put(`/projects/${pid}/members/${uid}`, { role }).then((r: { data: unknown }) => r.data),
  removeProjectMember: (pid: number, uid: number) => apiMock.delete(`/projects/${pid}/members/${uid}`),
  deleteProject: (pid: number) => apiMock.delete(`/projects/${pid}`),
}));
vi.mock('../../components/TagManagement', () => ({ default: () => null }));
vi.mock('../../components/WebhookSettings', () => ({ default: () => null }));
vi.mock('../../components/WebhookDeliveries', () => ({ default: () => null }));
// Tested on its own (ProjectIngestSettings.test.tsx); its switch thumb is a
// `.bg-card.shadow-raised` the no-card check below would catch.
vi.mock('../../components/scans/ProjectIngestSettings', () => ({
  default: ({ canEdit }: { canEdit: boolean }) => <div data-testid="imports-section" data-can-edit={String(canEdit)} />,
}));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
let myRole = 'admin';
const refreshProjects = vi.fn();
// A NEW object on every call, on purpose: the page must not loop or reset its
// form when a refresh hands back an equal project.
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProject: {
      id: 3, name: 'Demo — Insights Eval', description: null, status: 'active',
      start_date: null, end_date: null, my_role: myRole,
    },
    projects: [{ id: 3 }, { id: 4 }],
    refreshProjects,
  }),
}));
let globalRole = 'member';
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1, role: globalRole } }) }));

import ProjectSettings from '../../pages/ProjectSettings';

const members = [
  { id: 1, user_id: 1, username: 'ana', full_name: 'Ana', role: 'admin', created_at: '2026-09-01T00:00:00Z' },
  { id: 2, user_id: 2, username: 'ben', full_name: null, role: 'analyst', created_at: '2026-09-01T00:00:00Z' },
];

const renderPage = () => render(<MemoryRouter><ProjectSettings /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  myRole = 'admin';
  globalRole = 'member';
  apiMock.get.mockResolvedValue({ data: members });
  apiMock.put.mockResolvedValue({ data: {} });
});

describe('Project settings', () => {
  it('is about the current project only, in sections', async () => {
    const { container } = renderPage();
    expect(await screen.findByText('Ana')).toBeInTheDocument();
    expect(screen.getByText(/For/)).toHaveTextContent('For Demo — Insights Eval — the project chosen at the top of the page.');
    expect(apiMock.get).toHaveBeenCalledWith('/projects/3/members');
    expect(container.querySelector('.bg-card.shadow-raised')).toBeNull();
    // No project list here any more; a non-global admin gets no delete area.
    expect(screen.queryByText('All projects')).not.toBeInTheDocument();
    expect(screen.queryByText('Delete this project')).not.toBeInTheDocument();
    // UX review 2026-09-24 — the upload dialog's project setting lives here.
    expect(screen.getByTestId('imports-section')).toHaveAttribute('data-can-edit', 'true');
  });

  it('saves the engagement dates', async () => {
    updateProjectMock.mockResolvedValue({});
    renderPage();
    await screen.findByText('Ana');
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2026-09-01' } });
    fireEvent.change(screen.getByLabelText('End date'), { target: { value: '2026-09-19' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save details' }));
    await waitFor(() => expect(updateProjectMock).toHaveBeenCalledWith(3, expect.objectContaining({
      start_date: new Date('2026-09-01').toISOString(), end_date: new Date('2026-09-19').toISOString(),
    })));
  });

  it('asks before you change your own role', async () => {
    confirmMock.mockResolvedValue(false);
    renderPage();
    await screen.findByText('Ana');
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Role of Ana' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('option', { name: 'Viewer' }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'Change your own role?' })));
    expect(apiMock.put).not.toHaveBeenCalled();
  });

  it('counts the name\'s characters near the API limit (v5.290.0)', async () => {
    const { container } = renderPage();
    await screen.findByText('Ana');
    const name = screen.getByLabelText('Name');
    expect(name).toHaveAttribute('maxLength', '100');
    expect(screen.queryByText(/\/100/)).toBeNull();
    fireEvent.change(name, { target: { value: 'x'.repeat(93) } });
    expect(screen.getByText('93/100')).toBeInTheDocument();
    expect(name).toHaveAttribute('aria-describedby', 'ps-name-count');
    fireEvent.change(name, { target: { value: 'x'.repeat(100) } });
    expect(screen.getByText('100/100 — the limit')).toBeInTheDocument();
    // Full width like the other hub pages: no centred max-width container.
    expect(container.firstElementChild?.className).not.toMatch(/mx-auto|max-w-6xl/);
  });

  it('is read-only for an analyst', async () => {
    myRole = 'analyst';
    renderPage();
    await screen.findByText('Ana');
    expect(screen.getByLabelText('Name')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save details' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add member/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Role of Ana' })).not.toBeInTheDocument();
    expect(screen.getByText(/Only a project admin can change these settings/)).toBeInTheDocument();
  });

  describe('for a global administrator (v5.288.0)', () => {
    beforeEach(() => { globalRole = 'admin'; });

    it('has no "All projects" button — the Settings tab is the way there', async () => {
      renderPage();
      await screen.findByText('Ana');
      expect(screen.queryByRole('link', { name: 'All projects' })).toBeNull();
      expect(screen.queryByText('All projects')).not.toBeInTheDocument();
    });

    it('offers delete as an outline destructive button gated on typing the project name', async () => {
      confirmMock.mockResolvedValue(false);
      renderPage();
      await screen.findByText('Ana');
      const del = screen.getByRole('button', { name: /Delete Demo — Insights Eval/ });
      expect(del.className.split(/\s+/)).not.toContain('bg-destructive');
      expect(del.className).toMatch(/text-destructive/);
      expect(del.className).toMatch(/border-destructive/);
      fireEvent.click(del);
      await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
        confirmTypedName: true, resourceName: 'Demo — Insights Eval',
      })));
      expect(apiMock.delete).not.toHaveBeenCalled();
    });
  });
});
