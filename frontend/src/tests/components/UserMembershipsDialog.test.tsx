/**
 * The administrator's memberships dialog follows the same member rules as
 * Project settings (defect 1.4, `utils/projectMembers`).  The server treats a
 * global administrator like a project admin on the member routes: it refuses
 * to remove a project's only admin and does NOT refuse to demote them — so
 * the dialog reads the project's roster before either, says why a removal
 * cannot be done, and asks before a demotion.  It used to do neither.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  getUserMemberships: vi.fn(),
  getProjects: vi.fn(),
  listProjectMembers: vi.fn(),
  addProjectMember: vi.fn(),
  updateProjectMemberRole: vi.fn(),
  removeProjectMember: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
const confirmMock = vi.hoisted(() => vi.fn());
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
const me = vi.hoisted(() => ({ id: 1 }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: me.id, role: 'admin' } }) }));

import * as api from '../../services/api';
import UserMembershipsDialog from '../../components/UserMembershipsDialog';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const membership = (over: Record<string, unknown> = {}) => ({
  project_id: 7, project_name: 'Acme', project_slug: 'acme', project_status: 'active',
  project_is_default: false, project_is_archived: false, role: 'admin', joined_at: '2026-09-01T00:00:00Z',
  ...over,
});
const rosterRow = (user_id: number, role: string) => ({
  id: user_id, project_id: 7, user_id, username: `u${user_id}`, full_name: null, role, created_at: '2026-09-01T00:00:00Z',
});
const TARGET = { id: 5, username: 'ben', full_name: 'Ben Okoro', role: 'member' };

const renderDialog = (user = TARGET) => render(<UserMembershipsDialog user={user} onClose={() => {}} />);

const rowOf = async (project: string) => (await screen.findByText(project)).closest('tr') as HTMLElement;
const chooseRole = async (project: string, role: string) => {
  fireEvent.keyDown(within(await rowOf(project)).getByRole('combobox'), { key: 'Enter' });
  fireEvent.click(await screen.findByRole('option', { name: role }));
};

beforeEach(() => {
  vi.clearAllMocks();
  me.id = 1;
  mocked.getUserMemberships.mockResolvedValue([membership(), membership({ project_id: 8, project_name: 'Borealis', role: 'analyst' })]);
  mocked.getProjects.mockResolvedValue([{ id: 7, name: 'Acme' }, { id: 8, name: 'Borealis' }]);
  // Acme's roster: ben (5) is its only project admin.
  mocked.listProjectMembers.mockResolvedValue([rosterRow(5, 'admin'), rosterRow(6, 'viewer')]);
  mocked.updateProjectMemberRole.mockResolvedValue({});
  mocked.removeProjectMember.mockResolvedValue({});
});

describe('UserMembershipsDialog — the member rules of Project settings', () => {
  it('reads the memberships of the user it was opened on', async () => {
    renderDialog();
    await rowOf('Acme');
    expect(mocked.getUserMemberships).toHaveBeenCalledTimes(1);
    expect(mocked.getUserMemberships.mock.calls[0][0]).toBe(5);
  });

  it('asks before a project\'s only admin is demoted, and sends nothing on "no"', async () => {
    confirmMock.mockResolvedValue(false);
    renderDialog();
    await chooseRole('Acme', 'Viewer');
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith({
      title: 'Ben Okoro is the only project admin',
      body: 'Making them a Viewer leaves Acme with no project admin; only a global administrator could then manage its members.',
      severity: 'danger',
      confirmLabel: 'Make them Viewer',
    }));
    expect(mocked.listProjectMembers).toHaveBeenCalledWith(7);
    expect(mocked.updateProjectMemberRole).not.toHaveBeenCalled();
  });

  it('sends the demotion once it is confirmed', async () => {
    confirmMock.mockResolvedValue(true);
    renderDialog();
    await chooseRole('Acme', 'Viewer');
    await waitFor(() => expect(mocked.updateProjectMemberRole).toHaveBeenCalledWith(7, 5, 'viewer'));
  });

  it('demotes an admin who is not the only one without asking', async () => {
    mocked.listProjectMembers.mockResolvedValue([rosterRow(5, 'admin'), rosterRow(6, 'admin')]);
    renderDialog();
    await chooseRole('Acme', 'Viewer');
    await waitFor(() => expect(mocked.updateProjectMemberRole).toHaveBeenCalledWith(7, 5, 'viewer'));
    expect(confirmMock).not.toHaveBeenCalled();
  });

  it('changes a role that is not admin without reading the roster or asking', async () => {
    renderDialog();
    await chooseRole('Borealis', 'Viewer');
    await waitFor(() => expect(mocked.updateProjectMemberRole).toHaveBeenCalledWith(8, 5, 'viewer'));
    expect(mocked.listProjectMembers).not.toHaveBeenCalled();
    expect(confirmMock).not.toHaveBeenCalled();
  });

  it('says why a project\'s only admin cannot be removed, and sends nothing', async () => {
    confirmMock.mockResolvedValue(true);
    renderDialog();
    await rowOf('Acme');
    fireEvent.click(screen.getByRole('button', { name: 'Remove from Acme' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      'Ben Okoro is the only project admin of Acme and cannot be removed. Make another member a project admin first.',
    ));
    expect(confirmMock).not.toHaveBeenCalled();
    expect(mocked.removeProjectMember).not.toHaveBeenCalled();
  });

  it('asks before a removal in the shared words', async () => {
    confirmMock.mockResolvedValue(true);
    renderDialog();
    await rowOf('Borealis');
    fireEvent.click(screen.getByRole('button', { name: 'Remove from Borealis' }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith({
      title: 'Remove Ben Okoro?',
      body: 'Ben Okoro loses access to Borealis. Their notes, findings and reviews stay. They can be added again later.',
      severity: 'danger',
      confirmLabel: 'Remove',
    }));
    await waitFor(() => expect(mocked.removeProjectMember).toHaveBeenCalledWith(8, 5));
  });

  it('asks an administrator before they change their own role', async () => {
    me.id = 5;
    confirmMock.mockResolvedValue(false);
    renderDialog();
    await chooseRole('Borealis', 'Viewer');
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Change your own role?', confirmLabel: 'Make me Viewer' }),
    ));
    expect(mocked.updateProjectMemberRole).not.toHaveBeenCalled();
  });

  it('sends nothing when the project\'s admins cannot be read', async () => {
    mocked.listProjectMembers.mockRejectedValue(new Error('boom'));
    renderDialog();
    await chooseRole('Acme', 'Viewer');
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/boom|Could not check the admins of Acme/)));
    expect(confirmMock).not.toHaveBeenCalled();
    expect(mocked.updateProjectMemberRole).not.toHaveBeenCalled();
  });
});
