/**
 * Names — the state chips and the search are the address's (UI_STYLE_GUIDE
 * §39), under the REAL router.
 *
 * The defect this pins (code review 2026-10-09, finding 6): the page copied
 * `?state=` and `?search=` into state once and wrote the state back from an
 * effect.  A link to /names with other filters, or Back, changed the address
 * while the chips and the request kept the old filter — and the effect then
 * put the old filter back over the address.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// setupTests replaces useNavigate / useLocation for every file; this one
// needs the router's own.
vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({
  listNames: vi.fn(),
  getNamesSummary: vi.fn(),
  getName: vi.fn(),
  importNames: vi.fn(),
  deleteName: vi.fn(),
  exportNames: vi.fn(),
}));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'tester', role: 'member' }, hasPermission: () => true }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: 'analyst' } }),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import Names from '../../pages/Names';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const row = {
  id: 1, fqdn: 'portal.acme.com', kind: 'fqdn', in_scope: true, first_seen: null, last_seen: null,
  current_addresses: [], previous_address_count: 0, evidence: {}, imported: true, resolved: false,
};

const open = (entry: string) => {
  const router = createMemoryRouter(
    [{ path: '/names', element: <Names /> }, { path: '/elsewhere', element: <p>elsewhere</p> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
};
/** What each list read asked for — every one of the project on screen. */
const asked = () => mocked.listNames.mock.calls.map(([projectId, q]) => {
  expect(projectId).toBe(1);
  return q as { state: string; search: string; skip: number };
});
const last = () => asked()[asked().length - 1];
const chip = (label: RegExp) => screen.getByRole('button', { name: label });
const pressed = () => screen.getAllByRole('button', { pressed: true }).map((b) => b.textContent);
const box = () => screen.getByLabelText('Search names') as HTMLInputElement;
/** Longer than the search box's delay: anything that was going to be written has been. */
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });

describe('Names — the filters are the address (real router)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocked.listNames.mockResolvedValue({ items: [row], total: 250, skip: 0, limit: 100 });
    mocked.getNamesSummary.mockResolvedValue({ total: 250, unresolved: 9, resolved: 0, in_scope: 1, wildcards: 0 });
  });

  it('opens on what the address says: the chip, the box and the request', async () => {
    const router = open('/names?state=unresolved&search=portal');
    await screen.findByText('portal.acme.com');
    expect(chip(/^Unresolved/)).toHaveAttribute('aria-pressed', 'true');
    expect(pressed()).toHaveLength(1);
    expect(box().value).toBe('portal');
    expect(asked()).toEqual([{ skip: 0, limit: 100, search: 'portal', state: 'unresolved' }]);
    // Opening it changed nothing in the address, then or later.
    await settle();
    expect(router.state.location.search).toBe('?state=unresolved&search=portal');
    expect(asked()).toHaveLength(1);
  });

  it('a bare address is every name, and an emptied filter leaves the address', async () => {
    const router = open('/names');
    await screen.findByText('portal.acme.com');
    expect(chip(/^All/)).toHaveAttribute('aria-pressed', 'true');
    expect(box().value).toBe('');
    expect(last()).toMatchObject({ state: 'all', search: '' });

    fireEvent.click(chip(/^In scope/));
    await waitFor(() => expect(last()).toMatchObject({ state: 'in_scope' }));
    expect(router.state.location.search).toBe('?state=in_scope');
    fireEvent.click(chip(/^All/));
    await waitFor(() => expect(last()).toMatchObject({ state: 'all' }));
    expect(router.state.location.search).toBe('');
  });

  it('choosing a chip writes the address — replaced, the page dropped, the search kept — and asks with it', async () => {
    const router = open('/names?search=portal&page=2');
    await screen.findByText('portal.acme.com');
    expect(last()).toMatchObject({ state: 'all', search: 'portal', skip: 100 });
    mocked.listNames.mockClear();

    fireEvent.click(chip(/^Wildcard/));
    await waitFor(() => expect(asked()).toHaveLength(1));
    expect(last()).toMatchObject({ state: 'wildcard', search: 'portal', skip: 0 });
    expect(chip(/^Wildcard/)).toHaveAttribute('aria-pressed', 'true');
    expect(router.state.location.search).toBe('?search=portal&state=wildcard');
    expect(router.state.historyAction).toBe('REPLACE');
    await settle();
    expect(asked()).toHaveLength(1);
  });

  it('a link to the page with other filters, and Back, re-seed the controls and the list; nothing writes the old filters back', async () => {
    const router = open('/names?state=unresolved&search=first');
    await screen.findByText('portal.acme.com');

    await act(async () => { await router.navigate('/names?state=wildcard&search=second'); });
    await waitFor(() => expect(last()).toMatchObject({ state: 'wildcard', search: 'second', skip: 0 }));
    expect(chip(/^Wildcard/)).toHaveAttribute('aria-pressed', 'true');
    expect(pressed()).toHaveLength(1);
    expect(box().value).toBe('second');
    await settle();
    expect(router.state.location.search).toBe('?state=wildcard&search=second');
    expect(last()).toMatchObject({ state: 'wildcard', search: 'second' });

    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(last()).toMatchObject({ state: 'unresolved', search: 'first' }));
    expect(chip(/^Unresolved/)).toHaveAttribute('aria-pressed', 'true');
    expect(pressed()).toHaveLength(1);
    expect(box().value).toBe('first');
    await settle();
    expect(router.state.location.search).toBe('?state=unresolved&search=first');
    expect(last()).toMatchObject({ state: 'unresolved', search: 'first' });
  });

  it('a link that drops the filters is the unfiltered list', async () => {
    const router = open('/names?state=unresolved&search=first');
    await screen.findByText('portal.acme.com');
    await act(async () => { await router.navigate('/names'); });
    await waitFor(() => expect(last()).toMatchObject({ state: 'all', search: '' }));
    expect(chip(/^All/)).toHaveAttribute('aria-pressed', 'true');
    expect(box().value).toBe('');
    await settle();
    expect(router.state.location.search).toBe('');
  });

  it('typing asks once, after the typing stops, and the search goes into the address', async () => {
    const router = open('/names?state=in_scope&page=2');
    await screen.findByText('portal.acme.com');
    mocked.listNames.mockClear();

    fireEvent.change(box(), { target: { value: 'po' } });
    fireEvent.change(box(), { target: { value: 'port' } });
    fireEvent.change(box(), { target: { value: 'portal' } });
    // Still typing: nothing asked, nothing written.
    expect(asked()).toHaveLength(0);
    expect(router.state.location.search).toBe('?state=in_scope&page=2');

    await settle();
    expect(asked()).toEqual([{ skip: 0, limit: 100, search: 'portal', state: 'in_scope' }]);
    expect(router.state.location.search).toBe('?state=in_scope&search=portal');
    expect(box().value).toBe('portal');
  });
});
