/**
 * Browser pass 2026-10-01 — the 403s on `/proposals/summary` and
 * `/agent-sessions` were a tab still showing account A's project after
 * another tab signed in as account B: the token is shared (localStorage), the
 * displayed account is not.  The tab now starts again from the stored session.
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiGet, reload } = vi.hoisted(() => ({ apiGet: vi.fn(), reload: vi.fn() }));

vi.mock('../../services/api', () => ({
  default: { get: apiGet, post: vi.fn() },
  setCurrentProjectId: vi.fn(),
}));
// setupTests replaces `useAuth` with a fixed user; this file tests the real one.
vi.mock('../../contexts/AuthContext', async () =>
  vi.importActual<typeof import('../../contexts/AuthContext')>('../../contexts/AuthContext'));
vi.mock('../../utils/authSession', async (original) => ({
  ...(await original<typeof import('../../utils/authSession')>()),
  reloadForAccountChange: reload,
}));

import { AuthProvider, useAuth } from '../../contexts/AuthContext';
import { accountChangedElsewhere } from '../../utils/authSession';

const ADMIN = { id: 1, username: 'admin', role: 'admin' };
const VIEWER = { id: 10, username: 'viewer', role: 'member' };

describe('accountChangedElsewhere', () => {
  const change = (key: string | null, value: unknown) => ({
    key, newValue: value === null ? null : typeof value === 'string' ? value : JSON.stringify(value),
  });

  it('another account signed in, or this one signed out, in another tab', () => {
    expect(accountChangedElsewhere(change('auth_user', VIEWER), 1)).toBe(true);
    expect(accountChangedElsewhere(change('auth_user', null), 1)).toBe(true);
    expect(accountChangedElsewhere(change('auth_token', null), 1)).toBe(true);
    expect(accountChangedElsewhere(change(null, null), 1)).toBe(true);
    expect(accountChangedElsewhere(change('auth_user', '{not json'), 1)).toBe(true);
  });

  it('the same account is left alone: a profile edit, a new token, an unrelated key', () => {
    expect(accountChangedElsewhere(change('auth_user', { ...ADMIN, full_name: 'New Name' }), 1)).toBe(false);
    expect(accountChangedElsewhere(change('auth_token', 'a.new.token'), 1)).toBe(false);
    expect(accountChangedElsewhere(change('current_project_id', '21'), 1)).toBe(false);
    expect(accountChangedElsewhere(change('theme', 'dark'), 1)).toBe(false);
  });

  it('a tab with nobody signed in is never reloaded', () => {
    expect(accountChangedElsewhere(change('auth_user', VIEWER), null)).toBe(false);
    expect(accountChangedElsewhere(change('auth_token', null), undefined)).toBe(false);
  });
});

const Who: React.FC = () => {
  const { user, isLoading } = useAuth();
  return <p>{isLoading ? 'checking' : user ? `signed in as ${user.username}` : 'signed out'}</p>;
};

const storageEvent = (key: string, newValue: string | null) =>
  new StorageEvent('storage', { key, newValue, storageArea: window.localStorage });

describe('AuthProvider, when the stored session changes in another tab', () => {
  beforeEach(() => {
    reload.mockReset();
    apiGet.mockReset();
    localStorage.clear();
    // The provider narrates every state change through the auth logger.
    for (const level of ['log', 'debug', 'info'] as const) vi.spyOn(console, level).mockImplementation(() => {});
  });

  const mountSignedInAsAdmin = async () => {
    localStorage.setItem('auth_token', 'admin-token');
    localStorage.setItem('auth_user', JSON.stringify(ADMIN));
    apiGet.mockResolvedValue({ data: ADMIN });
    render(<MemoryRouter><AuthProvider><Who /></AuthProvider></MemoryRouter>);
    await screen.findByText('signed in as admin');
  };

  it('starts again when another account signs in there', async () => {
    await mountSignedInAsAdmin();
    act(() => { window.dispatchEvent(storageEvent('auth_user', JSON.stringify(VIEWER))); });
    await waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
  });

  it('starts again when that tab signs out', async () => {
    await mountSignedInAsAdmin();
    act(() => { window.dispatchEvent(storageEvent('auth_token', null)); });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('keeps going when the same account is stored again', async () => {
    await mountSignedInAsAdmin();
    act(() => {
      window.dispatchEvent(storageEvent('auth_user', JSON.stringify({ ...ADMIN, full_name: 'Renamed' })));
      window.dispatchEvent(storageEvent('auth_token', 'refreshed-token'));
      window.dispatchEvent(storageEvent('recent_projects', '[21]'));
    });
    expect(reload).not.toHaveBeenCalled();
  });

  it('does nothing on the sign-in page', async () => {
    render(<MemoryRouter><AuthProvider><Who /></AuthProvider></MemoryRouter>);
    await screen.findByText('signed out');
    act(() => { window.dispatchEvent(storageEvent('auth_user', JSON.stringify(VIEWER))); });
    expect(reload).not.toHaveBeenCalled();
  });
});
