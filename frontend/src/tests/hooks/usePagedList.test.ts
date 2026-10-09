/**
 * usePagedList (5.331.0; on `useQuery` since 5.351.0) — one page of a list at
 * a time: "1–25 of N", previous / next.  Operations' tabs page through their
 * whole list with it.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { ListPage, ListPageRequest } from '../../hooks/useListQuery';
import { usePagedList } from '../../hooks/usePagedList';

type Row = { id: number; filter: string };
type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void };
const deferred = <T,>(): Deferred<T> => {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
const rows = (filter: string, from: number, n: number): Row[] =>
  Array.from({ length: n }, (_, i) => ({ id: from + i, filter }));

/** A server holding `size` rows per filter. */
const server = (filter: string, size: { n: number } = { n: 60 }) =>
  vi.fn(async ({ offset, limit }: ListPageRequest): Promise<ListPage<Row>> => ({
    items: rows(filter, offset + 1, Math.max(0, Math.min(limit, size.n - offset))), total: size.n,
  }));

describe('usePagedList', () => {
  it('loads the first page, and next asks the server for the next 25', async () => {
    const fetch = server('a');
    const { result } = renderHook(() => usePagedList<Row>('getRows', fetch, ['a']));
    await waitFor(() => expect(result.current.rows).toHaveLength(25));
    expect(fetch).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, limit: 25 }));
    expect(result.current.total).toBe(60);
    expect(result.current.page).toBe(0);

    act(() => result.current.setPage(1));
    // The previous page's rows are not shown as the new page's.
    expect(result.current.rows).toBeNull();
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.rows?.[0]?.id).toBe(26));
    expect(fetch).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 25, limit: 25 }));

    act(() => result.current.setPage(2));
    await waitFor(() => expect(result.current.rows?.map((r) => r.id)).toEqual(
      Array.from({ length: 10 }, (_, i) => 51 + i),
    ));
    expect(result.current.total).toBe(60);
  });

  it('drops a stale response: a slow page never replaces the page asked for after it', async () => {
    const slow = deferred<ListPage<Row>>();
    const fast = deferred<ListPage<Row>>();
    const fetch = vi.fn(({ offset }: ListPageRequest) => {
      if (offset === 0) return Promise.resolve({ items: rows('a', 1, 25), total: 75 });
      return offset === 25 ? slow.promise : fast.promise;
    });
    const { result } = renderHook(() => usePagedList<Row>('getRows', fetch, ['a']));
    await waitFor(() => expect(result.current.rows).toHaveLength(25));

    act(() => result.current.setPage(1));      // slow
    act(() => result.current.setPage(2));      // asked for after it
    await act(async () => { fast.resolve({ items: rows('a', 51, 25), total: 75 }); });
    await waitFor(() => expect(result.current.rows?.[0]?.id).toBe(51));
    // Page 2's answer arrives late.
    await act(async () => { slow.resolve({ items: rows('a', 26, 25), total: 75 }); });
    expect(result.current.rows?.[0]?.id).toBe(51);
    expect(result.current.page).toBe(2);
    expect(result.current.loading).toBe(false);
  });

  it('a new filter starts from the first page, with no request for the old page number', async () => {
    const a = server('a');
    const b = server('b');
    const { result, rerender } = renderHook(
      ({ filter }) => usePagedList<Row>('getRows', filter === 'a' ? a : b, [filter]),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(25));
    act(() => result.current.setPage(2));
    await waitFor(() => expect(result.current.rows?.[0]?.id).toBe(51));

    rerender({ filter: 'b' });
    expect(result.current.page).toBe(0);
    expect(result.current.rows).toBeNull();
    await waitFor(() => expect(result.current.rows?.[0]).toEqual({ id: 1, filter: 'b' }));
    expect(b.mock.calls.map((c) => c[0].offset)).toEqual([0]);
  });

  it('a failed read is an error and no rows — never an empty list', async () => {
    const fetch = vi.fn(async (): Promise<ListPage<Row>> => { throw new Error('HTTP 503'); });
    const { result } = renderHook(() => usePagedList<Row>('getRows', fetch, ['a'], { errorMessage: 'Could not load.' }));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.rows).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);     // no automatic retry
  });

  it('a failed RELOAD keeps the page that was shown, with the error beside it', async () => {
    let fail = false;
    const good = server('a');
    const fetch = vi.fn(async (req: ListPageRequest) => {
      if (fail) throw new Error('down');
      return good(req);
    });
    const { result } = renderHook(() => usePagedList<Row>('getRows', fetch, ['a']));
    await waitFor(() => expect(result.current.rows).toHaveLength(25));
    fail = true;
    await act(async () => { await result.current.reload(); });
    await waitFor(() => expect(result.current.error).toBe('Could not load the list.'));
    expect(result.current.rows).toHaveLength(25);
    expect(result.current.total).toBe(60);

    fail = false;
    await act(async () => { await result.current.reload(); });
    await waitFor(() => expect(result.current.error).toBeNull());
  });

  it('returning to an earlier filter starts from its first page, not the page that was left', async () => {
    const a = server('a');
    const b = server('b');
    const { result, rerender } = renderHook(
      ({ filter }) => usePagedList<Row>('getRows', filter === 'a' ? a : b, [filter]),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(25));
    act(() => result.current.setPage(2));
    await waitFor(() => expect(result.current.rows?.[0]?.id).toBe(51));
    rerender({ filter: 'b' });
    await waitFor(() => expect(result.current.rows?.[0]?.filter).toBe('b'));
    rerender({ filter: 'a' });
    expect(result.current.page).toBe(0);
    await waitFor(() => expect(result.current.rows?.[0]).toEqual({ id: 1, filter: 'a' }));
  });

  it('two lists of different API functions with the same deps do not answer each other', async () => {
    const { result } = renderHook(() => ({
      a: usePagedList<Row>('getA', server('a'), ['open']),
      b: usePagedList<Row>('getB', server('b'), ['open']),
    }));
    await waitFor(() => expect(result.current.b.rows).toHaveLength(25));
    await waitFor(() => expect(result.current.a.rows).toHaveLength(25));
    expect(result.current.a.rows?.[0]?.filter).toBe('a');
    expect(result.current.b.rows?.[0]?.filter).toBe('b');
  });

  it('reload re-reads the page on screen and keeps its rows until the new ones arrive', async () => {
    const fetch = server('a');
    const { result } = renderHook(() => usePagedList<Row>('getRows', fetch, ['a']));
    await waitFor(() => expect(result.current.rows).toHaveLength(25));
    act(() => result.current.setPage(1));
    await waitFor(() => expect(result.current.rows?.[0]?.id).toBe(26));

    const calls = fetch.mock.calls.length;
    let reloading!: Promise<void>;
    act(() => { reloading = result.current.reload(); });
    // Not blanked while it re-reads.
    expect(result.current.rows?.[0]?.id).toBe(26);
    await act(async () => { await reloading; });
    expect(fetch.mock.calls.length).toBe(calls + 1);
    expect(fetch).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 25, limit: 25 }));
    expect(result.current.page).toBe(1);
  });

  it('steps back when the page on screen no longer exists', async () => {
    const size = { n: 51 };
    const fetch = server('a', size);
    const { result } = renderHook(() => usePagedList<Row>('getRows', fetch, ['a']));
    await waitFor(() => expect(result.current.rows).toHaveLength(25));
    act(() => result.current.setPage(2));
    await waitFor(() => expect(result.current.rows).toHaveLength(1));

    // The last row of the last page was dealt with.
    size.n = 50;
    await act(async () => { await result.current.reload(); });
    await waitFor(() => expect(result.current.page).toBe(1));
    await waitFor(() => expect(result.current.rows?.[0]?.id).toBe(26));
    expect(result.current.total).toBe(50);
  });

  it('keeps the last response while the next page loads (filter chips must not blink)', async () => {
    const fetch = server('a');
    const { result } = renderHook(() => usePagedList<Row>('getRows', fetch, ['a']));
    await waitFor(() => expect(result.current.rows).toHaveLength(25));
    act(() => result.current.setPage(1));
    expect(result.current.response).toBeNull();
    expect(result.current.lastResponse?.total).toBe(60);
  });
});
