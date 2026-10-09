/**
 * Portfolio's members sheet follows the same member rules as Project settings
 * (defect 1.4, `utils/projectMembers`): it used to demote the only project
 * admin, and let the reader demote themselves, without a word, and asked
 * "Remove member?" for a removal the server refuses.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  listProjectMembers: vi.fn(),
  getUserDirectory: vi.fn(),
  addProjectMember: vi.fn(),
  updateProjectMemberRole: vi.fn(),
  removeProjectMember: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('sonner', () => ({ toast }));
const confirmMock = vi.hoisted(() => vi.fn());
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 2, role: 'member' } }) }));

import * as api from '../../services/api';
import ProjectMembersSheet from '../../components/ProjectMembersSheet';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const member = (over: Record<string, unknown>) => ({
  id: 1, project_id: 7, user_id: 1, username: 'ana', full_name: 'Ana Ortiz', role: 'admin',
  created_at: '2026-09-01T00:00:00Z', ...over,
});
const ANA = member({});
const BEN = member({ id: 2, user_id: 2, username: 'ben', full_name: null, role: 'admin' });
const CY = member({ id: 3, user_id: 3, username: 'cy', full_name: null, role: 'analyst' });

const renderSheet = (canManage = true) => render(
  <ProjectMembersSheet projectId={7} projectName="Acme" canManage={canManage} open onOpenChange={() => {}} />,
);

const chooseRole = async (who: string, role: string) => {
  fireEvent.keyDown(await screen.findByRole('combobox', { name: `Role for ${who}` }), { key: 'Enter' });
  fireEvent.click(await screen.findByRole('option', { name: role }));
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listProjectMembers.mockResolvedValue([ANA, CY]);
  mocked.getUserDirectory.mockResolvedValue([]);
  mocked.updateProjectMemberRole.mockResolvedValue({});
  mocked.removeProjectMember.mockResolvedValue({});
});

describe('ProjectMembersSheet — the member rules of Project settings', () => {
  it('asks before the only project admin is demoted, and sends nothing on "no"', async () => {
    confirmMock.mockResolvedValue(false);
    renderSheet();
    await chooseRole('Ana Ortiz', 'Viewer');
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith({
      title: 'Ana Ortiz is the only project admin',
      body: 'Making them a Viewer leaves Acme with no project admin; only a global administrator could then manage its members.',
      severity: 'danger',
      confirmLabel: 'Make them Viewer',
    }));
    expect(mocked.updateProjectMemberRole).not.toHaveBeenCalled();
  });

  it('sends the demotion once it is confirmed', async () => {
    confirmMock.mockResolvedValue(true);
    renderSheet();
    await chooseRole('Ana Ortiz', 'Viewer');
    await waitFor(() => expect(mocked.updateProjectMemberRole).toHaveBeenCalledWith(7, 1, 'viewer'));
  });

  it('asks before the reader changes their own role', async () => {
    mocked.listProjectMembers.mockResolvedValue([ANA, BEN]);   // the reader is ben (user 2)
    confirmMock.mockResolvedValue(false);
    renderSheet();
    await chooseRole('ben', 'Analyst');
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Change your own role?', confirmLabel: 'Make me Analyst' }),
    ));
    expect(mocked.updateProjectMemberRole).not.toHaveBeenCalled();
  });

  it('changes an ordinary role without asking', async () => {
    renderSheet();
    await chooseRole('cy', 'Viewer');
    await waitFor(() => expect(mocked.updateProjectMemberRole).toHaveBeenCalledWith(7, 3, 'viewer'));
    expect(confirmMock).not.toHaveBeenCalled();
  });

  it('says why the only project admin cannot be removed, and sends nothing', async () => {
    confirmMock.mockResolvedValue(true);
    renderSheet();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove Ana Ortiz' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Ana Ortiz is the only project admin of Acme and cannot be removed. Make another member a project admin first.',
    ));
    expect(confirmMock).not.toHaveBeenCalled();
    expect(mocked.removeProjectMember).not.toHaveBeenCalled();
  });

  it('asks before a removal in the shared words', async () => {
    confirmMock.mockResolvedValue(true);
    renderSheet();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove cy' }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith({
      title: 'Remove cy?',
      body: 'cy loses access to Acme. Their notes, findings and reviews stay. They can be added again later.',
      severity: 'danger',
      confirmLabel: 'Remove',
    }));
    await waitFor(() => expect(mocked.removeProjectMember).toHaveBeenCalledWith(7, 3));
  });

  it('offers the one role list, highest first', async () => {
    renderSheet();
    fireEvent.keyDown(await screen.findByRole('combobox', { name: 'Role for cy' }), { key: 'Enter' });
    const options = await screen.findAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual(['Admin', 'Analyst', 'Auditor', 'Viewer']);
  });

  it('shows a reader who cannot manage the roles as text, with no controls', async () => {
    renderSheet(false);
    expect(await screen.findByText('Ana Ortiz')).toBeInTheDocument();
    expect(screen.getByText('Admin')).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: /Remove/ })).toBeNull();
    expect(mocked.getUserDirectory).not.toHaveBeenCalled();
  });
});
