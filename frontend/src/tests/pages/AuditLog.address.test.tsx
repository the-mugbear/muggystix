/**
 * Audit log — the page of events and its filters live in the address, under
 * the REAL router (setupTests replaces `useNavigate` / `useLocation` for
 * every other file).
 *
 * The viewer kept the page, the action and the resource type in component
 * state: a reload or a shared link always opened on the first twenty events
 * of everything.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

vi.mock('../../services/api', () => ({
  listAuditLogs: vi.fn(),
  getAuditStats: vi.fn(),
}));

import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import * as api from '../../services/api';
import AuditLog from '../../pages/AuditLog';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const TOTAL = 101;

const row = (id: number) => ({
  id, user_id: 3, user_username: 'ana', user_full_name: 'Ana Ortiz', action: `event_${id}`,
  resource_type: null, resource_id: null, details: null, success: true, error_message: null,
  ip_address: '10.0.0.5', user_agent: null, created_at: '2026-09-22T20:49:37Z',
});

const settle = async (ms = 450) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); };
const open = (entry: string) => {
  const router = createMemoryRouter([{ path: '/audit-log', element: <AuditLog /> }], { initialEntries: [entry] });
  render(<RouterProvider router={router} />);
  return router;
};
const asked = () => mocked.listAuditLogs.mock.calls.map(([params]) => params as Record<string, unknown>);
const last = () => asked()[asked().length - 1];
const box = () => screen.getByLabelText('Resource type') as HTMLInputElement;
const action = () => screen.getByRole('combobox', { name: 'Action' });

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listAuditLogs.mockImplementation(async ({ skip, limit }: { skip: number; limit: number }) => ({
    // One row per page, named by where the page starts.
    logs: skip < TOTAL ? [row(skip + 1)] : [], total: TOTAL, skip, limit,
  }));
  mocked.getAuditStats.mockResolvedValue({
    total_logs: TOTAL, successful_logs: 99, failed_logs: 2, recent_logs_24h: 7,
    top_actions: [{ action: 'login_success', count: 90 }, { action: 'user_updated', count: 4 }], top_users: [],
  });
});

describe('Audit log — page and filters live in the address (real router)', () => {
  it('opens on what the address says: the controls and the request', async () => {
    open('/audit-log?action=login_failed&resource=user&page=3');
    expect(await screen.findByText('41–60 of 101')).toBeInTheDocument();
    expect(box().value).toBe('user');
    // An action that is not among the most frequent ones is still named.
    expect(action()).toHaveTextContent('login_failed');
    expect(asked()).toEqual([{ skip: 40, limit: 20, action: 'login_failed', resource_type: 'user' }]);
  });

  it('a reload on page 3 shows page 3 and writes nothing', async () => {
    const router = open('/audit-log?page=3');
    expect(await screen.findByText('41–60 of 101')).toBeInTheDocument();
    expect(screen.getByText('event_41')).toBeInTheDocument();
    await settle();
    expect(router.state.location.search).toBe('?page=3');
    expect(asked()).toEqual([{ skip: 40, limit: 20 }]);
  });

  it('the pager writes the address (replace) and the list follows', async () => {
    const router = open('/audit-log');
    await screen.findByText('1–20 of 101');
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('21–40 of 101')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?page=2');
    expect(router.state.historyAction).toBe('REPLACE');
    expect(last()).toEqual({ skip: 20, limit: 20 });

    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('1–20 of 101')).toBeInTheDocument();
    expect(router.state.location.search).toBe('');
  });

  it('a typed resource type is written once the typing stops — page dropped, the action kept, one request', async () => {
    const router = open('/audit-log?action=login_success&page=3');
    await screen.findByText('41–60 of 101');
    mocked.listAuditLogs.mockClear();

    fireEvent.change(box(), { target: { value: ' scan ' } });
    expect(mocked.listAuditLogs).not.toHaveBeenCalled();
    await waitFor(() => expect(router.state.location.search).toBe('?action=login_success&resource=scan'));
    expect(router.state.historyAction).toBe('REPLACE');
    await screen.findByText('1–20 of 101');
    expect(asked()).toEqual([{ skip: 0, limit: 20, action: 'login_success', resource_type: 'scan' }]);

    // Emptied, it is left out of the address and of the request.
    fireEvent.change(box(), { target: { value: '' } });
    await waitFor(() => expect(router.state.location.search).toBe('?action=login_success'));
    await waitFor(() => expect(last()).toEqual({ skip: 0, limit: 20, action: 'login_success' }));
  });

  it('choosing an action writes it and drops the page, in one request', async () => {
    const user = userEvent.setup();
    const router = open('/audit-log?resource=user&page=3');
    await screen.findByText('41–60 of 101');
    mocked.listAuditLogs.mockClear();

    await user.click(action());
    await user.click(await screen.findByRole('option', { name: 'user_updated' }));
    await waitFor(() => expect(router.state.location.search).toBe('?resource=user&action=user_updated'));
    expect(router.state.historyAction).toBe('REPLACE');
    await screen.findByText('1–20 of 101');
    expect(asked()).toEqual([{ skip: 0, limit: 20, action: 'user_updated', resource_type: 'user' }]);

    await user.click(action());
    await user.click(await screen.findByRole('option', { name: 'All actions' }));
    await waitFor(() => expect(router.state.location.search).toBe('?resource=user'));
  });

  it('a link to the same page with other filters, and Back, change what is shown; nothing writes the old state back', async () => {
    const router = open('/audit-log?resource=first');
    await screen.findByText('1–20 of 101');
    expect(box().value).toBe('first');

    await act(async () => { await router.navigate('/audit-log?resource=linked&action=user_updated&page=2'); });
    expect(await screen.findByText('21–40 of 101')).toBeInTheDocument();
    expect(box().value).toBe('linked');
    expect(action()).toHaveTextContent('user_updated');
    expect(last()).toEqual({ skip: 20, limit: 20, action: 'user_updated', resource_type: 'linked' });
    await settle();
    expect(router.state.location.search).toBe('?resource=linked&action=user_updated&page=2');
    expect(last()).toEqual({ skip: 20, limit: 20, action: 'user_updated', resource_type: 'linked' });

    await act(async () => { await router.navigate(-1); });
    expect(await screen.findByText('1–20 of 101')).toBeInTheDocument();
    expect(box().value).toBe('first');
    expect(action()).toHaveTextContent('All actions');
    await settle();
    expect(router.state.location.search).toBe('?resource=first');
    expect(last()).toEqual({ skip: 0, limit: 20, resource_type: 'first' });
  });

  it('a page past the end steps back to the last page that exists', async () => {
    const router = open('/audit-log?page=99');
    expect(await screen.findByText('101–101 of 101')).toBeInTheDocument();
    expect(router.state.location.search).toBe('?page=6');
  });
});
