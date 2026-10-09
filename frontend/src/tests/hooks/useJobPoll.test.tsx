/**
 * hooks/useJobPoll — following a server-side job from the answer of the
 * request that started it.  These are the guarantees the draft report's
 * previews and the contact's remediation document each built by hand
 * (`initialData` + `staleTime` + an `enabled` function + `pollEvery`).
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useJobPoll, useJobPolls } from '../../hooks/useJobPoll';

interface Job { id: number; status: 'queued' | 'processing' | 'completed' | 'failed' }

const setVisibility = (state: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  window.dispatchEvent(new Event('visibilitychange'));
};
const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
/** An answer reaches the hook a tick after its request resolves (the client
 *  notifies from a timer): one millisecond, which the timings below allow for. */
const shown = () => pass(1);
const isDone = (job: Job) => job.status === 'completed' || job.status === 'failed';
const queued: Job = { id: 7, status: 'queued' };

const follow = (read: (job: Job, signal: AbortSignal) => Promise<Job>, job: Job | null = queued) => renderHook(
  ({ given }: { given: Job | null }) => useJobPoll({
    queryKey: ['getJob', given?.id], queryFn: read, job: given, interval: 1000, isDone,
  }),
  { initialProps: { given: job } },
);

describe('useJobPoll', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility('visible');
  });
  afterEach(() => {
    setVisibility('visible');
    vi.useRealTimers();
  });

  it('asks nothing at once: the given job is the first reading, and the first poll is one interval later', async () => {
    const read = vi.fn().mockResolvedValue({ id: 7, status: 'processing' });
    const { result } = follow(read);
    expect(result.current).toEqual({ job: queued, running: true, error: null });
    await pass(999);
    expect(read).not.toHaveBeenCalled();
    await pass(1);
    expect(read).toHaveBeenCalledTimes(1);
    // The read is given the job it was started with.
    expect(read.mock.calls[0][0]).toEqual(queued);
    expect(read.mock.calls[0][1]).toBeInstanceOf(AbortSignal);
    await shown();
    expect(result.current.job).toEqual({ id: 7, status: 'processing' });
  });

  it('asks once per interval while the job runs, and never again once it is done', async () => {
    const read = vi.fn()
      .mockResolvedValueOnce({ id: 7, status: 'queued' })
      .mockResolvedValueOnce({ id: 7, status: 'processing' })
      .mockResolvedValue({ id: 7, status: 'completed' });
    const { result } = follow(read);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(2);
    expect(result.current.running).toBe(true);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(3);
    await shown();
    expect(result.current).toEqual({ job: { id: 7, status: 'completed' }, running: false, error: null });

    // Done: not by the interval, and not on return to the tab.
    await pass(60_000);
    setVisibility('hidden');
    setVisibility('visible');
    await pass(60_000);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it('never asks about a job that is already finished when it is given, or about no job', async () => {
    const read = vi.fn().mockResolvedValue({ id: 7, status: 'completed' });
    const done = follow(read, { id: 7, status: 'failed' });
    const none = follow(read, null);
    await pass(60_000);
    expect(read).not.toHaveBeenCalled();
    expect(done.result.current).toEqual({ job: { id: 7, status: 'failed' }, running: false, error: null });
    expect(none.result.current).toEqual({ job: null, running: false, error: null });
  });

  it('reports a failed poll and keeps the job on screen; the failure goes when a poll answers', async () => {
    const failure = new Error('503');
    const read = vi.fn()
      .mockResolvedValueOnce({ id: 7, status: 'processing' })
      .mockRejectedValueOnce(failure)
      .mockResolvedValue({ id: 7, status: 'completed' });
    const { result } = follow(read);
    await pass(1000);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(2);
    await shown();
    expect(result.current).toEqual({ job: { id: 7, status: 'processing' }, running: true, error: failure });

    await pass(2000);
    expect(read).toHaveBeenCalledTimes(3);
    await shown();
    expect(result.current).toEqual({ job: { id: 7, status: 'completed' }, running: false, error: null });
  });

  it('asks half as often while the read is failing, and at the interval again once it answers', async () => {
    const read = vi.fn().mockRejectedValue(new Error('503'));
    const { result } = follow(read);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(2);
    // Still the job the request answered, still running.
    expect(result.current.job).toEqual(queued);
    expect(result.current.running).toBe(true);

    read.mockResolvedValue({ id: 7, status: 'processing' });
    await pass(2000);
    expect(read).toHaveBeenCalledTimes(3);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(4);
  });

  it('asks nothing while the tab is hidden, and once at once on return', async () => {
    const read = vi.fn().mockResolvedValue({ id: 7, status: 'processing' });
    follow(read);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(1);

    setVisibility('hidden');
    await pass(5000);
    expect(read).toHaveBeenCalledTimes(1);

    setVisibility('visible');
    await pass(0);
    expect(read).toHaveBeenCalledTimes(2);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(3);
  });

  it('a new job starts from its own answer: nothing at once, and the previous job is not asked about', async () => {
    const read = vi.fn(async (job: Job): Promise<Job> => ({ id: job.id, status: 'processing' }));
    const { result, rerender } = follow(read);
    await pass(1000);
    expect(read).toHaveBeenCalledTimes(1);

    rerender({ given: { id: 8, status: 'queued' } });
    expect(result.current.job).toEqual({ id: 8, status: 'queued' });
    await pass(999);
    expect(read).toHaveBeenCalledTimes(1);
    await pass(1);
    expect(read).toHaveBeenCalledTimes(2);
    expect(read.mock.calls[1][0]).toEqual({ id: 8, status: 'queued' });

    // No job any more (the reader chose another format): nothing is followed.
    rerender({ given: null });
    expect(result.current).toEqual({ job: null, running: false, error: null });
    await pass(60_000);
    expect(read).toHaveBeenCalledTimes(2);
  });
});

describe('useJobPolls', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility('visible');
  });
  afterEach(() => {
    setVisibility('visible');
    vi.useRealTimers();
  });

  it('follows each job on its own: one finishing does not stop the other, and a job added later starts from its answer', async () => {
    const status: Record<number, Job['status']> = { 1: 'completed', 2: 'processing', 3: 'processing' };
    const read = vi.fn(async (job: Job): Promise<Job> => ({ id: job.id, status: status[job.id] }));
    const asked = (id: number) => read.mock.calls.filter(([job]) => job.id === id).length;
    const { result, rerender } = renderHook(
      ({ jobs }: { jobs: Job[] }) => useJobPolls({
        jobs, queryKey: (job) => ['getJob', job.id], queryFn: read, interval: 1000, isDone,
      }),
      { initialProps: { jobs: [{ id: 1, status: 'queued' }, { id: 2, status: 'queued' }] as Job[] } },
    );
    expect(read).not.toHaveBeenCalled();
    expect(result.current.map((p) => p.running)).toEqual([true, true]);

    await pass(1000);
    expect([asked(1), asked(2)]).toEqual([1, 1]);
    await shown();
    expect(result.current.map((p) => p.job?.status)).toEqual(['completed', 'processing']);
    expect(result.current.map((p) => p.running)).toEqual([false, true]);

    // A third job is started half an interval later.
    await pass(500);
    rerender({ jobs: [{ id: 1, status: 'queued' }, { id: 2, status: 'queued' }, { id: 3, status: 'queued' }] });
    expect(asked(3)).toBe(0);
    await pass(500);
    expect([asked(1), asked(2), asked(3)]).toEqual([1, 2, 0]);
    await pass(500);
    expect([asked(1), asked(2), asked(3)]).toEqual([1, 2, 1]);
    await shown();
    // The finished job keeps its last reading, in the order of `jobs`.
    expect(result.current.map((p) => p.job?.status)).toEqual(['completed', 'processing', 'processing']);
  });
});
