/**
 * The stored session is verified once at start.  Only the server saying "not
 * signed in" (401) ends it: a 502 / 503 or a network error during a deploy
 * used to clear the session and sign everyone out.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiGet, setCurrentProjectId } = vi.hoisted(() => ({ apiGet: vi.fn(), setCurrentProjectId: vi.fn() }));
vi.mock('../../services/api', () => ({
  default: { get: apiGet, post: vi.fn() },
  setCurrentProjectId,
}));
// setupTests replaces `useAuth` with a fixed user; this file tests the real one.
vi.mock('../../contexts/AuthContext', async () =>
  vi.importActual<typeof import('../../contexts/AuthContext')>('../../contexts/AuthContext'));

import { AuthProvider, useAuth } from '../../contexts/AuthContext';
import { createQueryClient } from '../../lib/query';

const ADMIN = { id: 1, username: 'admin', role: 'admin' };

const Who: React.FC = () => {
  const { user, authStatus } = useAuth();
  return <p>{authStatus === 'checking' ? 'checking' : user ? `signed in as ${user.username}` : 'signed out'}</p>;
};

const mount = () => render(<MemoryRouter><AuthProvider><Who /></AuthProvider></MemoryRouter>);

beforeEach(() => {
  apiGet.mockReset();
  setCurrentProjectId.mockReset();
  localStorage.clear();
  localStorage.setItem('auth_token', 'admin-token');
  localStorage.setItem('auth_user', JSON.stringify(ADMIN));
  for (const level of ['log', 'debug', 'info', 'error', 'warn'] as const) vi.spyOn(console, level).mockImplementation(() => {});
});

describe('AuthProvider — verifying the stored session', () => {
  it.each([
    ['a 503 while the backend restarts', { response: { status: 503 } }],
    ['a 502 from the proxy', { response: { status: 502 } }],
    ['a network error', Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' })],
  ])('keeps the session through %s', async (_what, failure) => {
    apiGet.mockRejectedValue(failure);
    mount();
    expect(await screen.findByText('signed in as admin')).toBeInTheDocument();
    expect(localStorage.getItem('auth_token')).toBe('admin-token');
    expect(JSON.parse(localStorage.getItem('auth_user') as string)).toMatchObject({ id: 1 });
    // The project the reader was in is kept with the session.
    expect(setCurrentProjectId).not.toHaveBeenCalled();
  });

  it('ends the session when the server says the token is not valid (401)', async () => {
    apiGet.mockRejectedValue({ response: { status: 401 } });
    mount();
    expect(await screen.findByText('signed out')).toBeInTheDocument();
    expect(localStorage.getItem('auth_token')).toBeNull();
    expect(localStorage.getItem('auth_user')).toBeNull();
    expect(setCurrentProjectId).toHaveBeenCalledWith(null);
  });

  // 5.355.0 — the account role answers ONE question.  It used to rank role
  // names, and every member passed 'analyst' / 'auditor' / 'viewer'.
  it.each([
    ['admin', 'global admin'],
    ['member', 'not a global admin'],
    ['analyst', 'not a global admin'],   // a stale token's old global role is not an administrator
  ])('hasPermission: an account whose role is %s is %s', async (role, shown) => {
    localStorage.setItem('auth_user', JSON.stringify({ ...ADMIN, role }));
    apiGet.mockRejectedValue({ response: { status: 503 } });   // the stored session is kept
    const Role: React.FC = () => {
      const { user, hasPermission } = useAuth();
      return <p>{user ? (hasPermission('admin') ? 'global admin' : 'not a global admin') : 'nobody'}</p>;
    };
    render(<MemoryRouter><AuthProvider><Role /></AuthProvider></MemoryRouter>);
    expect(await screen.findByText(shown)).toBeInTheDocument();
  });

  // 5.353.0 — a key names the project, not the user: what one user read
  // (remembered answers included) is dropped when the signed-in user changes.
  it('drops what the user read from the query cache when the session ends — and keeps it while the session is kept', async () => {
    const remembered = { staleTime: Infinity, gcTime: Infinity };
    const withRead = async () => {
      const client = createQueryClient();
      await client.fetchQuery({ queryKey: ['getRemediationPolicy'], queryFn: async () => 'read by admin', ...remembered });
      return client;
    };
    const mountWith = (client: ReturnType<typeof createQueryClient>) => render(
      <MemoryRouter><AuthProvider><Who /></AuthProvider></MemoryRouter>,
      { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> },
    );

    apiGet.mockRejectedValue({ response: { status: 503 } });
    const kept = await withRead();
    const first = mountWith(kept);
    expect(await screen.findByText('signed in as admin')).toBeInTheDocument();
    expect(kept.getQueryData(['getRemediationPolicy'])).toBe('read by admin');
    first.unmount();

    apiGet.mockRejectedValue({ response: { status: 401 } });
    const ended = await withRead();
    mountWith(ended);
    expect(await screen.findByText('signed out')).toBeInTheDocument();
    expect(ended.getQueryData(['getRemediationPolicy'])).toBeUndefined();
  });
});
