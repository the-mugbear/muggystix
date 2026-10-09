/**
 * Findings — the Owner filter lists the project's people (5.356.0).
 *
 * Reported from the remote deployment (2026-10-09): several people had
 * promoted findings — the promoter owns what they promote — and the filter
 * offered only "Assigned to me" and "Unowned", so nobody could list a
 * teammate's.  The server always took `owner_id`; the page never offered it.
 *
 * Real router: the filter lives in the address (`?owner=<user id>`).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({
  listFindings: vi.fn(),
  setFindingStatus: vi.fn(),
  listProjectMembers: vi.fn(),
  bulkSetFindingStatus: vi.fn(),
  bulkAssignFindings: vi.fn(),
}));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'tester' }, hasPermission: () => true }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import Findings from '../../pages/Findings';
import { TooltipProvider } from '../../components/ui/tooltip';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const LONG = 'Maximiliana-Alexandrina von Hohenzollern-Sigmaringen-Wolfenbüttel the Third of Her Name';
const member = (userId: number, username: string, fullName: string | null) => ({
  id: 100 + userId, project_id: 1, user_id: userId, username, full_name: fullName, role: 'analyst', created_at: '',
});
const ROSTER = [member(1, 'tester', 'Tess Tester'), member(8, 'zed', null), member(7, 'ana', 'Ana Ruiz'), member(9, 'max', LONG)];

const finding = (id: number, ownerId: number | null, ownerName: string | null) => ({
  id, project_id: 1, title: `Finding ${id}`, severity: 'high', status: 'open', source: 'manual',
  owner_id: ownerId, owner_name: ownerName, evidence_annotation_id: null, vuln_id: null, host_count: 1, hosts: [],
  created_at: '2026-08-01T00:00:00Z', updated_at: null,
});

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/findings', element: <TooltipProvider><Findings /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
const chooser = () => screen.getByRole('combobox', { name: 'Owner' });
const lastAsked = () => {
  const [projectId, filters] = mocked.listFindings.mock.calls[mocked.listFindings.mock.calls.length - 1];
  expect(projectId).toBe(1);
  return filters as Record<string, unknown>;
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listFindings.mockResolvedValue({ items: [finding(1, 7, 'Ana Ruiz')], total: 1, severity_counts: {} });
  mocked.listProjectMembers.mockResolvedValue(ROSTER);
});

describe('Findings — the Owner filter', () => {
  it('offers every other member by name, after the three fixed choices, and choosing one lists theirs', async () => {
    const user = userEvent.setup({ skipHover: true });
    const router = open('/findings?status=all&page=3');
    await screen.findByText('Finding 1');
    await user.click(chooser());
    const options = (await screen.findAllByRole('option')).map((o) => o.textContent);
    // The reader is "Assigned to me", not a second entry under their name;
    // the others are in name order (a member with no full name by username).
    expect(options).toEqual(['Any owner', 'Assigned to me', 'Unowned', 'Ana Ruiz', LONG, 'zed']);

    await user.click(screen.getByRole('option', { name: 'Ana Ruiz' }));
    await waitFor(() => expect(lastAsked()).toMatchObject({ owner_id: 7, offset: 0 }));
    expect(lastAsked().unowned).toBeUndefined();
    // In the address, with the page dropped and the other filters kept.
    expect(router.state.location.search).toBe('?status=all&owner=7');
    expect(chooser()).toHaveTextContent('Ana Ruiz');
  });

  it('opens on the owner the address names', async () => {
    open('/findings?owner=7');
    await screen.findByText('Finding 1');
    expect(lastAsked()).toMatchObject({ owner_id: 7 });
    await waitFor(() => expect(chooser()).toHaveTextContent('Ana Ruiz'));
  });

  it('the reader\'s own id in the address is "Assigned to me"', async () => {
    open('/findings?owner=1');
    await screen.findByText('Finding 1');
    expect(lastAsked()).toMatchObject({ owner_id: 1 });
    expect(chooser()).toHaveTextContent('Assigned to me');
  });

  it('an owner who is not on the roster is still named, from the rows — never a blank select', async () => {
    mocked.listProjectMembers.mockResolvedValue([member(1, 'tester', 'Tess Tester')]);
    mocked.listFindings.mockResolvedValue({ items: [finding(1, 42, 'Former Member')], total: 1, severity_counts: {} });
    open('/findings?owner=42');
    await screen.findByText('Finding 1');
    expect(lastAsked()).toMatchObject({ owner_id: 42 });
    await waitFor(() => expect(chooser()).toHaveTextContent('Former Member'));
  });

  it('a roster that could not be read leaves the three fixed choices working', async () => {
    const user = userEvent.setup({ skipHover: true });
    mocked.listProjectMembers.mockRejectedValue(new Error('boom'));
    open('/findings');
    await screen.findByText('Finding 1');
    await user.click(chooser());
    expect((await screen.findAllByRole('option')).map((o) => o.textContent)).toEqual(['Any owner', 'Assigned to me', 'Unowned']);
    await user.click(screen.getByRole('option', { name: 'Unowned' }));
    await waitFor(() => expect(lastAsked()).toMatchObject({ unowned: true }));
  });

  it.each(['bogus', '0', '-3', '7abc'])('a value the address cannot mean (%s) is "Any owner"', async (value) => {
    open(`/findings?owner=${value}`);
    await screen.findByText('Finding 1');
    expect(lastAsked().owner_id).toBeUndefined();
    expect(lastAsked().unowned).toBeUndefined();
    expect(chooser()).toHaveTextContent('Any owner');
  });
});
