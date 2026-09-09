import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useLatestRequest } from '../../hooks/useLatestRequest';

// Review 2026-09-09 B2 — one "latest request wins" convention.  The hook
// must flag anything but the newest run as stale, abort the predecessor's
// signal, fold cancellation into stale, and abort on unmount.

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void };
const deferred = <T,>(): Deferred<T> => {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};

describe('useLatestRequest', () => {
  it('flags an out-of-order older response as stale and delivers the newer one', async () => {
    const { result } = renderHook(() => useLatestRequest());
    const run = result.current;
    const a = deferred<string>();
    const b = deferred<string>();
    const pa = run(() => a.promise);
    const pb = run(() => b.promise);
    b.resolve('B');
    a.resolve('A'); // late
    const [ra, rb] = await Promise.all([pa, pb]);
    expect(ra).toEqual({ stale: true });
    expect(rb).toEqual({ stale: false, ok: true, value: 'B' });
  });

  it('aborts the previous signal when a new run starts', async () => {
    const { result } = renderHook(() => useLatestRequest());
    const run = result.current;
    const seen: AbortSignal[] = [];
    const a = deferred<number>();
    const pa = run((signal) => { seen.push(signal); return a.promise; });
    expect(seen[0].aborted).toBe(false);
    const pb = run(async () => 2);
    expect(seen[0].aborted).toBe(true);
    // A rejects with a cancel-shaped error → stale, never an error.
    a.reject(Object.assign(new Error('canceled'), { name: 'CanceledError', code: 'ERR_CANCELED' }));
    expect(await pa).toEqual({ stale: true });
    expect(await pb).toEqual({ stale: false, ok: true, value: 2 });
  });

  it('reports a real failure of the newest run as an error, not stale', async () => {
    const { result } = renderHook(() => useLatestRequest());
    const boom = new Error('500');
    const r = await result.current(async () => { throw boom; });
    expect(r).toEqual({ stale: false, ok: false, error: boom });
  });

  it('aborts the in-flight signal on unmount', async () => {
    const { result, unmount } = renderHook(() => useLatestRequest());
    let signal: AbortSignal | undefined;
    const d = deferred<void>();
    const p = result.current((s) => { signal = s; return d.promise; });
    unmount();
    expect(signal?.aborted).toBe(true);
    d.resolve();
    expect(await p).toEqual({ stale: true });
  });
});
