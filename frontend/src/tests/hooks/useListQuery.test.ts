/**
 * Review 2026-10-01 R33 / B2 — the one list-fetch hook.  Reproduced live on
 * Proposals: a slow "pending" response landed after the filter had changed
 * to "accepted" and replaced its rows, Accept buttons included.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../../hooks/useVisibilityPoll', () => ({ useVisibilityPoll: vi.fn() }));

import { ListPage, ListPageRequest, useListQuery } from '../../hooks/useListQuery';
import { useVisibilityPoll } from '../../hooks/useVisibilityPoll';

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

/** A server with 120 rows per filter. */
const server = (filter: string) => async ({ offset, limit }: ListPageRequest): Promise<ListPage<Row>> => ({
  items: rows(filter, offset + 1, Math.max(0, Math.min(limit, 120 - offset))), total: 120,
});

describe('useListQuery', () => {
  it('drops a slow response for an earlier filter — it never replaces the current rows', async () => {
    const pending = deferred<ListPage<Row>>();
    const accepted = deferred<ListPage<Row>>();
    const fetcher = vi.fn((filter: string) => (filter === 'pending' ? pending.promise : accepted.promise));
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>(() => fetcher(filter), [filter], { pageSize: 50 }),
      { initialProps: { filter: 'pending' } },
    );
    expect(result.current.rows).toBeNull();
    expect(result.current.loading).toBe(true);

    rerender({ filter: 'accepted' });
    await act(async () => { accepted.resolve({ items: rows('accepted', 1, 2), total: 2 }); });
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    // The first request answers late.
    await act(async () => { pending.resolve({ items: rows('pending', 1, 3), total: 3 }); });
    expect(result.current.rows?.map((r) => r.filter)).toEqual(['accepted', 'accepted']);
    expect(result.current.total).toBe(2);
    expect(result.current.loading).toBe(false);
  });

  it('never hands back the previous filter’s rows under the new filter, not even for one render', async () => {
    const seen: Array<{ filter: string; rows: Row[] | null }> = [];
    const { result, rerender } = renderHook(
      ({ filter }) => {
        const list = useListQuery<Row>(server(filter), [filter], { pageSize: 50 });
        seen.push({ filter, rows: list.rows });
        return list;
      },
      { initialProps: { filter: 'pending' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    rerender({ filter: 'accepted' });
    await waitFor(() => expect(result.current.rows?.[0]?.filter).toBe('accepted'));
    for (const s of seen) {
      for (const r of s.rows ?? []) expect(r.filter).toBe(s.filter);
    }
  });

  it('reports a failure as an error, not as an empty list, and recovers on reload', async () => {
    let fail = true;
    const fetcher = vi.fn(async (req: ListPageRequest) => {
      if (fail) throw new Error('boom');
      return server('a')(req);
    });
    const { result } = renderHook(() => useListQuery<Row>(fetcher, [], { errorMessage: 'Could not load the things.' }));
    await waitFor(() => expect(result.current.error).toBe('Could not load the things.'));
    expect(result.current.rows).toBeNull();      // not [] — nothing was loaded
    expect(result.current.loading).toBe(false);

    fail = false;
    await act(async () => { await result.current.reload(); });
    expect(result.current.error).toBeNull();
    expect(result.current.rows).toHaveLength(50);
  });

  it('keeps the loaded rows when a RELOAD fails, with the error beside them', async () => {
    let fail = false;
    const fetcher = vi.fn(async (req: ListPageRequest) => {
      if (fail) throw new Error('down');
      return server('a')(req);
    });
    const { result } = renderHook(() => useListQuery<Row>(fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    fail = true;
    await act(async () => { await result.current.reload(); });
    expect(result.current.error).toBe('Could not load the list.');
    expect(result.current.rows).toHaveLength(50);
  });

  it('loadMore appends the next page, and a reload re-reads every loaded row', async () => {
    const fetcher = vi.fn(server('a'));
    const { result } = renderHook(() => useListQuery<Row>(fetcher, [], { pageSize: 50 }));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));

    await act(async () => { await result.current.loadMore(); });
    expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, limit: 50 }));
    expect(result.current.rows?.map((r) => r.id)).toEqual(Array.from({ length: 100 }, (_, i) => i + 1));
    expect(result.current.loadingMore).toBe(false);

    // After a decision on row 73 the reader stays where they were.
    await act(async () => { await result.current.reload(); });
    expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, limit: 100 }));
    expect(result.current.rows).toHaveLength(100);
  });

  it('caps a reload at maxReload, and a new filter starts from the first page', async () => {
    const fetcher = vi.fn((filter: string, req: ListPageRequest) => server(filter)(req));
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>((req) => fetcher(filter, req), [filter], { pageSize: 50, maxReload: 75 }),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await act(async () => { await result.current.loadMore(); });
    await act(async () => { await result.current.reload(); });
    expect(fetcher).toHaveBeenLastCalledWith('a', expect.objectContaining({ offset: 0, limit: 75 }));

    rerender({ filter: 'b' });
    await waitFor(() => expect(result.current.rows?.[0]?.filter).toBe('b'));
    expect(fetcher).toHaveBeenLastCalledWith('b', expect.objectContaining({ offset: 0, limit: 50 }));
  });

  it('rejects a failed loadMore for the caller to say, and keeps the rows', async () => {
    let fail = false;
    const fetcher = vi.fn(async (req: ListPageRequest) => {
      if (fail) throw new Error('nope');
      return server('a')(req);
    });
    const { result } = renderHook(() => useListQuery<Row>(fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    fail = true;
    let thrown: unknown = null;
    await act(async () => { await result.current.loadMore().catch((e) => { thrown = e; }); });
    expect((thrown as Error).message).toBe('nope');
    expect(result.current.rows).toHaveLength(50);
    expect(result.current.loadingMore).toBe(false);
  });

  it('a "load more" superseded by a new filter appends nothing', async () => {
    const more = deferred<ListPage<Row>>();
    const fetcher = vi.fn((filter: string, req: ListPageRequest) =>
      (filter === 'a' && req.offset > 0 ? more.promise : server(filter)(req)));
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>((req) => fetcher(filter, req), [filter]),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    act(() => { void result.current.loadMore(); });
    rerender({ filter: 'b' });
    await waitFor(() => expect(result.current.rows?.[0]?.filter).toBe('b'));
    await act(async () => { more.resolve({ items: rows('a', 51, 50), total: 120 }); });
    expect(result.current.rows).toHaveLength(50);
    expect(result.current.rows?.every((r) => r.filter === 'b')).toBe(true);
    expect(result.current.loadingMore).toBe(false);
  });

  // Review 2026-10-01 M1 — the lanes are consistent.
  it('a "load more" asked for during a reload waits for it, appends after the fresh rows, and leaves no flag on', async () => {
    const reloadGate = deferred<ListPage<Row>>();
    let gated = false;
    const fetcher = vi.fn((req: ListPageRequest) =>
      (gated && req.offset === 0 ? reloadGate.promise : server('a')(req)));
    const { result } = renderHook(() => useListQuery<Row>(fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));

    gated = true;
    let reloaded!: Promise<void>;
    let more!: Promise<void>;
    act(() => { reloaded = result.current.reload(); });
    act(() => { more = result.current.loadMore(); });
    expect(result.current.loading).toBe(true);
    // The reload was not cancelled, and no page was asked for at a stale offset.
    expect(fetcher).toHaveBeenCalledTimes(2);
    // The reload brings one row fewer (one was decided meanwhile).
    await act(async () => {
      reloadGate.resolve({ items: rows('a', 1, 49), total: 119 });
      await reloaded;
      await more;
    });
    expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 49, limit: 50 }));
    expect(result.current.rows).toHaveLength(99);
    expect(result.current.loading).toBe(false);
    expect(result.current.loadingMore).toBe(false);
  });

  it('a first-page fetch superseded by a newer one leaves `loading` to the newer one, which clears it', async () => {
    const slow = deferred<ListPage<Row>>();
    let calls = 0;
    const fetcher = vi.fn((req: ListPageRequest) => {
      calls += 1;
      return calls === 2 ? slow.promise : server('a')(req);
    });
    const { result } = renderHook(() => useListQuery<Row>(fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    act(() => { void result.current.reload(); });   // slow, superseded below
    await act(async () => { await result.current.reload(); });
    expect(result.current.loading).toBe(false);
    await act(async () => { slow.resolve({ items: rows('stale', 1, 50), total: 120 }); });
    expect(result.current.loading).toBe(false);
    expect(result.current.rows?.every((r) => r.filter === 'a')).toBe(true);
  });

  it('the poll tick skips its turn while a "load more" is in flight', async () => {
    const moreGate = deferred<ListPage<Row>>();
    const fetcher = vi.fn((req: ListPageRequest) => (req.offset > 0 ? moreGate.promise : server('a')(req)));
    const { result } = renderHook(() => useListQuery<Row>(fetcher, [], { poll: 60_000 }));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    const pollCalls = vi.mocked(useVisibilityPoll).mock.calls;
    const tick = pollCalls[pollCalls.length - 1][0];

    let more!: Promise<void>;
    act(() => { more = result.current.loadMore(); });
    await act(async () => { await tick(); });
    expect(fetcher).toHaveBeenCalledTimes(2);  // first page + the load more; no tick
    await act(async () => { moreGate.resolve({ items: rows('a', 51, 50), total: 120 }); await more; });
    expect(result.current.rows).toHaveLength(100);
    expect(result.current.loadingMore).toBe(false);
    // …and runs again once the lane is free.
    await act(async () => { await tick(); });
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, limit: 100 }));
  });

  it('fetches nothing while disabled, and polls through the same lane when asked', async () => {
    const fetcher = vi.fn(server('a'));
    const { result, rerender } = renderHook(
      ({ enabled }) => useListQuery<Row>(fetcher, [], { enabled, poll: 60_000 }),
      { initialProps: { enabled: false } },
    );
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
    expect(vi.mocked(useVisibilityPoll)).toHaveBeenLastCalledWith(expect.any(Function), 60_000, false);

    rerender({ enabled: true });
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    const calls = vi.mocked(useVisibilityPoll).mock.calls;
    const [tick, interval, on] = calls[calls.length - 1];
    expect([interval, on]).toEqual([60_000, true]);
    await act(async () => { await tick(); });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
