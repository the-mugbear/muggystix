/**
 * Renewing the session stores the token that carries the later end — the one
 * every tab's requests are then sent with — and never puts a session back
 * that ended, or was replaced, while the request was away.
 */
import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiGet, apiPost } = vi.hoisted(() => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock('../../services/api', () => ({
  default: { get: apiGet, post: apiPost },
  setCurrentProjectId: vi.fn(),
}));
// setupTests replaces `useAuth` with a fixed user; this file tests the real one.
vi.mock('../../contexts/AuthContext', async () =>
  vi.importActual<typeof import('../../contexts/AuthContext')>('../../contexts/AuthContext'));

import { AuthProvider, useAuth } from '../../contexts/AuthContext';

const ADMIN = { id: 1, username: 'admin', role: 'admin' };

let renew: () => Promise<void>;
const Probe: React.FC = () => {
  const { token, renewSession, authStatus } = useAuth();
  renew = renewSession;
  return <p>{authStatus === 'checking' ? 'checking' : `token: ${token ?? 'none'}`}</p>;
};

const mount = async () => {
  render(<MemoryRouter><AuthProvider><Probe /></AuthProvider></MemoryRouter>);
  expect(await screen.findByText('token: first-token')).toBeInTheDocument();
};

/** A request whose answer the test gives when it chooses. */
const pending = () => {
  let answer!: (value: unknown) => void;
  let fail!: (reason: unknown) => void;
  const promise = new Promise((resolve, reject) => { answer = resolve; fail = reject; });
  return { promise, answer, fail };
};

beforeEach(() => {
  apiGet.mockReset().mockResolvedValue({ data: ADMIN });
  apiPost.mockReset();
  localStorage.clear();
  localStorage.setItem('auth_token', 'first-token');
  localStorage.setItem('auth_user', JSON.stringify(ADMIN));
  for (const level of ['log', 'debug', 'info', 'error', 'warn'] as const) vi.spyOn(console, level).mockImplementation(() => {});
});

describe('AuthProvider — renewing the session', () => {
  it('stores the renewed token and shows it', async () => {
    apiPost.mockResolvedValue({ data: { access_token: 'renewed-token', token_type: 'bearer', expires_in: 28800 } });
    await mount();

    await act(async () => { await renew(); });

    expect(apiPost).toHaveBeenCalledWith('/auth/session/renew');
    expect(localStorage.getItem('auth_token')).toBe('renewed-token');
    expect(screen.getByText('token: renewed-token')).toBeInTheDocument();
    // Whose session it is has not changed.
    expect(JSON.parse(localStorage.getItem('auth_user') as string)).toMatchObject({ id: 1 });
  });

  it('calls made while one is away share that one request', async () => {
    const request = pending();
    apiPost.mockReturnValue(request.promise);
    await mount();

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => { first = renew(); second = renew(); });
    await act(async () => { await Promise.resolve(); });
    expect(apiPost).toHaveBeenCalledTimes(1);

    await act(async () => {
      request.answer({ data: { access_token: 'renewed-token' } });
      await Promise.all([first, second]);
    });
    expect(localStorage.getItem('auth_token')).toBe('renewed-token');

    // A later call is a new request.
    apiPost.mockResolvedValue({ data: { access_token: 'renewed-again' } });
    await act(async () => { await renew(); });
    expect(apiPost).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem('auth_token')).toBe('renewed-again');
  });

  it.each([
    ['signed out', null],
    ['replaced by another tab', 'another-tabs-token'],
  ])('does not put its answer back over a session %s while it was away', async (_what, storedMeanwhile) => {
    const request = pending();
    apiPost.mockReturnValue(request.promise);
    await mount();

    let call!: Promise<void>;
    act(() => { call = renew(); });
    if (storedMeanwhile === null) localStorage.removeItem('auth_token');
    else localStorage.setItem('auth_token', storedMeanwhile);
    await act(async () => {
      request.answer({ data: { access_token: 'renewed-token' } });
      await call;
    });

    expect(localStorage.getItem('auth_token')).toBe(storedMeanwhile);
  });

  it('a renewal that fails leaves the session as it was and can be asked for again', async () => {
    apiPost.mockRejectedValueOnce(Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' }));
    await mount();

    await act(async () => { await expect(renew()).rejects.toThrow('Network Error'); });
    expect(localStorage.getItem('auth_token')).toBe('first-token');
    expect(screen.getByText('token: first-token')).toBeInTheDocument();

    apiPost.mockResolvedValue({ data: { access_token: 'renewed-token' } });
    await act(async () => { await renew(); });
    expect(localStorage.getItem('auth_token')).toBe('renewed-token');
  });
});
