import { useEffect, useRef } from 'react';

/**
 * Run a callback on an interval, but only while the document is visible.
 *
 * Background tabs do not tick — saves battery, stops API hammering on
 * outage, and avoids racing fetches that arrive after the user returns.
 * On visibility returning to visible, the callback fires once
 * immediately so the user sees fresh data rather than the last value
 * from before they backgrounded the tab.
 *
 * Scheduling is settle-then-wait, not a fixed interval: the next run is
 * scheduled `intervalMs` after the previous callback settles, and a tick
 * that arrives while one is still in flight is skipped.  A slow endpoint
 * therefore never accumulates overlapping requests (which could also
 * resolve out of order).  A rejected callback backs the delay off —
 * doubling up to 4× the interval — and a success resets it.
 *
 * Pass `enabled = false` (or null intervalMs) to suspend polling.
 *
 * Use cases: notification badge poll, agent activity rail, active
 * recon/execution session refresh.
 */
export function useVisibilityPoll(
  callback: () => void | Promise<void>,
  intervalMs: number | null,
  enabled = true,
): void {
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    if (!enabled || intervalMs == null || intervalMs <= 0) return;

    const MAX_BACKOFF_FACTOR = 4;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inFlight = false;
    let backoffFactor = 1;
    let stopped = false;

    const isVisible = () =>
      typeof document === 'undefined' || document.visibilityState === 'visible';

    const clearTimer = () => {
      if (timer != null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = () => {
      clearTimer();
      if (stopped || !isVisible()) return;
      timer = setTimeout(run, intervalMs * backoffFactor);
    };

    // One tick: skip if a previous callback is still settling, otherwise run
    // it and schedule the next tick relative to when it settles.
    const run = () => {
      clearTimer();
      if (stopped || !isVisible()) return;
      if (inFlight) {
        schedule();
        return;
      }
      inFlight = true;
      let result: void | Promise<void>;
      try {
        result = callbackRef.current();
      } catch {
        result = Promise.reject();
      }
      Promise.resolve(result).then(
        () => {
          inFlight = false;
          backoffFactor = 1;
          schedule();
        },
        () => {
          inFlight = false;
          backoffFactor = Math.min(backoffFactor * 2, MAX_BACKOFF_FACTOR);
          schedule();
        },
      );
    };

    const onVisibilityChange = () => {
      if (isVisible()) {
        // Re-sync immediately on return — the user expects current data.
        // Still one-at-a-time: if a callback is mid-flight, run() just
        // reschedules instead of starting a second one.
        run();
      } else {
        clearTimer();
      }
    };

    if (isVisible()) schedule();
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      stopped = true;
      clearTimer();
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [intervalMs, enabled]);
}
