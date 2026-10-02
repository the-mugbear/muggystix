import { useCallback, useEffect, useRef, useState } from 'react';

import { type ListPage, type ListPageRequest, useListQuery } from './useListQuery';

/**
 * usePagedList — one page of a list at a time ("1–25 of N", previous / next),
 * on `useListQuery` (UI_STYLE_GUIDE §39, §42): the latest request wins, a
 * failed read is an error and never an empty list, and a reload keeps the
 * page the reader is on.
 *
 *   const list = usePagedList(
 *     ({ offset, limit, signal }) => getThings({ kind, offset, limit, signal }),
 *     [kind, refreshKey],
 *   );
 *   list.rows / list.total / list.page / list.setPage(n) / list.reload()
 *
 * New `deps` (a filter, the page's Refresh) start from the first page.  A page
 * that an action emptied — the last row of the last page was dealt with —
 * steps back to the last page that exists.
 */
export interface PagedList<T, P extends ListPage<T>> {
  /** `null` until this page has loaded (loading, or failed — see `error`). */
  rows: T[] | null;
  total: number;
  loading: boolean;
  error: string | null;
  /** Zero-based. */
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  /** Re-read the page on screen, keeping its rows until the new ones arrive. */
  reload: () => Promise<void>;
  /** This page's response, whole. */
  response: P | null;
  /** The last response of ANY page or filter — for what must not blink while
   *  the next page loads (filter chips and their counts). */
  lastResponse: P | null;
}

export function usePagedList<T, P extends ListPage<T> = ListPage<T>>(
  fetchPage: (request: ListPageRequest) => Promise<P>,
  deps: ReadonlyArray<unknown>,
  { pageSize = 25, errorMessage }: { pageSize?: number; errorMessage?: string } = {},
): PagedList<T, P> {
  // The page belongs to the deps it was chosen under: with new deps it is the
  // first page at once, with no render in which the old page number is asked
  // of the new list.
  const depsKey = JSON.stringify(deps);
  const [chosen, setChosen] = useState({ page: 0, key: depsKey });
  const page = chosen.key === depsKey ? chosen.page : 0;
  // …and it is forgotten with them: returning to an earlier filter (All →
  // one kind → All) starts from the first page too, not the page that was
  // left.  `page` is already 0 here, so this asks for nothing.
  useEffect(() => {
    setChosen((c) => (c.key === depsKey ? c : { page: 0, key: depsKey }));
  }, [depsKey]);
  const setPage = useCallback(
    (next: number) => setChosen({ page: Math.max(0, next), key: depsKey }),
    [depsKey],
  );

  const list = useListQuery<T, P>(
    ({ limit, signal }) => fetchPage({ offset: page * pageSize, limit, signal }),
    [depsKey, page],
    { pageSize, maxReload: pageSize, errorMessage },
  );

  // The page on screen no longer exists (its rows were dealt with).
  const { rows, total } = list;
  useEffect(() => {
    if (rows !== null && rows.length === 0 && total > 0 && page > 0) {
      setPage(Math.max(0, Math.ceil(total / pageSize) - 1));
    }
  }, [rows, total, page, pageSize, setPage]);

  const lastResponse = useRef<P | null>(null);
  if (list.response) lastResponse.current = list.response;

  return {
    rows, total,
    loading: list.loading,
    error: list.error,
    page, pageSize, setPage,
    reload: list.reload,
    response: list.response,
    lastResponse: lastResponse.current,
  };
}
