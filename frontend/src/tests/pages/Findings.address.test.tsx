/**
 * The Findings list's search box and the address — under the REAL router
 * (setupTests replaces `useNavigate` / `useLocation` for every other file).
 *
 * The box kept its own copy of `?search=`, seeded once, and wrote it back to
 * the address from a timer: opening a link to page 3 lost the page 300 ms
 * later, and Back — the address changes, the copy does not — had the newer
 * text written over the address the reader had returned to.  The box is now
 * `useUrlSearchDraft('search')`: the address owns the search, the box holds
 * only what is being typed.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

const finding = (id: number) => ({
  id, project_id: 1, title: `Finding ${id}`, severity: 'high', status: 'open', source: 'manual',
  owner_id: null, owner_name: null, evidence_annotation_id: null, vuln_id: null, host_count: 1, hosts: [],
  created_at: '2026-08-01T00:00:00Z', updated_at: null,
});
const RESPONSE = { items: [finding(1), finding(2)], total: 400, severity_counts: {} };

const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const open = async (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/findings', element: <TooltipProvider><Findings /></TooltipProvider> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  await pass(20);
  return router;
};
const box = () => screen.getByRole('searchbox', { name: 'Search findings' }) as HTMLInputElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
/** The filters of each list read — every one asked of the project on screen. */
const asked = () => mocked.listFindings.mock.calls.map(([projectId, filters]) => {
  expect(projectId).toBe(1);
  return filters as Record<string, unknown>;
});
const last = () => asked()[asked().length - 1];

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  mocked.listFindings.mockImplementation(async () => RESPONSE);
  mocked.listProjectMembers.mockResolvedValue([]);
});
afterEach(() => { vi.useRealTimers(); });

describe('Findings — the search box follows the address (real router)', () => {
  it('opens on what the address says', async () => {
    await open('/findings?search=tls&status=all');
    expect(box().value).toBe('tls');
    expect(last()).toMatchObject({ search: 'tls' });
    expect(asked()).toHaveLength(1);
  });

  it('opening a link to a later page keeps the page: the box writes nothing it was not asked to', async () => {
    const router = await open('/findings?search=tls&status=all&page=3');
    expect(last()).toMatchObject({ search: 'tls', offset: 100 });
    await pass(1000);
    expect(router.state.location.search).toBe('?search=tls&status=all&page=3');
    expect(asked()).toHaveLength(1);
  });

  it('typing asks once after the typing stops — trimmed, replacing the entry, from the first page, the other filters kept', async () => {
    const router = await open('/findings?status=all&page=3');
    expect(last()).toMatchObject({ offset: 100 });
    mocked.listFindings.mockClear();

    type('  weak');
    await pass(200);
    type('  weak tls ');
    await pass(200);
    // Still typing: nothing is asked for, nothing is written.
    expect(asked()).toHaveLength(0);
    expect(router.state.location.search).toBe('?status=all&page=3');

    await pass(150);
    expect(router.state.location.search).toBe('?status=all&search=weak+tls');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(asked()).toHaveLength(1);
    expect(last()).toMatchObject({ search: 'weak tls', offset: 0 });
    expect(last()).not.toHaveProperty('status');

    // Emptied, the search is left out of the address and of the request.
    type('');
    await pass(350);
    expect(router.state.location.search).toBe('?status=all');
    expect(last()).not.toHaveProperty('search');
  });

  it('a link to the same page with another search, and Back, re-seed the box; the list follows and the address is left alone', async () => {
    const router = await open('/findings?search=first');
    expect(box().value).toBe('first');

    await act(async () => { await router.navigate('/findings?search=linked&page=2'); });
    await pass(20);
    expect(box().value).toBe('linked');
    expect(last()).toMatchObject({ search: 'linked', offset: 50 });
    // Nothing puts the previous search back over it, now or once a timer fires.
    await pass(1000);
    expect(router.state.location.search).toBe('?search=linked&page=2');
    expect(last()).toMatchObject({ search: 'linked', offset: 50 });

    await act(async () => { await router.navigate(-1); });
    await pass(20);
    expect(box().value).toBe('first');
    expect(last()).toMatchObject({ search: 'first', offset: 0 });
    await pass(1000);
    expect(router.state.location.search).toBe('?search=first');
    expect(last()).toMatchObject({ search: 'first', offset: 0 });
  });

  it('Back while something half-typed is pending drops the half-typed text, not the address', async () => {
    const router = await open('/findings?search=first');
    await act(async () => { await router.navigate('/findings?search=linked'); });
    await pass(20);
    type('linked and mo');
    await pass(100);
    await act(async () => { await router.navigate(-1); });
    await pass(1000);
    expect(box().value).toBe('first');
    expect(router.state.location.search).toBe('?search=first');
    expect(asked().some((f) => f.search === 'linked and mo')).toBe(false);
  });
});
