/**
 * The operator's own live agent sessions (5.328.0).
 *
 * "Live" is decided HERE, by the key's expiry. The list this hook used to read
 * (`GET /assist/sessions`) derived an `ended` status on the server for a
 * session whose key had run out; the one session list reports the stored
 * status beside `key_expires_at` — such a session is resumable, not over — so
 * this is where "an agent can use it right now" is pinned.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { useQueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ listAgentSessions: vi.fn() }));
vi.mock('../../services/api', () => api);
const auth = vi.hoisted(() => ({ user: { id: 7 } as { id: number } | null }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }));

import { useMyAssistSessions } from '../../hooks/useMyAssistSessions';
import { invalidateReads } from '../../lib/query';
import { hasLiveKey } from '../../utils/agentRuns';

const HOUR = 3_600_000;
const at = (ms: number) => new Date(Date.now() + ms).toISOString();
const session = (over = {}) => ({
  kind: 'project', id: 72, project_id: 1, status: 'active', user_id: 7,
  key_expires_at: at(HOUR), ...over,
});
const listed = (...sessions: object[]) => ({ project_id: 1, sessions, total: sessions.length });

beforeEach(() => {
  vi.clearAllMocks();
  auth.user = { id: 7 };
});

describe('hasLiveKey', () => {
  const now = Date.parse('2026-10-01T12:00:00Z');
  it('is true only for an active session whose key has not run out', () => {
    expect(hasLiveKey({ status: 'active', key_expires_at: '2026-10-01T12:00:01Z' }, now)).toBe(true);
    // Run out, on the second: nothing can use it (it may still be resumable).
    expect(hasLiveKey({ status: 'active', key_expires_at: '2026-10-01T12:00:00Z' }, now)).toBe(false);
    expect(hasLiveKey({ status: 'active', key_expires_at: '2026-10-01T11:00:00Z' }, now)).toBe(false);
    // Revoked: no key at all.
    expect(hasLiveKey({ status: 'active', key_expires_at: null }, now)).toBe(false);
    expect(hasLiveKey({ status: 'active' }, now)).toBe(false);
    // Ended, whatever its last key said.
    expect(hasLiveKey({ status: 'ended', key_expires_at: '2026-10-02T12:00:00Z' }, now)).toBe(false);
  });
});

describe('useMyAssistSessions', () => {
  it('asks for this operator’s active project sessions and keeps the ones with a live key', async () => {
    api.listAgentSessions.mockResolvedValue(listed(
      session({ id: 72 }),
      session({ id: 60, key_expires_at: at(-HOUR) }),   // key ran out: resumable, not live
      session({ id: 61, key_expires_at: null }),        // key revoked
    ));
    const { result } = renderHook(() => useMyAssistSessions());
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(result.current.sessions.map((s) => s.id)).toEqual([72]));
    // Narrowed on the server, not by filtering the whole project's rows here.
    expect(api.listAgentSessions).toHaveBeenCalledWith({ kind: 'project', status: 'active', user_id: 7 });
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
