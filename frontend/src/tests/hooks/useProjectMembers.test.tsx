/**
 * One loader for the project's members: full rows with a status, cached per
 * project, shared by every picker and by the mention helpers.  A failed load
 * is `error` (with a retry) — it used to be an empty list at five call sites,
 * which the pickers showed as "No members".
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const listProjectMembers = vi.fn();
vi.mock('../../services/api', () => ({ listProjectMembers: (...a: unknown[]) => listProjectMembers(...a) }));
const project = vi.hoisted(() => ({ id: 1 as number | null }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: project.id == null ? null : { id: project.id, name: 'P' } }),
}));

import { resetProjectMembersCache, useProjectMembers, useProjectRoster } from '../../hooks/useProjectMembers';

const member = (user_id: number, username: string | null, full_name: string | null = null) => ({
  id: user_id, project_id: 1, user_id, username, full_name, role: 'analyst', created_at: '',
});
const ANA = member(1, 'ana', 'Ana Analyst');
const BEN = member(2, 'ben');
const NAMELESS = member(3, null, 'No Username');

beforeEach(() => {
  listProjectMembers.mockReset();
  resetProjectMembersCache();
  project.id = 1;
});

describe('useProjectRoster', () => {
  it('returns the full rows once loaded, and asks once for every reader of the project', async () => {
    listProjectMembers.mockResolvedValue([ANA, BEN]);
    const first = renderHook(() => useProjectRoster());
    const second = renderHook(() => useProjectRoster());
    expect(first.result.current).toMatchObject({ status: 'loading', members: [] });
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    expect(first.result.current.members).toEqual([ANA, BEN]);
    expect(second.result.current.members[0]).toMatchObject({ user_id: 1, role: 'analyst' });
    expect(listProjectMembers).toHaveBeenCalledTimes(1);
  });

  it('a failed load is an error, never an empty roster — and retry asks again', async () => {
    listProjectMembers.mockRejectedValueOnce(new Error('503'));
    const { result } = renderHook(() => useProjectRoster());
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.members).toEqual([]);

    listProjectMembers.mockResolvedValue([ANA]);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.members).toEqual([ANA]);
    expect(listProjectMembers).toHaveBeenCalledTimes(2);
  });

  it('a failure is not cached: the next reader asks again', async () => {
    listProjectMembers.mockRejectedValueOnce(new Error('503'));
    const failed = renderHook(() => useProjectRoster());
    await waitFor(() => expect(failed.result.current.status).toBe('error'));
    listProjectMembers.mockResolvedValue([BEN]);
    const next = renderHook(() => useProjectRoster());
    await waitFor(() => expect(next.result.current.status).toBe('ready'));
    expect(next.result.current.members).toEqual([BEN]);
  });

  it('keeps each project’s roster apart and never shows one project the other’s rows', async () => {
    listProjectMembers.mockResolvedValueOnce([ANA]);
    const { result, rerender } = renderHook(() => useProjectRoster());
    await waitFor(() => expect(result.current.members).toEqual([ANA]));

    let answer!: (rows: unknown) => void;
    listProjectMembers.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
    project.id = 2;
    rerender();
    // Project 2 has not answered: its picker is loading, not showing project 1's people.
    expect(result.current).toMatchObject({ status: 'loading', members: [] });
    await act(async () => { answer([BEN]); });
    expect(result.current.members).toEqual([BEN]);

    // Back on project 1: its own cached rows, no third request.
    project.id = 1;
    rerender();
    await waitFor(() => expect(result.current.members).toEqual([ANA]));
    expect(listProjectMembers).toHaveBeenCalledTimes(2);
  });

  it('asks for nothing until it is enabled', async () => {
    listProjectMembers.mockResolvedValue([ANA]);
    const { result, rerender } = renderHook(({ enabled }) => useProjectRoster({ enabled }), { initialProps: { enabled: false } });
    expect(listProjectMembers).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(listProjectMembers).toHaveBeenCalledTimes(1);
  });
});

describe('useProjectMembers — the mention shape of the same cache', () => {
  it('gives usernames with their names, leaving out members with no username', async () => {
    listProjectMembers.mockResolvedValue([ANA, BEN, NAMELESS]);
    const roster = renderHook(() => useProjectRoster());
    const mentions = renderHook(() => useProjectMembers());
    await waitFor(() => expect(mentions.result.current).toHaveLength(2));
    expect(mentions.result.current).toEqual([
      { username: 'ana', full_name: 'Ana Analyst' },
      { username: 'ben', full_name: null },
    ]);
    await waitFor(() => expect(roster.result.current.status).toBe('ready'));
    expect(listProjectMembers).toHaveBeenCalledTimes(1);
  });

  it('is an empty list when the load fails: mentions still work typed in full', async () => {
    listProjectMembers.mockRejectedValue(new Error('boom'));
    const { result } = renderHook(() => useProjectMembers());
    await waitFor(() => expect(listProjectMembers).toHaveBeenCalled());
    expect(result.current).toEqual([]);
  });
});
