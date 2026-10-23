/**
 * A notification opens in ITS project (review 2026-10-23, H-FE1).  The bell
 * lists every project's notifications, and their links name no project: one
 * from project B opened in A ("Finding not found", or A's list) and was marked
 * read.  Now opening one switches to its project first, says so when the
 * reader can no longer open that project (and keeps it unread), and a row
 * from another project names it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

const ALPHA = { id: 1, name: 'Alpha', slug: 'alpha' };
const BETA = { id: 2, name: 'Beta', slug: 'beta' };
const selectProject = vi.fn();
vi.mock('../../contexts/ProjectContext', async () => {
  const actual = await vi.importActual<typeof import('../../contexts/ProjectContext')>('../../contexts/ProjectContext');
  return {
    ...actual,
    useProject: () => ({ projects: [ALPHA, BETA], currentProject: ALPHA, selectProject }),
  };
});

const warning = vi.fn();
vi.mock('../../contexts/ToastContext', async () => {
  const actual = await vi.importActual<typeof import('../../contexts/ToastContext')>('../../contexts/ToastContext');
  return {
    ...actual,
    useToast: () => ({ success: vi.fn(), error: vi.fn(), warning, info: vi.fn(), dismiss: vi.fn() }),
  };
});

const getNotifications = vi.fn();
const markNotificationsRead = vi.fn();
vi.mock('../../services/api', () => ({
  getNotifications: (...a: unknown[]) => getNotifications(...a),
  markNotificationsRead: (...a: unknown[]) => markNotificationsRead(...a),
  markAllNotificationsRead: vi.fn().mockResolvedValue(0),
}));

import { BellPanel, NOT_A_MEMBER } from '../../components/UnreadNotifications';

const note = (over: Record<string, unknown>) => ({
  id: 7, type: 'assignment', title: 'A finding was assigned to you', body: null, project_id: 1,
  source_type: 'finding', source_id: 9, host_id: null, finding_id: 9, actor_id: 2,
  actor_username: 'ana', read_at: null, created_at: '2026-10-01T00:00:00Z', ...over,
});

const show = async (n: Record<string, unknown>) => {
  getNotifications.mockResolvedValue({ notifications: [n], total_unread: 1 });
  render(<MemoryRouter><BellPanel onClose={vi.fn()} /></MemoryRouter>);
  return screen.findByRole('button', { name: new RegExp(String(n.title)) });
};

beforeEach(() => {
  navigate.mockReset(); selectProject.mockReset(); warning.mockReset();
  getNotifications.mockReset(); markNotificationsRead.mockReset().mockResolvedValue(1);
});

describe('opening a notification of another project', () => {
  it('switches to its project BEFORE navigating, and names the project on the row', async () => {
    const row = await show(note({ project_id: 2 }));
    expect(row).toHaveTextContent('In Beta');
    fireEvent.click(row);
    expect(selectProject).toHaveBeenCalledWith(BETA);
    expect(navigate).toHaveBeenCalledWith('/findings/9');
    expect(selectProject.mock.invocationCallOrder[0]).toBeLessThan(navigate.mock.invocationCallOrder[0]);
    await waitFor(() => expect(markNotificationsRead).toHaveBeenCalledWith([7]));
  });

  it('one of the selected project opens without a switch and names no project', async () => {
    const row = await show(note({ project_id: 1 }));
    expect(row).not.toHaveTextContent('In Alpha');
    fireEvent.click(row);
    expect(selectProject).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith('/findings/9');
  });

  it('a project the reader can no longer open is said; nothing opens and it stays unread', async () => {
    const row = await show(note({ project_id: 3 }));
    expect(row).toHaveTextContent('In another project');
    fireEvent.click(row);
    expect(warning).toHaveBeenCalledWith(NOT_A_MEMBER);
    expect(selectProject).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    expect(markNotificationsRead).not.toHaveBeenCalled();
  });

  it('a remediation alert opens the cross-project deadlines list, whoever is a member', async () => {
    const row = await show(note({
      type: 'remediation', title: '2 findings overdue', project_id: 3,
      source_type: 'remediation_overdue', source_id: 3, finding_id: null,
    }));
    fireEvent.click(row);
    expect(selectProject).not.toHaveBeenCalled();
    expect(warning).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith('/remediation-deadlines?state=overdue&project=3');
  });

  it("the installation's own alerts (no project) open where they are shown", async () => {
    const row = await show(note({
      type: 'system', title: 'Disk space is low', project_id: null,
      source_type: 'operational:disk', source_id: null, finding_id: null,
    }));
    fireEvent.click(row);
    expect(selectProject).not.toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith('/system-settings');
  });
});
