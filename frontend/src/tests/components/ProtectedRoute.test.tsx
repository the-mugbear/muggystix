/**
 * Review 2026-10-01 R32 — a route's `requiredRole="analyst"` means the
 * caller's role on the CURRENT PROJECT.  It was the account role, which every
 * member passes, so a project viewer reached Scope and Ingestion Results and
 * met their 403s there.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ account: 'member', projectRole: 'viewer' as string | undefined }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: true, isLoading: false,
    user: { id: 1, username: 'u', role: state.account, must_change_password: false },
    hasPermission: (r: string) => (state.account === 'admin' ? true : r !== 'admin'),
  }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: state.projectRole } }),
}));

import ProtectedRoute from '../../components/ProtectedRoute';

const show = (requiredRole: string) => render(
  <MemoryRouter><ProtectedRoute requiredRole={requiredRole}><p>the page</p></ProtectedRoute></MemoryRouter>,
);

beforeEach(() => { state.account = 'member'; state.projectRole = 'viewer'; });

describe('ProtectedRoute', () => {
  it('refuses a project viewer an analyst page, naming their PROJECT role', () => {
    show('analyst');
    expect(screen.queryByText('the page')).toBeNull();
    expect(screen.getByText('Access Denied')).toBeInTheDocument();
    expect(screen.getByText(/Your role on this project:/).textContent).toContain('viewer');
  });

  it('lets a project analyst in, and anyone into a viewer page', () => {
    state.projectRole = 'analyst';
    const { unmount } = show('analyst');
    expect(screen.getByText('the page')).toBeInTheDocument();
    unmount();
    state.projectRole = 'viewer';
    show('viewer');
    expect(screen.getByText('the page')).toBeInTheDocument();
  });

  it('never locks a global admin out of a project page', () => {
    state.account = 'admin';
    state.projectRole = 'viewer';
    show('analyst');
    expect(screen.getByText('the page')).toBeInTheDocument();
  });

  it('lets the server decide while the project role has not loaded', () => {
    state.projectRole = undefined;
    show('analyst');
    expect(screen.getByText('the page')).toBeInTheDocument();
  });

  it('keeps "admin" the account role: a project admin does not reach instance settings', () => {
    state.projectRole = 'admin';
    show('admin');
    expect(screen.queryByText('the page')).toBeNull();
    expect(screen.getByText(/Your role:/).textContent).toContain('member');
  });
});
