/**
 * Findings — the Owner filter lists the people who own the listed findings,
 * each with how many (5.356.0, 5.357.0).
 *
 * Reported from the remote deployment (2026-10-09): several people had
 * promoted findings — the promoter owns what they promote — and the filter
 * offered only "Assigned to me" and "Unowned", so nobody could list a
 * teammate's.  5.356.0 offered every project MEMBER, which on a real project
 * is mostly people who own nothing; since 5.357.0 the options are the
 * server's `owner_counts` (every filter but the owner), so each number is the
 * size of the list its choice opens.
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
// The roster has someone who owns nothing (8, "idle"): never offered.
const ROSTER = [member(1, 'tester', 'Tess Tester'), member(8, 'idle', 'Idle Member'), member(7, 'ana', 'Ana Ruiz')];
// As the server sends them: named owners by name, unowned last.
const OWNER_COUNTS = [
  { owner_id: 7, owner_name: 'Ana Ruiz', count: 3 },
  { owner_id: 9, owner_name: LONG, count: 1 },
  { owner_id: 1, owner_name: 'Tess Tester', count: 2 },
  { owner_id: 6, owner_name: 'zed', count: 1204 },
  { owner_id: null, owner_name: null, count: 5 },
];

const finding = (id: number, ownerId: number | null, ownerName: string | null) => ({
  id, project_id: 1, title: `Finding ${id}`, severity: 'high', status: 'open', source: 'manual',
  owner_id: ownerId, owner_name: ownerName, evidence_annotation_id: null, vuln_id: null, host_count: 1, hosts: [],
  created_at: '2026-08-01T00:00:00Z', updated_at: null,
});
const page = (over: Record<string, unknown> = {}) => ({
  items: [finding(1, 7, 'Ana Ruiz')], total: 1, severity_counts: {}, owner_counts: OWNER_COUNTS, ...over,
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
const options = async () => (await screen.findAllByRole('option')).map((o) => o.textContent);
const lastAsked = () => {
  const [projectId, filters] = mocked.listFindings.mock.calls[mocked.listFindings.mock.calls.length - 1];
  expect(projectId).toBe(1);
  return filters as Record<string, unknown>;
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listFindings.mockResolvedValue(page());
  mocked.listProjectMembers.mockResolvedValue(ROSTER);
});

describe('Findings — the Owner filter', () => {
  it('offers the people who own a listed finding, with how many, and choosing one lists theirs', async () => {
    const user = userEvent.setup({ skipHover: true });
    const router = open('/findings?status=all&page=3');
    await screen.findByText('Finding 1');
    await user.click(chooser());
    // The reader's own count is on "Assigned to me", not a second entry under
    // their name; the member who owns nothing is not offered; an owner who is
    // not on the roster (9, 6) is — the options are the server's answer.
    expect(await options()).toEqual([
      'Any owner', 'Assigned to me (2)', 'Unowned (5)', 'Ana Ruiz (3)', `${LONG} (1)`, 'zed (1,204)',
    ]);

    await user.click(screen.getByRole('option', { name: 'Ana Ruiz (3)' }));
    await waitFor(() => expect(lastAsked()).toMatchObject({ owner_id: 7, offset: 0 }));
    expect(lastAsked().unowned).toBeUndefined();
    // In the address, with the page dropped and the other filters kept.
    expect(router.state.location.search).toBe('?status=all&owner=7');
    expect(chooser()).toHaveTextContent('Ana Ruiz (3)');
  });

  it('opens on the owner the address names', async () => {
    open('/findings?owner=7');
    await screen.findByText('Finding 1');
    expect(lastAsked()).toMatchObject({ owner_id: 7 });
    await waitFor(() => expect(chooser()).toHaveTextContent('Ana Ruiz (3)'));
  });

  it('the reader\'s own id in the address is "Assigned to me"', async () => {
    open('/findings?owner=1');
    await screen.findByText('Finding 1');
    expect(lastAsked()).toMatchObject({ owner_id: 1 });
    expect(chooser()).toHaveTextContent('Assigned to me (2)');
  });

  it('an owner the address names who owns none of what is listed stays chosen, by name, at 0', async () => {
    // Another filter left this member nothing: the select is not blank.
    mocked.listFindings.mockResolvedValue(page({ items: [], total: 0 }));
    open('/findings?owner=8&search=tls');
    await waitFor(() => expect(chooser()).toHaveTextContent('Idle Member (0)'));
    expect(lastAsked()).toMatchObject({ owner_id: 8, search: 'tls' });
  });

  it('…and one who is not on the roster either is still named', async () => {
    mocked.listFindings.mockResolvedValue(page({ items: [], total: 0 }));
    open('/findings?owner=42');
    await waitFor(() => expect(chooser()).toHaveTextContent('User 42 (0)'));
    expect(lastAsked()).toMatchObject({ owner_id: 42 });
  });

  it('a server that sends no owner counts leaves the three fixed choices, without numbers', async () => {
    const user = userEvent.setup({ skipHover: true });
    mocked.listFindings.mockResolvedValue({ items: [finding(1, 7, 'Ana Ruiz')], total: 1, severity_counts: {} });
    mocked.listProjectMembers.mockRejectedValue(new Error('boom'));
    open('/findings');
    await screen.findByText('Finding 1');
    await user.click(chooser());
    expect(await options()).toEqual(['Any owner', 'Assigned to me', 'Unowned']);
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
