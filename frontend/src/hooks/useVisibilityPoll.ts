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

    let timer: ReturnType<typeof setInterval> | null = null;

    const isVisible = () =>
      typeof document === 'undefined' || document.visibilityState === 'visible';

    const start = () => {
      if (timer != null) return;
      timer = setInterval(() => {
        if (isVisible()) {
          void callbackRef.current();
        }
      }, intervalMs);
    };

    const stop = () => {
      if (timer != null) {
        clearInterval(timer);
        timer = null;
      }
    };

    const onVisibilityChange = () => {
      if (isVisible()) {
        // Re-sync immediately on return — the user expects current data.
        void callbackRef.current();
        start();
      } else {
        stop();
      }
    };

    if (isVisible()) start();
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [intervalMs, enabled]);
}
