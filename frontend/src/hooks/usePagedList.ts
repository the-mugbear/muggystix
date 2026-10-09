import { useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { queryErrorText, useLastSettled } from '../lib/query';
import type { ListPage, ListPageRequest } from './useListQuery';
import type { UrlPage } from './useUrlPage';

/**
 * usePagedList — one page of a list at a time ("1–25 of N", previous / next),
 * on `useQuery` (lib/query; UI_STYLE_GUIDE §39, §42, §48): a failed read is
 * an error and never an empty list, and a reload keeps the page the reader is
 * on.  What this adds to the query is the PAGE: which one is asked for, where
 * it is kept, and when it goes back to the first.
 *
 *   const list = usePagedList(
 *     'getThings',
 *     ({ offset, limit, signal }) => getThings({ kind, offset, limit, signal }),
 *     [kind],
 *   );
 *   list.rows / list.total / list.page / list.setPage(n) / list.reload()
 *
 * `name` is the API function the fetcher calls; the query key is
 * `[name, ...deps, { page, pageSize }]`, so `deps` are plain values.
 *
 * New `deps` (a filter, the page's Refresh) start from the first page.  A page
 * that an action emptied — the last row of the last page was dealt with —
 * steps back to the last page that exists.
 *
 * A page's main list keeps its page in the address: pass
 * `{ page: useUrlPage() }` (`?page=`, left out for the first).
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
   *  the next page loads (filter chips and their counts).  This component's
   *  and one project's (lib/query `useLastSettled`). */
  lastResponse: P | null;
  /** When the page on screen was last read successfully. */
  loadedAt: Date | null;
}

export interface PagedListOptions {
  pageSize?: number;
  errorMessage?: string;
  /** Keep the page in the address instead of in the component
   *  (`useUrlPage()`): it survives a reload and Back from a row's own page. */
  page?: UrlPage;
  /** What `lastResponse` belongs to — the project, for one project's list
   *  (`within: projectId`): it is forgotten when this changes. */
  within?: unknown;
}

export function usePagedList<T, P extends ListPage<T> = ListPage<T>>(
  name: string,
  fetchPage: (request: ListPageRequest) => Promise<P>,
  deps: ReadonlyArray<unknown>,
  { pageSize = 25, errorMessage, page: url, within }: PagedListOptions = {},
): PagedList<T, P> {
  // The page belongs to the deps it was chosen under: with new deps it is the
  // first page at once, with no render in which the old page number is asked
  // of the new list.
  const depsKey = JSON.stringify(deps);
  const [chosen, setChosen] = useState({ page: 0, key: depsKey });
  // In the address, the page is the address's while the deps are the ones it
  // was read under.  New deps start from the first page — and take the page
  // out of the address — unless they came WITH an address the reader went
  // back or forward to, whose page is then the one they left.
  const [read, setRead] = useState({ key: depsKey, search: url?.search });
  const wentBack = !!url && url.byHistory && url.search !== read.search;
  const page = url
    ? (read.key === depsKey || wentBack ? url.page : 0)
    : (chosen.key === depsKey ? chosen.page : 0);
  // …and it is forgotten with them: returning to an earlier filter (All →
  // one kind → All) starts from the first page too, not the page that was
  // left.  `page` is already 0 here, so this asks for nothing.
  useEffect(() => {
    if (!url) {
      setChosen((c) => (c.key === depsKey ? c : { page: 0, key: depsKey }));
      return;
    }
    if (read.key !== depsKey && !wentBack && url.page !== 0) {
      // Runs again once the address has lost its page.
      url.setPage(0);
      return;
    }
    if (read.key !== depsKey || read.search !== url.search) setRead({ key: depsKey, search: url.search });
  }, [depsKey, url, read, wentBack]);
  const setUrlPage = url?.setPage;
  const setPage = useCallback(
    (next: number) => {
      if (setUrlPage) setUrlPage(Math.max(0, next));
      else setChosen({ page: Math.max(0, next), key: depsKey });
    },
    [depsKey, setUrlPage],
  );

  const query = useQuery<P>({
    // `depsKey` is the deps, already as one plain value.
    queryKey: [name, depsKey, { page, pageSize }],
    queryFn: ({ signal }) => fetchPage({ offset: page * pageSize, limit: pageSize, signal }),
  });
  const response = query.data ?? null;
  const rows = response ? response.items : null;
  const total = response ? response.total : 0;
  const { refetch } = query;
  const reload = useCallback(async () => { await refetch(); }, [refetch]);

  // The page on screen no longer exists (its rows were dealt with).
  useEffect(() => {
    if (rows !== null && rows.length === 0 && total > 0 && page > 0) {
      setPage(Math.max(0, Math.ceil(total / pageSize) - 1));
    }
  }, [rows, total, page, pageSize, setPage]);

  const lastResponse = useLastSettled(response, { resetKey: within }) ?? null;

  return {
    rows, total,
    loading: query.isFetching,
    error: queryErrorText(query.error, errorMessage ?? 'Could not load the list.'),
    page, pageSize, setPage,
    reload,
    response,
    lastResponse,
    loadedAt: query.dataUpdatedAt ? new Date(query.dataUpdatedAt) : null,
  };
}
