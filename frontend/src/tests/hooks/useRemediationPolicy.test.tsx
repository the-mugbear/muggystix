/**
 * The installation's remediation setting when its read FAILS (code review
 * 2026-10-09).  The answer is remembered for the session and the shell that
 * reads it never unmounts, so a failure must not be remembered like an answer:
 * it is not "off", the next reader asks again, and it is asked again by itself
 * while it is failing.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from '../../lib/query';
import { withClient } from '../helpers/heldByMutations';

const getRemediationPolicy = vi.fn();
vi.mock('../../services/api', () => ({
  getRemediationPolicy: (...a: unknown[]) => getRemediationPolicy(...a),
}));

import { useRemediationPolicy } from '../../hooks/useRemediationPolicy';

const POLICY = { enabled: true, due_soon_days: 7, time_zone: 'UTC' };

beforeEach(() => {
  getRemediationPolicy.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useRemediationPolicy — a failed read', () => {
  it('is reported as a failure, never as "off", and retry reads it', async () => {
    getRemediationPolicy.mockRejectedValueOnce(new Error('Network Error'));
    const { result } = renderHook(() => useRemediationPolicy());
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.enabled).toBe(false);
    expect(result.current.loading).toBe(false);

    getRemediationPolicy.mockResolvedValue(POLICY);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.enabled).toBe(true));
    expect(result.current.error).toBeNull();
  });

  it('is asked again by the next reader that mounts — a remembered answer is not', async () => {
    const client = createQueryClient();
    getRemediationPolicy.mockRejectedValueOnce(new Error('Network Error'));
    const first = renderHook(() => useRemediationPolicy(), { wrapper: withClient(client) });
    await waitFor(() => expect(first.result.current.error).not.toBeNull());
    first.unmount();

    getRemediationPolicy.mockResolvedValue(POLICY);
    const second = renderHook(() => useRemediationPolicy(), { wrapper: withClient(client) });
    await waitFor(() => expect(second.result.current.enabled).toBe(true));
    expect(getRemediationPolicy).toHaveBeenCalledTimes(2);
    second.unmount();

    // Now it is an ANSWER: a third reader is given it without a request.
    const third = renderHook(() => useRemediationPolicy(), { wrapper: withClient(client) });
    expect(third.result.current.enabled).toBe(true);
    await act(async () => { await Promise.resolve(); });
    expect(getRemediationPolicy).toHaveBeenCalledTimes(2);
  });

  it('is asked again by itself while it is failing (a reader that never unmounts), and not once it has answered', async () => {
    vi.useFakeTimers();
    getRemediationPolicy.mockRejectedValueOnce(new Error('Network Error'));
    const { result } = renderHook(() => useRemediationPolicy());
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(result.current.error).not.toBeNull();
    expect(getRemediationPolicy).toHaveBeenCalledTimes(1);

    getRemediationPolicy.mockResolvedValue(POLICY);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_010); });
    expect(getRemediationPolicy).toHaveBeenCalledTimes(2);
    expect(result.current.enabled).toBe(true);

    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60_000); });
    expect(getRemediationPolicy).toHaveBeenCalledTimes(2);
  });
});
