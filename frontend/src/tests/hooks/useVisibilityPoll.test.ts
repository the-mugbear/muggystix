import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useVisibilityPoll } from '../../hooks/useVisibilityPoll';

// Review 2026-09-09 #5 — the hook must never overlap callbacks, must schedule
// relative to settle time, back off on rejection, and re-sync on visibility.

const setVisibility = (state: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
};

describe('useVisibilityPoll', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('does not overlap when the callback is slower than the interval', async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    let calls = 0;
    const cb = vi.fn(() => {
      calls += 1;
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      return new Promise<void>((resolve) => setTimeout(() => { inFlight -= 1; resolve(); }, 250));
    });
    renderHook(() => useVisibilityPoll(cb, 100));

    // First tick at 100ms; it takes 250ms.  A fixed interval would fire again
    // at 200 and 300 while the first is still running.
    await vi.advanceTimersByTimeAsync(100);
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(calls).toBe(1); // still in flight → no second start
    // Settles at 350; next run 100ms after settle → 450.
    await vi.advanceTimersByTimeAsync(150);
    expect(calls).toBe(2);
    expect(maxInFlight).toBe(1);
  });

  it('backs off after a rejection and resets on success', async () => {
    let fail = true;
    const cb = vi.fn(() => (fail ? Promise.reject(new Error('down')) : Promise.resolve()));
    renderHook(() => useVisibilityPoll(cb, 100));

    await vi.advanceTimersByTimeAsync(100); // run 1 (rejects) → factor 2
    expect(cb).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(150);
    expect(cb).toHaveBeenCalledTimes(1); // 200ms delay now
    await vi.advanceTimersByTimeAsync(50);
    expect(cb).toHaveBeenCalledTimes(2); // run 2 (rejects) → factor 4
    await vi.advanceTimersByTimeAsync(350);
    expect(cb).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(50);
    expect(cb).toHaveBeenCalledTimes(3); // run 3 (rejects) → capped at 4×
    await vi.advanceTimersByTimeAsync(400);
    expect(cb).toHaveBeenCalledTimes(4);

    fail = false;
    await vi.advanceTimersByTimeAsync(400); // run 5 succeeds → factor resets
    expect(cb).toHaveBeenCalledTimes(5);
    await vi.advanceTimersByTimeAsync(100);
    expect(cb).toHaveBeenCalledTimes(6);
  });

  it('fires immediately on return to visible, without overlapping', async () => {
    const cb = vi.fn(() => new Promise<void>((resolve) => setTimeout(resolve, 500)));
    renderHook(() => useVisibilityPoll(cb, 1000));

    setVisibility('hidden');
    await vi.advanceTimersByTimeAsync(3000);
    expect(cb).toHaveBeenCalledTimes(0); // hidden tabs don't tick

    setVisibility('visible');
    expect(cb).toHaveBeenCalledTimes(1); // immediate re-sync

    // Flapping visibility while the callback is mid-flight must not start
    // a second one.
    setVisibility('hidden');
    setVisibility('visible');
    expect(cb).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(500 + 1000);
    expect(cb).toHaveBeenCalledTimes(2);
  });

  it('stops entirely on unmount', async () => {
    const cb = vi.fn(() => Promise.resolve());
    const { unmount } = renderHook(() => useVisibilityPoll(cb, 100));
    await vi.advanceTimersByTimeAsync(100);
    expect(cb).toHaveBeenCalledTimes(1);
    unmount();
    await vi.advanceTimersByTimeAsync(1000);
    expect(cb).toHaveBeenCalledTimes(1);
  });
});
