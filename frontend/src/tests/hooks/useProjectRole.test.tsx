/**
 * Review 2026-10-01 R32 / B1 — project controls follow the caller's PROJECT
 * role.  The account role is binary, and every member passed the old
 * `hasPermission('analyst')`, so a viewer saw every write control.
 */
import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  account: 'member' as string,
  project: { id: 1, name: 'P', my_role: 'viewer' } as Record<string, unknown> | null,
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'u', role: state.account },
    isAuthenticated: true,
    // As the real one since 5.355.0: it answers "is this account a global
    // administrator" and nothing else.
    hasPermission: (r: string) => r === 'admin' && state.account === 'admin',
  }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: state.project }),
}));

import { useProjectRole, useRoleGate } from '../../hooks/useProjectRole';

const as = (role: string | null | undefined, account = 'member') => {
  state.account = account;
  state.project = role === undefined ? { id: 1, name: 'P' } : { id: 1, name: 'P', my_role: role };
  return renderHook(() => useProjectRole()).result.current;
};

beforeEach(() => { state.account = 'member'; });

describe('useProjectRole', () => {
  it('a viewer reads: no write, no export, not an admin', () => {
    expect(as('viewer')).toEqual({
      role: 'viewer', canWrite: false, canExport: false, isProjectAdmin: false, isGlobalAdmin: false,
    });
  });

  it('an auditor exports but does not write', () => {
    expect(as('auditor')).toMatchObject({ role: 'auditor', canWrite: false, canExport: true, isProjectAdmin: false });
  });

  it('an analyst writes and exports', () => {
    expect(as('analyst')).toMatchObject({ role: 'analyst', canWrite: true, canExport: true, isProjectAdmin: false });
  });

  it('a project admin does everything in the project, without being a global admin', () => {
    expect(as('admin')).toMatchObject({ canWrite: true, canExport: true, isProjectAdmin: true, isGlobalAdmin: false });
  });

  it('a role that has not loaded leaves the decision to the server', () => {
    expect(as(undefined)).toMatchObject({ role: undefined, canWrite: true, canExport: true, isProjectAdmin: true });
    state.project = null;
    expect(renderHook(() => useProjectRole()).result.current).toMatchObject({ canWrite: true, canExport: true });
  });

  it('a known non-member (my_role: null) is below everything', () => {
    expect(as(null)).toMatchObject({ role: null, canWrite: false, canExport: false, isProjectAdmin: false });
  });

  it('a global admin can do everything, whatever the membership says', () => {
    expect(as('viewer', 'admin')).toEqual({
      role: 'admin', canWrite: true, canExport: true, isProjectAdmin: true, isGlobalAdmin: true,
    });
    expect(as(null, 'admin')).toMatchObject({ canWrite: true, isGlobalAdmin: true });
  });
});

describe('useRoleGate — what a route or nav entry requires', () => {
  const gate = (role: string | null | undefined, account = 'member') => {
    state.account = account;
    state.project = role === undefined ? { id: 1 } : { id: 1, my_role: role };
    return renderHook(() => useRoleGate()).result.current;
  };

  it('"analyst" and "auditor" mean the PROJECT role', () => {
    expect(gate('viewer')('analyst')).toBe(false);
    expect(gate('viewer')('auditor')).toBe(false);
    expect(gate('auditor')('auditor')).toBe(true);
    expect(gate('auditor')('analyst')).toBe(false);
    expect(gate('analyst')('analyst')).toBe(true);
  });

  it('"admin" stays the account role: a project admin is not let into instance settings', () => {
    expect(gate('admin')('admin')).toBe(false);
    expect(gate('viewer', 'admin')('admin')).toBe(true);
  });

  it('never locks a global admin out of a project page, nor anyone out of a viewer page', () => {
    expect(gate('viewer', 'admin')('analyst')).toBe(true);
    expect(gate('viewer')('viewer')).toBe(true);
    expect(gate('viewer')(undefined)).toBe(true);
  });
});
