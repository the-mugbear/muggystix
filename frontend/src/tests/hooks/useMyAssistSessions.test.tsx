/**
 * The operator's own live agent sessions (5.328.0).
 *
 * "Live" is the row's `key_state`, decided by the server on its own clock
 * (review 2026-10-10, R1e: it was the key's expiry against the browser's
 * clock). A session whose key ran out is `resumable`, not over, and not live.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useQueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listAgentSessions: vi.fn() }));
vi.mock('../../services/api', () => api);
const auth = vi.hoisted(() => ({ user: { id: 7 } as { id: number } | null }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));
// The project the hook is used in: the read names it first.
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1 } }) }));

import { useMyAssistSessions } from '../../hooks/useMyAssistSessions';
import { invalidateReads } from '../../lib/query';
import { hasLiveKey } from '../../utils/agentRuns';

const HOUR = 3_600_000;
const at = (ms: number) => new Date(Date.now() + ms).toISOString();
const session = (over = {}) => ({
  id: 72, project_id: 1, status: 'active', user_id: 7,
  key_expires_at: at(HOUR), key_state: 'live', ...over,
});
const listed = (...sessions: object[]) => ({ project_id: 1, sessions, total: sessions.length });

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { id: 7 };
});

describe('hasLiveKey', () => {
  it('is true only for an active session the server calls live', () => {
    expect(hasLiveKey({ status: 'active', key_state: 'live' })).toBe(true);
    // Run out or revoked: nothing can use it (it may still be resumable).
    expect(hasLiveKey({ status: 'active', key_state: 'resumable' })).toBe(false);
    expect(hasLiveKey({ status: 'active', key_state: 'ended' })).toBe(false);
    // Ended, whatever else the row said.
    expect(hasLiveKey({ status: 'ended', key_state: 'live' })).toBe(false);
  });
});

describe('useMyAssistSessions', () => {
  it('asks for this operator’s active sessions and keeps the ones with a live key', async () => {
    api.listAgentSessions.mockResolvedValue(listed(
      session({ id: 72 }),
      session({ id: 60, key_expires_at: at(-HOUR), key_state: 'resumable' }),   // key ran out: resumable, not live
      session({ id: 61, key_expires_at: null, key_state: 'resumable' }),        // key revoked
    ));
    const { result } = renderHook(() => useMyAssistSessions());
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(result.current.sessions.map((s) => s.id)).toEqual([72]));
    // Narrowed on the server, not by filtering the whole project's rows here.
    expect(api.listAgentSessions).toHaveBeenCalledWith(
      1, { status: 'active', user_id: 7 }, { signal: expect.any(AbortSignal) },
    );
    expect(result.current.failed).toBe(false);
  });

  it('reports a failed read as failed, never as "no sessions"', async () => {
    api.listAgentSessions.mockRejectedValue(new Error('down'));
    const { result } = renderHook(() => useMyAssistSessions());
    await waitFor(() => expect(result.current.failed).toBe(true));
    expect(result.current.sessions).toEqual([]);
  });

  it('does not read anything while disabled or signed out', async () => {
    renderHook(() => useMyAssistSessions({ enabled: false }));
    auth.user = null;
    renderHook(() => useMyAssistSessions());
    await Promise.resolve();
    expect(api.listAgentSessions).not.toHaveBeenCalled();
  });

  // The hook had a `refresh()` its callers ran after starting or ending a
  // session.  Those writes now say `listAgentSessions` is out of date
  // (`invalidateReads`), and that alone brings the new session here.
  it('re-reads when a write says the sessions are out of date', async () => {
    api.listAgentSessions.mockResolvedValue(listed());
    const { result } = renderHook(() => ({ mine: useMyAssistSessions(), client: useQueryClient() }));
    await waitFor(() => expect(api.listAgentSessions).toHaveBeenCalledTimes(1));
    api.listAgentSessions.mockResolvedValue(listed(session()));
    await act(() => invalidateReads(result.current.client, 'listAgentSessions'));
    await waitFor(() => expect(result.current.mine.sessions).toHaveLength(1));
    expect(api.listAgentSessions).toHaveBeenCalledTimes(2);
  });
});
