import { useCallback, useEffect, useRef } from 'react';

import { asAxiosError } from '../utils/apiErrors';

/**
 * useLatestRequest — one shared "latest request wins" convention.
 *
 * Every page that refetches on filter change had the same race: a slow
 * response for filter set A landing after B's response and overwriting B's
 * rows (or A's failure replacing B's error state).  Hosts and Findings each
 * grew their own AbortController + generation-counter fix; this hook is that
 * fix, once, so the next screen gets it by calling `run` instead of
 * re-deriving it.
 *
 *   const run = useLatestRequest();
 *   const r = await run((signal) => listThings(filters, signal));
 *   if (r.stale) return;            // superseded or aborted — write nothing
 *   if (r.ok) setRows(r.value); else setError(formatApiError(r.error, '…'));
 *
 * Guarantees:
 *   - starting a run aborts the previous in-flight one (axios honours the
 *     signal, so the network request is actually cancelled);
 *   - the result of anything but the newest run is reported as `stale`,
 *     never as a value or an error — cancellation errors are folded into
 *     `stale` so callers never toast them;
 *   - unmount aborts whatever is in flight.
 *
 * One hook instance = one request lane.  A page with two independent
 * fetches (rows + filter facets) uses two instances so they don't cancel
 * each other.
 */
export type LatestResult<T> =
  | { stale: true; ok?: undefined }
  | { stale: false; ok: true; value: T }
  | { stale: false; ok: false; error: unknown };

export function isCancelledRequest(err: unknown): boolean {
  if (err instanceof DOMException && err.name === 'AbortError') return true;
  const e = asAxiosError(err);
  return e.name === 'CanceledError' || e.code === 'ERR_CANCELED' || e.name === 'AbortError';
}

export function useLatestRequest(): <T>(fn: (signal: AbortSignal) => Promise<T>) => Promise<LatestResult<T>> {
  const abortRef = useRef<AbortController | null>(null);
  const genRef = useRef(0);

  useEffect(() => () => abortRef.current?.abort(), []);

  return useCallback(async <T,>(fn: (signal: AbortSignal) => Promise<T>): Promise<LatestResult<T>> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const gen = ++genRef.current;
    const isCurrent = () => gen === genRef.current && !controller.signal.aborted;
    try {
      const value = await fn(controller.signal);
      return isCurrent() ? { stale: false, ok: true, value } : { stale: true };
    } catch (error) {
      if (isCancelledRequest(error) || !isCurrent()) return { stale: true };
      return { stale: false, ok: false, error };
    }
  }, []);
}
