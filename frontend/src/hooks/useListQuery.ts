import { useCallback, useEffect, useRef, useState } from 'react';

import { formatApiError } from '../utils/apiErrors';
import { useLatestRequest } from './useLatestRequest';
import { useVisibilityPoll } from './useVisibilityPoll';

/**
 * useListQuery — the one way a list page fetches its rows (review 2026-10-01
 * R33 / B2).
 *
 *   const list = useListQuery(
 *     ({ offset, limit, signal }) => listThings({ status, offset, limit }, signal),
 *     [status],
 *     { pageSize: 50, poll: 60_000 },
 *   );
 *   list.rows / list.total / list.loading / list.error
 *   list.reload()      // after a change, or the Refresh button
 *   list.loadMore()    // "Show more"
 *
 * What it guarantees, so a page does not re-derive it:
 *   - **Latest request wins.**  Every fetch goes through one
 *     `useLatestRequest` lane: a new `deps` set, a reload, the poll tick and
 *     "load more" all supersede whatever is in flight, and a superseded
 *     response writes nothing.  (Proposals showed a slow "pending" response
 *     under the "Accepted" filter, with Accept buttons on it.)
 *   - **Failed is not empty.**  `error` is a message; `rows` stays `null`
 *     until a first page has loaded, and a failed RELOAD keeps the rows that
 *     were shown (with `error` set) instead of presenting an empty list.
 *   - **A reload keeps your place.**  `reload()` re-reads as many rows as
 *     "load more" has loaded (capped at `maxReload`), so deciding a row on
 *     page 3 does not drop the reader back to page 1.  New `deps` start from
 *     the first page.
 *
 * `fetcher` may close over anything: the newest one is always the one
 * called.  What decides WHEN to refetch is `deps` (compare a `useEffect`
 * dependency list).  `response` is the last first-page response, for a list
 * endpoint that carries more than rows (`has_more`, a summary).
 */
export interface ListPage<T> {
  items: T[];
  total: number;
}

export interface ListPageRequest {
  offset: number;
  limit: number;
  signal: AbortSignal;
}

export type ListFetcher<T, P extends ListPage<T> = ListPage<T>> = (request: ListPageRequest) => Promise<P>;

export interface UseListQueryOptions {
  /** Rows per page (and per "load more").  Default 50. */
  pageSize?: number;
  /** Re-read every N ms while the tab is visible (through the same lane). */
  poll?: number | null;
  /** False parks the hook: nothing is fetched and `rows` stays null. */
  enabled?: boolean;
  /** The most rows one reload asks for (the endpoint's `limit` ceiling). */
  maxReload?: number;
  /** The fallback for `error` when the failure carries no message. */
  errorMessage?: string;
}

export interface ListQuery<T, P extends ListPage<T> = ListPage<T>> {
  /** `null` until a first page has loaded for the current `deps`. */
  rows: T[] | null;
  total: number;
  /** A first-page fetch (new deps or a reload) is in flight. */
  loading: boolean;
  /** Why the last fetch failed; cleared by the next success. */
  error: string | null;
  /** Re-read the loaded rows in place.  Resolves when it settles. */
  reload: () => Promise<void>;
  /** Append the next page.  Rejects if it fails (the caller toasts). */
  loadMore: () => Promise<void>;
  loadingMore: boolean;
  /** The last first-page response, whole. */
  response: P | null;
  /** When the rows were last read successfully. */
  loadedAt: Date | null;
  /** Patch the loaded rows in place (a row the server just returned). */
  setRows: (update: (rows: T[]) => T[]) => void;
}

const sameDeps = (a: ReadonlyArray<unknown>, b: ReadonlyArray<unknown>): boolean =>
  a.length === b.length && a.every((value, i) => Object.is(value, b[i]));

export function useListQuery<T, P extends ListPage<T> = ListPage<T>>(
  fetcher: ListFetcher<T, P>,
  deps: ReadonlyArray<unknown>,
  {
    pageSize = 50, poll = null, enabled = true, maxReload = 500,
    errorMessage = 'Could not load the list.',
  }: UseListQueryOptions = {},
): ListQuery<T, P> {
  const run = useLatestRequest();
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const messageRef = useRef(errorMessage);
  messageRef.current = errorMessage;

  const depsRef = useRef(deps);
  depsRef.current = deps;

  const [rows, setRowsState] = useState<T[] | null>(null);
  // Which `deps` the loaded rows answer.  The reset below runs in an effect,
  // one render AFTER the deps change; without this that render would still
  // hand the caller the previous filter's rows under the new filter.
  const [rowsDeps, setRowsDeps] = useState<ReadonlyArray<unknown>>(deps);
  const [total, setTotal] = useState(0);
  const [response, setResponse] = useState<P | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  // How many rows are loaded — what a reload re-reads.  A ref beside the
  // state so `reload` and `loadMore` never act on a stale closure's count.
  const loadedRef = useRef(0);

  const fetchFirst = useCallback(async (limit: number): Promise<'ok' | 'failed' | 'stale'> => {
    setLoading(true);
    const askedFor = depsRef.current;
    const result = await run((signal) => fetcherRef.current({ offset: 0, limit, signal }));
    if (result.stale) return 'stale';
    setLoading(false);
    setLoadingMore(false);  // a "load more" this superseded will not report
    if (result.ok) {
      loadedRef.current = result.value.items.length;
      setRowsState(result.value.items);
      setRowsDeps(askedFor);
      setTotal(result.value.total);
      setResponse(result.value);
      setLoadedAt(new Date());
      setError(null);
      return 'ok';
    }
    setError(formatApiError(result.error, messageRef.current));
    return 'failed';
  }, [run]);

  // New deps: back to the first page, and the old rows are not this list's.
  useEffect(() => {
    loadedRef.current = 0;
    setRowsState(null);
    setRowsDeps(depsRef.current);
    setTotal(0);
    setResponse(null);
    setError(null);
    if (!enabled) {
      setLoading(false);
      return;
    }
    void fetchFirst(pageSize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, pageSize, fetchFirst]);

  const reload = useCallback(async () => {
    if (!enabled) return;
    await fetchFirst(Math.min(maxReload, Math.max(pageSize, loadedRef.current)));
  }, [enabled, fetchFirst, maxReload, pageSize]);

  const loadMore = useCallback(async () => {
    if (!enabled) return;
    setLoadingMore(true);
    const offset = loadedRef.current;
    const result = await run((signal) => fetcherRef.current({ offset, limit: pageSize, signal }));
    // Superseded by a reload or a new filter: those rows belong to a list
    // that is no longer shown.
    if (result.stale) return;
    setLoadingMore(false);
    if (!result.ok) throw result.error;
    loadedRef.current = offset + result.value.items.length;
    setRowsState((prev) => [...(prev ?? []), ...result.value.items]);
    setTotal(result.value.total);
  }, [enabled, pageSize, run]);

  // The tick goes through the same lane as everything else.  It rejects on a
  // failure so the poll backs off instead of hammering a server that is down.
  const tick = useCallback(async () => {
    const outcome = await fetchFirst(Math.min(maxReload, Math.max(pageSize, loadedRef.current)));
    if (outcome === 'failed') throw new Error('list poll failed');
  }, [fetchFirst, maxReload, pageSize]);
  useVisibilityPoll(tick, poll, enabled && poll != null);

  const setRows = useCallback((update: (rows: T[]) => T[]) => {
    setRowsState((prev) => (prev === null ? prev : update(prev)));
  }, []);

  const current = sameDeps(rowsDeps, deps);
  return {
    rows: current ? rows : null,
    total: current ? total : 0,
    loading: loading || (enabled && !current),
    error: current ? error : null,
    reload, loadMore, loadingMore,
    response: current ? response : null,
    loadedAt, setRows,
  };
}
