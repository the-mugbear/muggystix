import { useCallback, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';

import { GLOBAL, pollEvery, queryErrorText, useLastSettled } from '../lib/query';

/**
 * useListQuery — a list read a page at a time with "Show more", on
 * `useInfiniteQuery` (lib/query; UI_STYLE_GUIDE §39, §48).
 *
 *   const list = useListQuery(
 *     'listThings',
 *     ({ offset, limit, signal }) => listThings({ status, offset, limit }, signal),
 *     [status],
 *     { pageSize: 50, poll: 60_000 },
 *   );
 *   list.rows / list.total / list.loading / list.error
 *   list.reload()      // after a change, or the Refresh button
 *   list.loadMore()    // "Show more"
 *
 * `name` is the API function the fetcher calls; with `deps` it is the query
 * key (`[name, ...deps]`), so `deps` are plain values (strings, numbers,
 * plain objects).  What the page gets from this, beyond the query:
 *   - `rows` is `null` until a first page has loaded for the current `deps`
 *     — never the previous filter's rows — and a failed RELOAD keeps the rows
 *     that were shown, with `error` set: failed is not empty.
 *   - `reload()` and the poll re-read every page that "Show more" has loaded,
 *     so deciding a row far down the list does not drop the reader back to
 *     the top.  New `deps` start from the first page.
 *   - The background never takes what the reader asked for: the poll leaves a
 *     "Show more" in flight alone, and a "Show more" asked for during a
 *     re-read waits for it and appends after the fresh rows.
 *   - `response` is the first page's whole response, for a list endpoint
 *     that carries more than rows (a summary, counts); `lastResponse` is the
 *     last one of ANY deps, for what must not blink while the next list loads
 *     (filter chips and their counts, the options of a filter).
 *   - `loadMoreError` is the message of a failed "Show more", for a page that
 *     says it beside the list instead of in a toast (`loadMore` rejects all
 *     the same).
 *
 * Two options change what `rows` are, for the lists that need it:
 *   - `keepPrevious`: while new `deps` load — and when that read FAILS — the
 *     rows that were on screen stay (with their `total` and `response`), and
 *     `isPrevious` is true: they are not the answer to what is asked now.  The
 *     page dims them or makes them inert, and reads `isPrevious && error` as
 *     "this could not be loaded", never as the new list.
 *   - `dedupeBy`: a row whose key was already seen in an earlier page is
 *     dropped (offset paging over a list that moves can hand a row out twice).
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

export interface UseListQueryOptions<T = unknown> {
  /** Rows per page (and per "load more").  Default 50. */
  pageSize?: number;
  /** Re-read every N ms while the tab is visible. */
  poll?: number | null;
  /** False parks the hook: nothing is fetched and `rows` stays null. */
  enabled?: boolean;
  /** The fallback for `error` when the failure carries no message. */
  errorMessage?: string;
  /** The list is not one project's (lib/query `GLOBAL`): the key is
   *  `[GLOBAL, name, ...deps]`. */
  global?: boolean;
  /** While new `deps` load, and when that read fails, `rows` / `total` /
   *  `response` stay the previous deps' and `isPrevious` says so.  Default
   *  false: the rows are the current deps' or `null`. */
  keepPrevious?: boolean;
  /** A row's identity: a row whose key an earlier row already had is dropped
   *  from `rows`.  (`total` is the server's and is not changed by it.) */
  dedupeBy?: (row: T) => unknown;
}

export interface ListQuery<T, P extends ListPage<T> = ListPage<T>> {
  /** `null` until a first page has loaded for the current `deps` — or, with
   *  `keepPrevious`, the previous deps' rows until then (see `isPrevious`). */
  rows: T[] | null;
  total: number;
  /** A read of the loaded rows (first load, new deps, a reload, the poll) is in flight. */
  loading: boolean;
  /** Why the last read failed; cleared by the next success. */
  error: string | null;
  /** The failure behind `error`, as it was thrown — for a page that words it
   *  by what the reader was doing, or tells one failure from the next. */
  failure: unknown;
  /** Re-read the loaded rows in place.  Resolves when it settles. */
  reload: () => Promise<void>;
  /** Append the next page.  Rejects if it fails (the caller toasts, or shows
   *  `loadMoreError`). */
  loadMore: () => Promise<void>;
  loadingMore: boolean;
  /** Why the last "Show more" of THIS list failed; cleared when the next one
   *  is asked for, and by `reload()`. */
  loadMoreError: string | null;
  /** True when `rows` are not the current deps' (`keepPrevious`): the current
   *  list is still loading, or could not be loaded (`error`). */
  isPrevious: boolean;
  /** The first page's response, whole. */
  response: P | null;
  /** The last first-page response of ANY deps this list has shown. */
  lastResponse: P | null;
  /** When the rows were last read successfully. */
  loadedAt: Date | null;
  /** Replace loaded rows in place (a row the server just returned): each row
   *  is mapped where it is, so nothing moves between pages. */
  mapRows: (update: (row: T) => T) => void;
}

export function useListQuery<T, P extends ListPage<T> = ListPage<T>>(
  name: string,
  fetcher: ListFetcher<T, P>,
  deps: ReadonlyArray<unknown>,
  {
    pageSize = 50, poll = null, enabled = true, errorMessage = 'Could not load the list.', global: isGlobal = false,
    keepPrevious = false, dedupeBy,
  }: UseListQueryOptions<T> = {},
): ListQuery<T, P> {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => [...(isGlobal ? [GLOBAL] : []), name, ...deps, { pageSize }],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the key is its parts
    [isGlobal, name, JSON.stringify(deps), pageSize],
  );
  const query = useInfiniteQuery<P, unknown, InfiniteData<P, number>, unknown[], number>({
    queryKey,
    queryFn: ({ pageParam, signal }) => fetcher({ offset: pageParam, limit: pageSize, signal }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, page) => n + page.items.length, 0);
      return last.items.length > 0 && loaded < last.total ? loaded : undefined;
    },
    enabled,
    ...pollEvery(poll),
  });

  const pages = query.data?.pages;
  // The last pages any deps brought (this component's, and one project's:
  // `useLastSettled`).  A parked hook shows nothing, kept or not.
  const lastPages = useLastSettled(pages, { global: isGlobal });
  const shown = pages ?? (keepPrevious && enabled ? lastPages : undefined);
  const isPrevious = !pages && shown !== undefined;
  // The caller's function is new on every render; the rows are not.
  const identity = useRef(dedupeBy);
  identity.current = dedupeBy;
  const deduped = dedupeBy !== undefined;
  const rows = useMemo(() => {
    if (!shown) return null;
    const all = shown.flatMap((page) => page.items);
    const keyOf = deduped ? identity.current : undefined;
    if (!keyOf) return all;
    const seen = new Set<unknown>();
    return all.filter((row) => {
      const key = keyOf(row);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [shown, deduped]);
  const { refetch, fetchNextPage } = query;
  // A failed "Show more", and the list it was asked of.
  const [moreFailure, setMoreFailure] = useState<{ queryKey: unknown[]; error: unknown } | null>(null);
  // The list on screen NOW, for a continuation that waited (see `loadMore`).
  const live = useRef({ queryKey, enabled });
  live.current = { queryKey, enabled };

  // A parked hook asks for nothing, by any door (`refetch` ignores `enabled`).
  const reload = useCallback(async () => {
    if (!enabled) return;
    setMoreFailure(null);
    await refetch();
  }, [enabled, refetch]);
  const loadMore = useCallback(async () => {
    if (!enabled) return;
    setMoreFailure(null);
    // A re-read in flight (a reload after a change, the poll): let it land and
    // append after the rows it brings.  Asked for at once, "more" would cancel
    // it and add a page at an offset that no longer matches the list.
    const state = queryClient.getQueryState(queryKey);
    if (state?.fetchStatus === 'fetching' && !state.fetchMeta?.fetchMore) {
      await refetch({ cancelRefetch: false });
      // "More" was asked of THIS list.  If the filter changed (or the hook was
      // parked) while it waited, there is nothing to append to: the observer
      // now follows another list, and a next page asked of it would be one
      // nobody asked for.
      if (live.current.queryKey !== queryKey || !live.current.enabled) return;
    }
    const result = await fetchNextPage();
    if (result.isFetchNextPageError) {
      setMoreFailure({ queryKey, error: result.error });
      throw result.error;
    }
  }, [enabled, queryClient, queryKey, refetch, fetchNextPage]);

  const mapRows = useCallback((update: (row: T) => T) => {
    queryClient.setQueryData<InfiniteData<P, number>>(queryKey, (old) => (
      old && { ...old, pages: old.pages.map((page) => ({ ...page, items: page.items.map(update) })) }
    ));
  }, [queryClient, queryKey]);

  // A failed "Show more" is the caller's to say (it rejects); it is not a
  // failure of the list on screen.  Over the PREVIOUS list's rows there is no
  // next page to fail: what was asked for was the current list, and its
  // failure is the list's.
  const failure = query.isFetchNextPageError && !isPrevious ? null : (query.error ?? null);
  return {
    rows,
    total: shown?.length ? shown[shown.length - 1].total : 0,
    loading: query.isFetching && !query.isFetchingNextPage,
    error: queryErrorText(failure, errorMessage),
    failure,
    reload, loadMore,
    loadingMore: query.isFetchingNextPage,
    loadMoreError: moreFailure && moreFailure.queryKey === queryKey
      ? queryErrorText(moreFailure.error, errorMessage) : null,
    isPrevious,
    response: shown?.[0] ?? null,
    lastResponse: lastPages?.[0] ?? null,
    loadedAt: query.dataUpdatedAt ? new Date(query.dataUpdatedAt) : null,
    mapRows,
  };
}
