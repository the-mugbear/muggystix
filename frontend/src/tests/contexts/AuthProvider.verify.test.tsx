/**
 * The stored session is verified once at start.  Only the server saying "not
 * signed in" (401) ends it: a 502 / 503 or a network error during a deploy
 * used to clear the session and sign everyone out.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
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
});
