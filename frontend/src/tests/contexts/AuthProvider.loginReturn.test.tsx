/**
 * An expired session reloads the app at `/login?from=<where the reader was>`;
 * signing in again goes back there — and never to a target outside the app.
 */
import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { apiPost, navigate, where } = vi.hoisted(() => ({
  apiPost: vi.fn(),
  navigate: vi.fn(),
  where: { search: '', state: null as unknown },
}));
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual<typeof import('react-router-dom')>('react-router-dom')),
  useNavigate: () => navigate,
  useLocation: () => ({ pathname: '/login', search: where.search, hash: '', state: where.state }),
}));
vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: apiPost },
  setCurrentProjectId: vi.fn(),
}));
vi.mock('../../contexts/AuthContext', async () =>
  vi.importActual<typeof import('../../contexts/AuthContext')>('../../contexts/AuthContext'));

import { AuthProvider, useAuth } from '../../contexts/AuthContext';

const SignIn: React.FC = () => {
  const { login } = useAuth();
  return <button type="button" onClick={() => void login('ana', 'pw')}>sign in</button>;
};

const signInFrom = async (search: string, state: unknown = null) => {
  where.search = search;
  where.state = state;
  render(<AuthProvider><SignIn /></AuthProvider>);
  await act(async () => { screen.getByRole('button', { name: 'sign in' }).click(); });
};

beforeEach(() => {
  navigate.mockReset();
  localStorage.clear();
  apiPost.mockReset().mockResolvedValue({ data: { access_token: 't', user: { id: 1, username: 'ana', role: 'member' } } });
  for (const level of ['log', 'debug', 'info'] as const) vi.spyOn(console, level).mockImplementation(() => {});
});

describe('AuthProvider — where sign-in goes', () => {
  it('back to the page the expired session left, with its query', async () => {
    await signInFrom(`?from=${encodeURIComponent('/findings/7?edit=report-text')}`);
    expect(navigate).toHaveBeenCalledWith('/findings/7?edit=report-text', { replace: true });
  });

  it('to the page the route guard handed over', async () => {
    await signInFrom('', { from: { pathname: '/hosts', search: '?page=3' } });
    expect(navigate).toHaveBeenCalledWith('/hosts?page=3', { replace: true });
  });

  it.each([
    encodeURIComponent('//evil.example/findings'),
    encodeURIComponent('https://evil.example/'),
    encodeURIComponent('/login?from=%2Fhosts'),
  ])('home for a target that is not a page of the app: %s', async (from) => {
    await signInFrom(`?from=${from}`);
    expect(navigate).toHaveBeenCalledWith('/', { replace: true });
  });
});
