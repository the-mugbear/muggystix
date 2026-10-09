/**
 * useListQuery — a list read a page at a time with "Show more", on
 * `useInfiniteQuery` (5.351.0).  What the hook's header promises a page:
 * rows of a previous filter are never returned (review 2026-10-01 R33 / B2 —
 * reproduced live on Proposals: a slow "pending" response landed after the
 * filter had changed to "accepted" and replaced its rows, Accept buttons
 * included), failed is not empty, a reload keeps the reader's place, and the
 * background never takes what the reader asked for.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ListPage, ListPageRequest, useListQuery } from '../../hooks/useListQuery';

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
const asked = (fetcher: { mock: { calls: unknown[][] } }, from = 0) =>
  fetcher.mock.calls.slice(from).map((call) => {
    const req = call[call.length - 1] as ListPageRequest;
    return `${req.offset}+${req.limit}`;
  });

/** Do it and let the readers hear of it: a query tells its observers in the
 *  task AFTER the one its request settles in. */
const doing = (work: () => Promise<unknown>) => act(async () => {
  await work();
  await new Promise((resolve) => { setTimeout(resolve, 0); });
});

afterEach(() => { vi.useRealTimers(); });

describe('useListQuery', () => {
  it('drops a slow response for an earlier filter — it never replaces the current rows', async () => {
    const pending = deferred<ListPage<Row>>();
    const accepted = deferred<ListPage<Row>>();
    const fetcher = vi.fn((filter: string) => (filter === 'pending' ? pending.promise : accepted.promise));
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>('listRows', () => fetcher(filter), [filter], { pageSize: 50 }),
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
        const list = useListQuery<Row>('listRows', server(filter), [filter], { pageSize: 50 });
        seen.push({ filter, rows: list.rows });
        return list;
      },
      { initialProps: { filter: 'pending' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    rerender({ filter: 'accepted' });
    expect(result.current.rows).toBeNull();
    expect(result.current.total).toBe(0);
    expect(result.current.response).toBeNull();
    await waitFor(() => expect(result.current.rows?.[0]?.filter).toBe('accepted'));
    for (const s of seen) {
      for (const r of s.rows ?? []) expect(r.filter).toBe(s.filter);
    }
  });

  it('two lists of different API functions with the same deps do not answer each other', async () => {
    const { result } = renderHook(() => ({
      a: useListQuery<Row>('listA', server('a'), ['open']),
      b: useListQuery<Row>('listB', server('b'), ['open']),
    }));
    await waitFor(() => expect(result.current.b.rows).toHaveLength(50));
    await waitFor(() => expect(result.current.a.rows).toHaveLength(50));
    expect(result.current.a.rows?.every((r) => r.filter === 'a')).toBe(true);
    expect(result.current.b.rows?.every((r) => r.filter === 'b')).toBe(true);
  });

  it('reports a failure as an error, not as an empty list, and recovers on reload', async () => {
    let fail = true;
    const fetcher = vi.fn(async (req: ListPageRequest) => {
      if (fail) throw new Error('boom');
      return server('a')(req);
    });
    const { result } = renderHook(
      () => useListQuery<Row>('listRows', fetcher, [], { errorMessage: 'Could not load the things.' }),
    );
    await waitFor(() => expect(result.current.error).toBe('Could not load the things.'));
    expect(result.current.rows).toBeNull();      // not [] — nothing was loaded
    expect(result.current.loading).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);    // no automatic retry

    fail = false;
    await doing(() => result.current.reload());
    expect(result.current.error).toBeNull();
    expect(result.current.rows).toHaveLength(50);
  });

  it('keeps the loaded rows when a RELOAD fails, with the error beside them', async () => {
    let fail = false;
    const fetcher = vi.fn(async (req: ListPageRequest) => {
      if (fail) throw new Error('down');
      return server('a')(req);
    });
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    fail = true;
    await doing(() => result.current.reload());
    expect(result.current.error).toBe('Could not load the list.');
    expect(result.current.rows).toHaveLength(50);
    expect(result.current.total).toBe(120);

    // …and the next success clears it.
    fail = false;
    await doing(() => result.current.reload());
    expect(result.current.error).toBeNull();
  });

  it('loadMore appends the next page, and a reload re-reads every loaded page', async () => {
    const fetcher = vi.fn(server('a'));
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, [], { pageSize: 50 }));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    expect(result.current.loadedAt).toBeInstanceOf(Date);

    await doing(() => result.current.loadMore());
    expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, limit: 50 }));
    expect(result.current.rows?.map((r) => r.id)).toEqual(Array.from({ length: 100 }, (_, i) => i + 1));
    expect(result.current.total).toBe(120);
    expect(result.current.loadingMore).toBe(false);

    // After a decision on row 73 the reader stays where they were: both
    // loaded pages are read again, a page at a time.
    const before = fetcher.mock.calls.length;
    await doing(() => result.current.reload());
    expect(asked(fetcher, before)).toEqual(['0+50', '50+50']);
    expect(result.current.rows).toHaveLength(100);
  });

  it('a reload that brings fewer rows asks for the next page where the fresh rows end', async () => {
    let size = 120;
    const fetcher = vi.fn(async ({ offset, limit }: ListPageRequest): Promise<ListPage<Row>> => ({
      // The first page is one row short after the decision (the server's page
      // boundary moved): the second page starts at 49, not 50.
      items: rows('a', offset + 1, Math.max(0, Math.min(offset === 0 && size < 120 ? limit - 1 : limit, size - offset))),
      total: size,
    }));
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());

    size = 119;
    const before = fetcher.mock.calls.length;
    await doing(() => result.current.reload());
    expect(asked(fetcher, before)).toEqual(['0+50', '49+50']);
    expect(result.current.rows?.map((r) => r.id)).toEqual(Array.from({ length: 99 }, (_, i) => i + 1));
    expect(result.current.total).toBe(119);
  });

  it('a new filter starts from the first page, whatever was loaded under the old one', async () => {
    const fetcher = vi.fn((filter: string, req: ListPageRequest) => server(filter)(req));
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>('listRows', (req) => fetcher(filter, req), [filter], { pageSize: 50 }),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());
    expect(result.current.rows).toHaveLength(100);

    rerender({ filter: 'b' });
    await waitFor(() => expect(result.current.rows?.[0]?.filter).toBe('b'));
    expect(result.current.rows).toHaveLength(50);
    expect(fetcher.mock.calls.filter(([filter]) => filter === 'b').map(([, req]) => req.offset)).toEqual([0]);
  });

  it('offers no further page once every row is loaded', async () => {
    const fetcher = vi.fn(async ({ offset, limit }: ListPageRequest): Promise<ListPage<Row>> => ({
      items: rows('a', offset + 1, Math.max(0, Math.min(limit, 60 - offset))), total: 60,
    }));
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());
    expect(result.current.rows).toHaveLength(60);
    await doing(() => result.current.loadMore());
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.rows).toHaveLength(60);
  });

  it('rejects a failed loadMore for the caller to say, keeps the rows, and sets no error', async () => {
    let fail = false;
    const fetcher = vi.fn(async (req: ListPageRequest) => {
      if (fail) throw new Error('nope');
      return server('a')(req);
    });
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    fail = true;
    let thrown: unknown = null;
    await doing(() => result.current.loadMore().catch((e) => { thrown = e; }));
    expect((thrown as Error).message).toBe('nope');
    expect(result.current.rows).toHaveLength(50);
    expect(result.current.loadingMore).toBe(false);
    // Not a failure of the list on screen.
    expect(result.current.error).toBeNull();

    // The next "Show more" can succeed.
    fail = false;
    await doing(() => result.current.loadMore());
    expect(result.current.rows).toHaveLength(100);
    expect(result.current.error).toBeNull();
  });

  it('a "load more" superseded by a new filter appends nothing', async () => {
    const more = deferred<ListPage<Row>>();
    const fetcher = vi.fn((filter: string, req: ListPageRequest) =>
      (filter === 'a' && req.offset > 0 ? more.promise : server(filter)(req)));
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>('listRows', (req) => fetcher(filter, req), [filter]),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    act(() => { void result.current.loadMore().catch(() => undefined); });
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
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));

    gated = true;
    let reloaded!: Promise<void>;
    let more!: Promise<void>;
    act(() => { reloaded = result.current.reload(); });
    act(() => { more = result.current.loadMore(); });
    await waitFor(() => expect(result.current.loading).toBe(true));
    // The reload was not cancelled, and no page was asked for at a stale offset.
    expect(fetcher).toHaveBeenCalledTimes(2);
    // The reload brings one row fewer (one was decided meanwhile).
    await doing(async () => {
      reloadGate.resolve({ items: rows('a', 1, 49), total: 119 });
      await reloaded;
      await more;
    });
    expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 49, limit: 50 }));
    expect(result.current.rows).toHaveLength(99);
    expect(result.current.loading).toBe(false);
    expect(result.current.loadingMore).toBe(false);
  });

  it('a reload asked for again while one is in flight shows the newer answer, and `loading` ends', async () => {
    const slow = deferred<ListPage<Row>>();
    let calls = 0;
    const fetcher = vi.fn((req: ListPageRequest) => {
      calls += 1;
      return calls === 2 ? slow.promise : server('a')(req);
    });
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    act(() => { void result.current.reload(); });   // slow, superseded below
    await doing(() => result.current.reload());
    expect(result.current.loading).toBe(false);
    await act(async () => { slow.resolve({ items: rows('stale', 1, 50), total: 120 }); });
    expect(result.current.loading).toBe(false);
    expect(result.current.rows?.every((r) => r.filter === 'a')).toBe(true);
  });

  it('mapRows replaces a row in place — on the second page too — and moves nothing', async () => {
    const { result } = renderHook(() => useListQuery<Row>('listRows', server('a'), []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());
    const before = result.current.rows!.map((r) => r.id);

    await doing(async () => result.current.mapRows((r) => (r.id === 73 ? { ...r, filter: 'patched' } : r)));
    expect(result.current.rows).toHaveLength(100);
    expect(result.current.rows?.find((r) => r.id === 73)?.filter).toBe('patched');
    expect(result.current.rows!.map((r) => r.id)).toEqual(before);
    expect(result.current.rows!.filter((r) => r.filter === 'patched')).toHaveLength(1);
  });

  // Code review 2026-10-09: "more" waits for a re-read in flight; if the filter
  // changes while it waits, it must not ask the NEW list for a second page.
  it('a "Show more" that was waiting for a reload does nothing once the filter has changed', async () => {
    const slow = deferred<ListPage<Row>>();
    const asked: Array<{ filter: string; offset: number }> = [];
    let slowNext = false;
    const fetcherFor = (filter: string) => (req: ListPageRequest) => {
      asked.push({ filter, offset: req.offset });
      if (filter === 'a' && slowNext) { slowNext = false; return slow.promise; }
      return server(filter)(req);
    };
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>('listRows', fetcherFor(filter), [filter]),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));

    slowNext = true;
    act(() => { void result.current.reload(); });        // the slow re-read of "a"
    let more!: Promise<void>;
    act(() => { more = result.current.loadMore(); });    // waits for it
    rerender({ filter: 'b' });                           // the reader changes the filter
    await waitFor(() => expect(result.current.rows?.[0].filter).toBe('b'));
    await act(async () => { slow.resolve({ items: rows('a', 1, 50), total: 120 }); await more; });

    expect(asked.filter((q) => q.filter === 'b')).toEqual([{ filter: 'b', offset: 0 }]);
    expect(result.current.rows).toHaveLength(50);
    expect(result.current.loadingMore).toBe(false);
  });

  it('gives the first page’s whole response, for an endpoint that carries more than rows', async () => {
    type Page = ListPage<Row> & { summary: string };
    const fetcher = async (req: ListPageRequest): Promise<Page> => ({
      ...(await server('a')(req)), summary: `from ${req.offset}`,
    });
    const { result } = renderHook(() => useListQuery<Row, Page>('listRows', fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());
    expect(result.current.response?.summary).toBe('from 0');
  });

  it('the poll re-reads the loaded pages, and leaves a "load more" in flight alone', async () => {
    vi.useFakeTimers();
    const moreGate = deferred<ListPage<Row>>();
    let gated = true;
    const fetcher = vi.fn((req: ListPageRequest) => (gated && req.offset > 0 ? moreGate.promise : server('a')(req)));
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, [], { poll: 60_000 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.rows).toHaveLength(50);

    let more!: Promise<void>;
    act(() => { more = result.current.loadMore(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetcher).toHaveBeenCalledTimes(2);  // first page + the load more; the tick asked for nothing
    expect(result.current.loadingMore).toBe(true);
    await act(async () => {
      moreGate.resolve({ items: rows('a', 51, 50), total: 120 });
      await more;
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.rows).toHaveLength(100);
    expect(result.current.loadingMore).toBe(false);

    // …and reads every loaded page once the lane is free.
    gated = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(60_001); });
    expect(asked(fetcher, 2)).toEqual(['0+50', '50+50']);
    expect(result.current.rows).toHaveLength(100);
  });

  it('polls half as often while the server is failing, and does not poll unless asked', async () => {
    vi.useFakeTimers();
    let fail = false;
    const polled = vi.fn(async (req: ListPageRequest) => {
      if (fail) throw new Error('down');
      return server('a')(req);
    });
    const quiet = vi.fn(server('a'));
    const { result } = renderHook(() => ({
      polled: useListQuery<Row>('listPolled', polled, [], { poll: 60_000 }),
      quiet: useListQuery<Row>('listQuiet', quiet, []),
    }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.polled.rows).toHaveLength(50);

    fail = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(60_001); });
    expect(polled).toHaveBeenCalledTimes(2);
    expect(result.current.polled.error).toBe('Could not load the list.');
    expect(result.current.polled.rows).toHaveLength(50);     // failed is not empty
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(polled).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_001); });
    expect(polled).toHaveBeenCalledTimes(3);
    expect(quiet).toHaveBeenCalledTimes(1);
  });

  it('fetches nothing while disabled — not on reload, not on "load more", not on the poll', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(server('a'));
    const { result, rerender } = renderHook(
      ({ enabled }) => useListQuery<Row>('listRows', fetcher, [], { enabled, poll: 60_000 }),
      { initialProps: { enabled: false } },
    );
    expect(result.current.loading).toBe(false);
    expect(result.current.rows).toBeNull();
    await act(async () => {
      await result.current.reload();
      await result.current.loadMore();
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(fetcher).not.toHaveBeenCalled();
    expect(result.current.rows).toBeNull();

    rerender({ enabled: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.rows).toHaveLength(50);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

// What Scopes and a test's evidence read `useInfiniteQuery` directly for:
// the rows under a search box stay while the next search loads.
describe('useListQuery — keepPrevious', () => {
  it('keeps the previous filter’s rows while the new one loads, and says they are the previous ones', async () => {
    type Page = ListPage<Row> & { summary: string };
    const slow = deferred<Page>();
    const fetcher = (filter: string) => async (req: ListPageRequest): Promise<Page> => (
      filter === 'b' ? slow.promise : { ...(await server(filter)(req)), summary: `of ${filter}` }
    );
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row, Page>('listRows', fetcher(filter), [filter], { keepPrevious: true }),
      { initialProps: { filter: 'a' } },
    );
    // Nothing was shown before the first list: nothing is kept.
    expect(result.current.rows).toBeNull();
    expect(result.current.isPrevious).toBe(false);
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());
    expect(result.current.rows).toHaveLength(100);
    expect(result.current.isPrevious).toBe(false);

    rerender({ filter: 'b' });
    // Every loaded row of "a" is still there — with its total and response —
    // and the page is told it is not the answer to "b".
    expect(result.current.rows).toHaveLength(100);
    expect(result.current.rows?.every((r) => r.filter === 'a')).toBe(true);
    expect(result.current.total).toBe(120);
    expect(result.current.response?.summary).toBe('of a');
    expect(result.current.isPrevious).toBe(true);
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();

    await doing(async () => { slow.resolve({ items: rows('b', 1, 2), total: 2, summary: 'of b' }); });
    expect(result.current.rows?.map((r) => r.filter)).toEqual(['b', 'b']);
    expect(result.current.total).toBe(2);
    expect(result.current.response?.summary).toBe('of b');
    expect(result.current.isPrevious).toBe(false);
  });

  it('a FAILED new filter is an error over the previous rows — never the new answer', async () => {
    let fail = true;
    const down = new Error('down');
    const fetcher = (filter: string) => async (req: ListPageRequest) => {
      if (filter === 'b' && fail) throw down;
      return server(filter)(req);
    };
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>('listRows', fetcher(filter), [filter], {
        keepPrevious: true, errorMessage: 'Could not load the things.',
      }),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));

    rerender({ filter: 'b' });
    await waitFor(() => expect(result.current.error).toBe('Could not load the things.'));
    expect(result.current.failure).toBe(down);
    expect(result.current.loading).toBe(false);
    // The rows on screen are still "a"'s, and flagged: a page that reads
    // `isPrevious && error` cannot take them for the list it asked for.
    expect(result.current.isPrevious).toBe(true);
    expect(result.current.rows?.every((r) => r.filter === 'a')).toBe(true);

    // Retry reads the list that was asked for, not the one on screen.
    fail = false;
    await doing(() => result.current.reload());
    expect(result.current.error).toBeNull();
    expect(result.current.isPrevious).toBe(false);
    expect(result.current.rows?.every((r) => r.filter === 'b')).toBe(true);
  });

  it('a failed RELOAD of the list on screen is not "previous": the rows are that list’s own', async () => {
    let fail = false;
    const fetcher = async (req: ListPageRequest) => {
      if (fail) throw new Error('down');
      return server('a')(req);
    };
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, [], { keepPrevious: true }));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    fail = true;
    await doing(() => result.current.reload());
    expect(result.current.error).toBe('Could not load the list.');
    expect(result.current.rows).toHaveLength(50);
    expect(result.current.isPrevious).toBe(false);
  });

  it('a parked hook shows nothing, kept or not', async () => {
    const { result, rerender } = renderHook(
      ({ enabled }) => useListQuery<Row>('listRows', server('a'), [enabled ? 'on' : 'off'], { enabled, keepPrevious: true }),
      { initialProps: { enabled: true } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    rerender({ enabled: false });
    expect(result.current.rows).toBeNull();
    expect(result.current.isPrevious).toBe(false);
  });

  it('never keeps one project’s rows under another project (`within`)', async () => {
    const never = new Promise<ListPage<Row>>(() => undefined);
    const { result, rerender } = renderHook(
      ({ project }) => useListQuery<Row>(
        'listRows', project === 1 ? server('project 1') : () => never, [project],
        { keepPrevious: true, within: project },
      ),
      { initialProps: { project: 1 } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));

    // The component survives the switch: the project is a dep like a filter,
    // and `within` says the kept rows were the other project's.
    rerender({ project: 2 });
    expect(result.current.rows).toBeNull();
    expect(result.current.isPrevious).toBe(false);
    expect(result.current.lastResponse).toBeNull();
  });
});

describe('useListQuery — lastResponse, loadMoreError, dedupeBy', () => {
  it('lastResponse is the last first page of ANY filter, while `response` is the current one’s or null', async () => {
    type Page = ListPage<Row> & { chips: string };
    const slow = deferred<Page>();
    const fetcher = (filter: string) => async (req: ListPageRequest): Promise<Page> => (
      filter === 'b' ? slow.promise : { ...(await server(filter)(req)), chips: `chips of ${filter} from ${req.offset}` }
    );
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row, Page>('listRows', fetcher(filter), [filter]),
      { initialProps: { filter: 'a' } },
    );
    expect(result.current.lastResponse).toBeNull();
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());
    expect(result.current.lastResponse?.chips).toBe('chips of a from 0');

    rerender({ filter: 'b' });
    // The rows are gone (no `keepPrevious`), the chips are not.
    expect(result.current.rows).toBeNull();
    expect(result.current.response).toBeNull();
    expect(result.current.isPrevious).toBe(false);
    expect(result.current.lastResponse?.chips).toBe('chips of a from 0');

    await doing(async () => { slow.resolve({ items: rows('b', 1, 1), total: 1, chips: 'chips of b' }); });
    expect(result.current.lastResponse?.chips).toBe('chips of b');
  });

  it('loadMoreError says a failed "Show more" — of this list only — until the next attempt or a reload', async () => {
    let fail = false;
    const gate = deferred<void>();
    let gated = false;
    const fetcher = (filter: string) => async (req: ListPageRequest) => {
      if (req.offset > 0 && gated) await gate.promise;
      if (req.offset > 0 && fail) throw new Error('nope');
      return server(filter)(req);
    };
    const { result, rerender } = renderHook(
      ({ filter }) => useListQuery<Row>('listRows', fetcher(filter), [filter], { errorMessage: 'Could not load the things.' }),
      { initialProps: { filter: 'a' } },
    );
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    expect(result.current.loadMoreError).toBeNull();

    fail = true;
    let thrown: unknown = null;
    await doing(() => result.current.loadMore().catch((e) => { thrown = e; }));
    // It still rejects, for the callers that toast…
    expect((thrown as Error).message).toBe('nope');
    // …and the page that says it beside the list has the message.
    expect(result.current.loadMoreError).toBe('Could not load the things.');
    expect(result.current.error).toBeNull();
    expect(result.current.rows).toHaveLength(50);

    // It was "a"'s: under another filter there is nothing to say, and back
    // on "a" it is still what happened there.
    rerender({ filter: 'b' });
    expect(result.current.loadMoreError).toBeNull();
    rerender({ filter: 'a' });
    await waitFor(() => expect(result.current.rows).toHaveLength(50));

    // The next attempt clears it at once, before it answers.
    gated = true;
    let more!: Promise<void>;
    act(() => { more = result.current.loadMore().catch(() => undefined); });
    await waitFor(() => expect(result.current.loadingMore).toBe(true));
    expect(result.current.loadMoreError).toBeNull();
    await doing(async () => { gate.resolve(); await more; });
    expect(result.current.loadMoreError).toBe('Could not load the things.');

    // A reload (the page's Retry) clears it too.
    gated = false;
    await doing(() => result.current.reload());
    expect(result.current.loadMoreError).toBeNull();

    fail = false;
    await doing(() => result.current.loadMore());
    expect(result.current.loadMoreError).toBeNull();
    expect(result.current.rows).toHaveLength(100);
  });

  it('dedupeBy drops a row an earlier page already brought, and pages on by what the server sent', async () => {
    // Between the two reads a row was added at the top: the second page
    // starts with the row that ended the first.
    const fetcher = vi.fn(async ({ offset, limit }: ListPageRequest): Promise<ListPage<Row>> => ({
      items: rows('a', offset === 0 ? 1 : offset, Math.min(limit, 120 - offset)), total: 120,
    }));
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, [], { dedupeBy: (row) => row.id }));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());
    // Rows 1–50, then 50–99: 50 is listed once.
    expect(result.current.rows?.map((r) => r.id)).toEqual(Array.from({ length: 99 }, (_, i) => i + 1));
    expect(result.current.total).toBe(120);

    // The same rows give the same array: the caller's function is new on
    // every render, the list is not.
    const before = result.current.rows;
    await doing(async () => undefined);
    expect(result.current.rows).toBe(before);

    // The next page is asked for after the 100 rows the server sent, not
    // after the 99 that are shown.
    await doing(() => result.current.loadMore());
    expect(asked(fetcher)).toEqual(['0+50', '50+50', '100+50']);
  });

  it('without dedupeBy a repeated row is listed twice, as before', async () => {
    const fetcher = async ({ offset, limit }: ListPageRequest): Promise<ListPage<Row>> => ({
      items: rows('a', offset === 0 ? 1 : offset, Math.min(limit, 120 - offset)), total: 120,
    });
    const { result } = renderHook(() => useListQuery<Row>('listRows', fetcher, []));
    await waitFor(() => expect(result.current.rows).toHaveLength(50));
    await doing(() => result.current.loadMore());
    expect(result.current.rows).toHaveLength(100);
    expect(result.current.rows?.filter((r) => r.id === 50)).toHaveLength(2);
  });
});
