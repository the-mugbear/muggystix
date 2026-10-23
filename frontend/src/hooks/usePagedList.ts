import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

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
 *
 * Three options, for the lists that need them:
 *   - `keepAddressPage` (with `page`): use it where a LINK may name a filter
 *     and a page together (`/hosts?q=…&page=4`, a same-page link on the audit
 *     log) and must open that page.  The page is then the address's whenever
 *     the deps changed WITH the address — by a link, Back, or the page's own
 *     control — and the hook does not take it out.  So a control that changes
 *     a filter leaves `page` out of its ONE address write, which is how it
 *     starts from the first page (and in one request).  Deps that changed
 *     while the address did not (a Refresh counter, a value kept elsewhere)
 *     still return to the first page.
 *   - `enabled: false` parks the list: nothing is asked for, `rows` stay
 *     `null` and the address is not written — for a page that is about to
 *     replace its own address (Hosts putting a restored session's filters
 *     into it) and must not read the list of the address it is leaving.
 *   - `page` need not come from `useUrlPage()`: a page that writes its whole
 *     address in one place hands its own `{ page, setPage, search }`, and is
 *     then the one writer — the hook's only writes are `setPage` calls.
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
  /** When a read of the page on screen last failed — with `loadedAt`, "has the
   *  list been read since then?" for a message that lasts until it has. */
  failedAt: Date | null;
  /** Replace rows of the page on screen in place (a row the server just
   *  returned), with no second read. */
  mapRows: (update: (row: T) => T) => void;
}

export interface PagedListOptions {
  pageSize?: number;
  errorMessage?: string;
  /** Keep the page in the address instead of in the component
   *  (`useUrlPage()`): it survives a reload and Back from a row's own page. */
  page?: UrlPage;
  /** With `page`: an address that names a page is shown on that page, new
   *  filters or not (see the header).  Default false: new deps start from the
   *  first page unless the reader went Back / Forward to them. */
  keepAddressPage?: boolean;
  /** False parks the hook: nothing is fetched, `rows` stay null and the
   *  address is left alone.  Default true. */
  enabled?: boolean;
  /** What `lastResponse` belongs to — the project, for one project's list
   *  (`within: projectId`): it is forgotten when this changes. */
  within?: unknown;
}

export function usePagedList<T, P extends ListPage<T> = ListPage<T>>(
  name: string,
  fetchPage: (request: ListPageRequest) => Promise<P>,
  deps: ReadonlyArray<unknown>,
  { pageSize = 25, errorMessage, page: url, keepAddressPage = false, enabled = true, within }: PagedListOptions = {},
): PagedList<T, P> {
  const queryClient = useQueryClient();
  // The page belongs to the deps it was chosen under: with new deps it is the
  // first page at once, with no render in which the old page number is asked
  // of the new list.
  const depsKey = JSON.stringify(deps);
  const [chosen, setChosen] = useState({ page: 0, key: depsKey });
  // In the address, the page is the address's while the deps are the ones it
  // was read under.  New deps start from the first page — and take the page
  // out of the address — unless they came WITH an address whose page is the
  // reader's: one they went back or forward to (the page they left), or, under
  // `keepAddressPage`, any address at all (a link names its page; a control
  // of the page left it out).
  const [read, setRead] = useState({ key: depsKey, search: url?.search });
  const withItsAddress = !!url && url.search !== read.search && (keepAddressPage || !!url.byHistory);
  const page = url
    ? (read.key === depsKey || withItsAddress ? url.page : 0)
    : (chosen.key === depsKey ? chosen.page : 0);
  // …and it is forgotten with them: returning to an earlier filter (All →
  // one kind → All) starts from the first page too, not the page that was
  // left.  `page` is already 0 here, so this asks for nothing.
  useEffect(() => {
    if (!url) {
      setChosen((c) => (c.key === depsKey ? c : { page: 0, key: depsKey }));
      return;
    }
    // A parked list writes nothing: its address is someone else's to replace.
    if (!enabled) return;
    if (read.key !== depsKey && !withItsAddress && url.page !== 0) {
      // Runs again once the address has lost its page.
      url.setPage(0);
      return;
    }
    if (read.key !== depsKey || read.search !== url.search) setRead({ key: depsKey, search: url.search });
  }, [depsKey, url, read, withItsAddress, enabled]);
  const setUrlPage = url?.setPage;
  const setPage = useCallback(
    (next: number) => {
      if (setUrlPage) setUrlPage(Math.max(0, next));
      else setChosen({ page: Math.max(0, next), key: depsKey });
    },
    [depsKey, setUrlPage],
  );

  // The same shape as every other key (`[apiFn, projectId, …args]`), so a
  // write can address this list — or one page of it — apart from another
  // reader of the same function.  (It was the deps as one JSON string.)
  const queryKey = [name, ...deps, { page, pageSize }];
  const query = useQuery<P>({
    // eslint-disable-next-line @tanstack/query/exhaustive-deps -- the key is its parts; the fetcher's inputs are `deps`
    queryKey,
    queryFn: ({ signal }) => fetchPage({ offset: page * pageSize, limit: pageSize, signal }),
    enabled,
  });
  const response = query.data ?? null;
  const rows = response ? response.items : null;
  const total = response ? response.total : 0;
  const { refetch } = query;
  // A parked hook asks for nothing, by any door (`refetch` ignores `enabled`).
  const reload = useCallback(async () => {
    if (!enabled) return;
    await refetch();
  }, [enabled, refetch]);

  // The page on screen no longer exists (its rows were dealt with, or the
  // address named a page past the end).  A list emptied to nothing goes back
  // to the first page (review 2026-10-23b, FEC-9: it stayed on its number).
  useEffect(() => {
    if (rows !== null && rows.length === 0 && page > 0) {
      setPage(Math.max(0, Math.ceil(total / pageSize) - 1));
    }
  }, [rows, total, page, pageSize, setPage]);

  const lastResponse = useLastSettled(response, { resetKey: within }) ?? null;

  // The page on screen NOW, whenever the rows are mapped.
  const onScreen = useRef(queryKey);
  onScreen.current = queryKey;
  const mapRows = useCallback((update: (row: T) => T) => {
    queryClient.setQueryData<P>(onScreen.current, (old) => (old && { ...old, items: old.items.map(update) }));
  }, [queryClient]);

  return {
    rows, total,
    loading: query.isFetching,
    error: queryErrorText(query.error, errorMessage ?? 'Could not load the list.'),
    page, pageSize, setPage,
    reload,
    response,
    lastResponse,
    loadedAt: query.dataUpdatedAt ? new Date(query.dataUpdatedAt) : null,
    failedAt: query.errorUpdatedAt ? new Date(query.errorUpdatedAt) : null,
    mapRows,
  };
}
