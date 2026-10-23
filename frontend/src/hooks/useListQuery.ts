import { useCallback, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';

import { pollEvery, queryErrorText, useLastSettled } from '../lib/query';

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
 *     What stays when the NEW list's read fails is the caller's to state:
 *     `keepPrevious: 'loading'` keeps the previous rows only WHILE the new
 *     list loads — once that read has failed, `rows` are `null` and only the
 *     error is left to show (Scans, the session history: the owner's rule
 *     that a failed read on a new filter shows the error and Retry, not the
 *     filter that was left).  A retry of it is loading again.
 *   - `dedupeBy`: a row whose key was already seen in an earlier page is
 *     dropped (offset paging over a list that moves can hand a row out twice).
 *
 * And three things a list may need that are not rows:
 *   - a page that states its own end (`hasMore` on the page object — an
 *     endpoint that answers "there is more" and need not count, or has no
 *     total at all) decides whether a next page exists; without it the hook
 *     compares the rows loaded with `total`.  Either way the answer is
 *     `hasMore`.
 *   - `responses` is EVERY loaded page's response, in order, for a page made
 *     of more than its rows (the import history: an order plus the rows it
 *     names, and whether a part of them could not be read).
 *   - `retrying` is true while a read that FAILED with nothing to show is
 *     being tried again — the page keeps what it said went wrong instead of
 *     going back to its first-load skeleton.
 */
export interface ListPage<T> {
  items: T[];
  total: number;
}

/** A page of a list whose END the server states: `hasMore` decides whether a
 *  next page is offered, and a `total` is optional (not every list is
 *  counted). */
export interface OpenListPage<T> {
  items: T[];
  total?: number | null;
  hasMore: boolean;
}

/** Either kind of page. */
export type AnyListPage<T> = ListPage<T> | OpenListPage<T>;

export interface ListPageRequest {
  offset: number;
  limit: number;
  signal: AbortSignal;
}

export type ListFetcher<T, P extends AnyListPage<T> = ListPage<T>> = (request: ListPageRequest) => Promise<P>;

/** What the page states about its own end, if it does. */
const statedHasMore = (page: AnyListPage<unknown>): boolean | undefined => (
  'hasMore' in page && typeof page.hasMore === 'boolean' ? page.hasMore : undefined
);

export interface UseListQueryOptions<T = unknown> {
  /** Rows per page (and per "load more").  Default 50. */
  pageSize?: number;
  /** Re-read every N ms while the tab is visible. */
  poll?: number | null;
  /** False parks the hook: nothing is fetched and `rows` stays null. */
  enabled?: boolean;
  /** The fallback for `error` when the failure carries no message. */
  errorMessage?: string;
  /** What the kept answers belong to — the project, for one project's list
   *  (`within: projectId`).  `lastResponse` and the `keepPrevious` rows are
   *  forgotten when it changes, so a component that survives a project switch
   *  never shows one project's rows or counts under another. */
  within?: unknown;
  /** While new `deps` load, and when that read fails, `rows` / `total` /
   *  `response` stay the previous deps' and `isPrevious` says so.  Default
   *  false: the rows are the current deps' or `null`.  `'loading'`: only while
   *  they load — after a FAILED read of the new deps nothing is kept. */
  keepPrevious?: boolean | 'loading';
  /** A row's identity: a row whose key an earlier row already had is dropped
   *  from `rows`.  (`total` is the server's and is not changed by it.) */
  dedupeBy?: (row: T) => unknown;
}

export interface ListQuery<T, P extends AnyListPage<T> = ListPage<T>> {
  /** `null` until a first page has loaded for the current `deps` — or, with
   *  `keepPrevious`, the previous deps' rows until then (see `isPrevious`). */
  rows: T[] | null;
  /** The last loaded page's `total`; 0 when the list states none. */
  total: number;
  /** A next page exists for the list on screen: the last page said so
   *  (`hasMore`), or fewer rows are loaded than its `total`.  False over the
   *  previous deps' rows — there is nothing to append to. */
  hasMore: boolean;
  /** A read of the loaded rows (first load, new deps, a reload, the poll) is in flight. */
  loading: boolean;
  /** Why the last read failed; cleared by the next success. */
  error: string | null;
  /** The failure behind `error`, as it was thrown — for a page that words it
   *  by what the reader was doing, or tells one failure from the next. */
  failure: unknown;
  /** A read of the current `deps` that failed with nothing loaded is being
   *  tried again (`error` is null meanwhile: the page keeps its own words). */
  retrying: boolean;
  /** Re-read the loaded rows in place.  Resolves when it settles. */
  reload: () => Promise<void>;
  /** Append the next page.  Rejects if it fails (the caller toasts, or shows
   *  `loadMoreError`). */
  loadMore: () => Promise<void>;
  loadingMore: boolean;
  /** Why the last "Show more" of THIS list failed; cleared when the next one
   *  is asked for, and once the list is read again (`reload()`, a poll, an
   *  invalidation). */
  loadMoreError: string | null;
  /** True when `rows` are not the current deps' (`keepPrevious`): the current
   *  list is still loading, or could not be loaded (`error`). */
  isPrevious: boolean;
  /** The first page's response, whole. */
  response: P | null;
  /** Every loaded page's response, in order (the previous deps' under
   *  `isPrevious`); `null` when `rows` are. */
  responses: P[] | null;
  /** The last first-page response of ANY deps this list has shown. */
  lastResponse: P | null;
  /** When the rows were last read successfully. */
  loadedAt: Date | null;
  /** Replace loaded rows in place (a row the server just returned): each row
   *  is mapped where it is, so nothing moves between pages. */
  mapRows: (update: (row: T) => T) => void;
}

export function useListQuery<T, P extends AnyListPage<T> = ListPage<T>>(
  name: string,
  fetcher: ListFetcher<T, P>,
  deps: ReadonlyArray<unknown>,
  {
    pageSize = 50, poll = null, enabled = true, errorMessage = 'Could not load the list.', within,
    keepPrevious = false, dedupeBy,
  }: UseListQueryOptions<T> = {},
): ListQuery<T, P> {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => [name, ...deps, { pageSize }],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the key is its parts
    [name, JSON.stringify(deps), pageSize],
  );
  const query = useInfiniteQuery<P, unknown, InfiniteData<P, number>, unknown[], number>({
    queryKey,
    queryFn: ({ pageParam, signal }) => fetcher({ offset: pageParam, limit: pageSize, signal }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, page) => n + page.items.length, 0);
      // The server's own word, where the page carries it; else the count.
      const stated = statedHasMore(last);
      if (stated !== undefined) return stated ? loaded : undefined;
      return last.items.length > 0 && loaded < (last.total ?? 0) ? loaded : undefined;
    },
    enabled,
    ...pollEvery(poll),
  });

  const pages = query.data?.pages;
  // The last pages any deps brought (this component's: `useLastSettled`).  A
  // parked hook shows nothing, kept or not.
  const lastPages = useLastSettled(pages, { resetKey: within });
  // `'loading'`: a FAILED read of the current deps leaves nothing of the
  // previous ones (and a retry of it is loading again).
  const keptNow = keepPrevious === 'loading' ? !query.isError : keepPrevious;
  const shown = pages ?? (keptNow && enabled ? lastPages : undefined);
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
  // A failed "Show more", the list it was asked of, and the reading of that
  // list it failed on: once the list is read again — by `reload()`, the poll
  // or a write elsewhere invalidating it — the failure is no longer said.
  const [moreFailure, setMoreFailure] = useState<
    { queryKey: unknown[]; error: unknown; readAt: number } | null
  >(null);
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
      setMoreFailure({ queryKey, error: result.error, readAt: result.dataUpdatedAt });
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
    total: shown?.length ? shown[shown.length - 1].total ?? 0 : 0,
    hasMore: query.hasNextPage,
    loading: query.isFetching && !query.isFetchingNextPage,
    error: queryErrorText(failure, errorMessage),
    failure,
    retrying: query.isLoading && query.errorUpdateCount > 0,
    reload, loadMore,
    loadingMore: query.isFetchingNextPage,
    loadMoreError: moreFailure && moreFailure.queryKey === queryKey
      && moreFailure.readAt === query.dataUpdatedAt
      ? queryErrorText(moreFailure.error, errorMessage) : null,
    isPrevious,
    response: shown?.[0] ?? null,
    responses: shown ?? null,
    lastResponse: lastPages?.[0] ?? null,
    loadedAt: query.dataUpdatedAt ? new Date(query.dataUpdatedAt) : null,
    mapRows,
  };
}
