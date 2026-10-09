/**
 * Tag rename/delete — the gap this panel closes is that a typo'd tag was
 * permanent. These pin the parts an operator would be hurt by getting wrong.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import TagManagement from '../../components/TagManagement';
import type { HostTagWithCount } from '../../services/api';
import { readsOnScreen } from '../helpers/readsOnScreen';

const listHostTags = vi.fn();
const updateHostTag = vi.fn();
const deleteHostTag = vi.fn();
vi.mock('../../services/api', () => ({
  listHostTags: (...a: unknown[]) => listHostTags(...a),
  updateHostTag: (...a: unknown[]) => updateHostTag(...a),
  deleteHostTag: (...a: unknown[]) => deleteHostTag(...a),
}));

const success = vi.fn();
const error = vi.fn();
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success, error, info: vi.fn(), warning: vi.fn() }),
}));
const project = vi.hoisted(() => ({ my_role: undefined as string | undefined }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Proj', my_role: project.my_role } }),
}));
// A project member, not the global admin setupTests signs in: the controls
// follow the PROJECT role (`useProjectRole`), and a global admin passes it.
const account = vi.hoisted(() => ({ role: 'member' }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'test-user', role: account.role } }),
}));

// Auto-confirm so the destructive path is exercised; the confirm copy itself
// is asserted separately below via the argument captured here.
const confirmSpy = vi.fn().mockResolvedValue(true);
vi.mock('../../hooks/useConfirm', () => ({
  useConfirm: () => [null, (...a: unknown[]) => confirmSpy(...a)],
}));

const tag = (over: Partial<HostTagWithCount> = {}): HostTagWithCount => ({
  id: 1, name: 'prod', color: null, host_count: 3, ...over,
} as HostTagWithCount);

beforeEach(() => {
  vi.clearAllMocks();
  confirmSpy.mockResolvedValue(true);
  listHostTags.mockResolvedValue([tag()]);
  updateHostTag.mockResolvedValue(tag({ name: 'production' }));
  deleteHostTag.mockResolvedValue(undefined);
});

describe('TagManagement', () => {
  // Branch review 2026-10-01 S5 — Project settings is every member's page
  // now; renaming and deleting a tag stay the project analyst's.
  it.each(['viewer', 'auditor'])('shows a project %s the tags without Rename or Delete', async (role) => {
    project.my_role = role;
    try {
      render(<TagManagement />);
      expect(await screen.findByText('prod')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /rename prod/i })).toBeNull();
      expect(screen.queryByRole('button', { name: /delete prod/i })).toBeNull();
      expect(screen.queryByText(/Rename or delete them here/)).toBeNull();
    } finally {
      project.my_role = undefined;
    }
  });

  it('shows an analyst the controls', async () => {
    project.my_role = 'analyst';
    try {
      render(<TagManagement />);
      expect(await screen.findByRole('button', { name: /rename prod/i })).toBeInTheDocument();
    } finally {
      project.my_role = undefined;
    }
  });

  it('shows a global admin the controls whatever the project says', async () => {
    project.my_role = 'viewer';
    account.role = 'admin';
    try {
      render(<TagManagement />);
      expect(await screen.findByRole('button', { name: /rename prod/i })).toBeInTheDocument();
    } finally {
      project.my_role = undefined;
      account.role = 'member';
    }
  });

  // A host that is open (the inspector) names its tags: a rename or a delete
  // here left the old name on it until it was reopened.
  it.each([
    ['renaming', async () => {
      fireEvent.click(await screen.findByRole('button', { name: /rename prod/i }));
      fireEvent.change(screen.getByLabelText(/rename tag prod/i), { target: { value: 'production' } });
      fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    }],
    ['deleting', async () => {
      fireEvent.click(await screen.findByRole('button', { name: /delete prod/i }));
    }],
  ])('%s a tag re-reads an open host, the host rows and the tag lists', async (_what, act) => {
    const { reread, ReadsOnScreen } = readsOnScreen({
      getHost: 'open host', getHosts: 'host rows', getHostFilterData: 'filters',
    });
    render(<><ReadsOnScreen /><TagManagement /></>);
    await act();
    await waitFor(() => expect(reread).toHaveBeenCalledWith('open host'));
    expect(reread).toHaveBeenCalledWith('host rows');
    expect(reread).toHaveBeenCalledWith('filters');
    await waitFor(() => expect(listHostTags).toHaveBeenCalledTimes(2));
  });

  it('a refused rename re-reads nothing', async () => {
    updateHostTag.mockRejectedValue(new Error('conflict'));
    const { reread, ReadsOnScreen } = readsOnScreen({ getHost: 'open host' });
    render(<><ReadsOnScreen /><TagManagement /></>);
    fireEvent.click(await screen.findByRole('button', { name: /rename prod/i }));
    fireEvent.change(screen.getByLabelText(/rename tag prod/i), { target: { value: 'staging' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(reread).not.toHaveBeenCalled();
  });

  it('lists tags with their host counts', async () => {
    render(<TagManagement />);
    expect(await screen.findByText('prod')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('renames a tag', async () => {
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /rename prod/i }));
    const input = screen.getByLabelText(/rename tag prod/i);
    fireEvent.change(input, { target: { value: 'production' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() =>
      expect(updateHostTag).toHaveBeenCalledWith(1, { name: 'production' }),
    );
  });

  it('does not call the API when the name is unchanged', async () => {
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /rename prod/i }));
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(updateHostTag).not.toHaveBeenCalled());
  });

  it('surfaces a name collision instead of silently dropping the edit', async () => {
    // The backend answers 409 when another tag owns the name. Swallowing that
    // would leave the operator believing the rename worked.
    updateHostTag.mockRejectedValue(new Error('conflict'));
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /rename prod/i }));
    fireEvent.change(screen.getByLabelText(/rename tag prod/i), {
      target: { value: 'staging' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(error).toHaveBeenCalled());
  });

  it('warns how many hosts a delete will affect before doing it', async () => {
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /delete prod/i }));
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    const body = String(confirmSpy.mock.calls[0][0].body);
    expect(body).toMatch(/3 hosts/);
    await waitFor(() => expect(deleteHostTag).toHaveBeenCalledWith(1));
  });

  it('does not delete when the operator cancels', async () => {
    confirmSpy.mockResolvedValue(false);
    render(<TagManagement />);
    fireEvent.click(await screen.findByRole('button', { name: /delete prod/i }));
    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    expect(deleteHostTag).not.toHaveBeenCalled();
  });

  it('a failed load reads as an error, not as "no tags"', async () => {
    listHostTags.mockRejectedValue(new Error('boom'));
    render(<TagManagement />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText(/No tags yet/i)).not.toBeInTheDocument();
  });

  it('says there are no tags left-aligned, like every other section (v5.288.0)', async () => {
    listHostTags.mockResolvedValue([]);
    render(<TagManagement />);
    const empty = await screen.findByText(/No tags yet/i);
    expect(empty.className).not.toMatch(/text-center/);
  });
});
