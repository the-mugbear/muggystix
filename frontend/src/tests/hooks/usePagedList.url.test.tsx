/**
 * usePagedList with its page in the address (`useUrlPage`, UI_STYLE_GUIDE §45):
 * `?page=` (1-based, left out for the first page) survives a reload and Back;
 * a new filter still starts from the first page.
 */
import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { createMemoryRouter, RouterProvider, useSearchParams } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { ListPage, ListPageRequest } from '../../hooks/useListQuery';
import { usePagedList } from '../../hooks/usePagedList';
import { pageFromParams, useUrlPage } from '../../hooks/useUrlPage';

type Row = { id: number; filter: string };

const fetchRows = vi.fn(async (filter: string, { offset, limit }: ListPageRequest): Promise<ListPage<Row>> => ({
  items: Array.from({ length: Math.max(0, Math.min(limit, 60 - offset)) }, (_, i) => ({ id: offset + i + 1, filter })),
  total: 60,
}));

let list!: ReturnType<typeof usePagedList<Row>>;
const Page: React.FC<{ refreshKey?: number }> = ({ refreshKey = 0 }) => {
  const [params] = useSearchParams();
  const filter = params.get('f') ?? 'a';
  list = usePagedList<Row>('getRows', (req) => fetchRows(filter, req), [filter, refreshKey], { page: useUrlPage() });
  return <p data-testid="first">{list.rows ? `${list.rows[0]?.filter}:${list.rows[0]?.id}` : 'loading'}</p>;
};

const open = (entry: string, element: React.ReactElement = <Page />) => {
  fetchRows.mockClear();
  const router = createMemoryRouter([{ path: '/list', element }], { initialEntries: [entry] });
  const view = render(<RouterProvider router={router} />);
  return { router, view };
};
const first = () => screen.getByTestId('first').textContent;
const offsets = () => fetchRows.mock.calls.map(([filter, req]) => `${filter}@${req.offset}`);

describe('usePagedList — the page in the address', () => {
  it('reads the page the address names, on the first request', async () => {
    open('/list?page=3');
    await waitFor(() => expect(first()).toBe('a:51'));
    expect(list.page).toBe(2);
    expect(offsets()).toEqual(['a@50']);
  });

  it('writes the page as a replacement, 1-based, and leaves it out for the first page', async () => {
    const { router } = open('/list?f=a');
    await waitFor(() => expect(first()).toBe('a:1'));
    act(() => list.setPage(1));
    await waitFor(() => expect(first()).toBe('a:26'));
    expect(router.state.location.search).toBe('?f=a&page=2');
    expect(router.state.historyAction).toBe('REPLACE');
    act(() => list.setPage(0));
    await waitFor(() => expect(first()).toBe('a:1'));
    expect(router.state.location.search).toBe('?f=a');
  });

  it('a new filter starts from the first page and takes the page out of the address, with no request for the old page', async () => {
    const { router } = open('/list?f=a&page=3');
    await waitFor(() => expect(first()).toBe('a:51'));
    // The page changes its filter and leaves `page` alone.
    await act(async () => { await router.navigate('/list?f=b&page=3'); });
    await waitFor(() => expect(first()).toBe('b:1'));
    await waitFor(() => expect(router.state.location.search).toBe('?f=b'));
    expect(offsets()).toEqual(['a@50', 'b@0']);
  });

  it('Back returns to the filter that was left, on the page that was left', async () => {
    const { router } = open('/list?f=a&page=3');
    await waitFor(() => expect(first()).toBe('a:51'));
    await act(async () => { await router.navigate('/list?f=b&page=3'); });
    await waitFor(() => expect(first()).toBe('b:1'));
    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(first()).toBe('a:51'));
    expect(router.state.location.search).toBe('?f=a&page=3');
  });

  it('a refresh of the same list starts from the first page', async () => {
    let refresh: () => void = () => {};
    const Refreshable: React.FC = () => {
      const [key, setKey] = React.useState(0);
      refresh = () => setKey((n) => n + 1);
      return <Page refreshKey={key} />;
    };
    // Opened at this address (so the last navigation is a history one), then
    // a dep that is not in the address changes.
    const { router } = open('/list?page=3', <Refreshable />);
    await waitFor(() => expect(first()).toBe('a:51'));
    act(() => refresh());
    await waitFor(() => expect(first()).toBe('a:1'));
    await waitFor(() => expect(router.state.location.search).toBe(''));
    expect(offsets()).toEqual(['a@50', 'a@0']);
  });

  it('steps back, in the address too, when the page on screen no longer exists', async () => {
    fetchRows.mockClear();
    let size = 51;
    const shrinking = vi.fn(async ({ offset, limit }: ListPageRequest): Promise<ListPage<Row>> => ({
      items: Array.from({ length: Math.max(0, Math.min(limit, size - offset)) }, (_, i) => ({ id: offset + i + 1, filter: 's' })),
      total: size,
    }));
    const Shrinking: React.FC = () => {
      list = usePagedList<Row>('getShrinking', shrinking, ['s'], { page: useUrlPage() });
      return <p data-testid="first">{list.rows ? String(list.rows[0]?.id ?? 'none') : 'loading'}</p>;
    };
    const { router } = open('/list?page=3', <Shrinking />);
    await waitFor(() => expect(first()).toBe('51'));
    size = 50;
    await act(async () => { await list.reload(); });
    await waitFor(() => expect(first()).toBe('26'));
    expect(router.state.location.search).toBe('?page=2');
  });

  // The default: a link that names a page under OTHER filters is still a new
  // list — first page, and the page leaves the address.  (Hosts and the audit
  // log say otherwise for themselves: `keepAddressPage`, below.)
  it('by default a pushed link with new filters and a page starts from the first page', async () => {
    const { router } = open('/list?f=a');
    await waitFor(() => expect(first()).toBe('a:1'));
    await act(async () => { await router.navigate('/list?f=b&page=2'); });
    await waitFor(() => expect(first()).toBe('b:1'));
    await waitFor(() => expect(router.state.location.search).toBe('?f=b'));
    expect(offsets()).toEqual(['a@0', 'b@0']);
  });

  it('reads anything but a whole page number ≥ 2 as the first page', () => {
    const page = (s: string) => pageFromParams(new URLSearchParams(s));
    expect(page('')).toBe(0);
    expect(page('page=1')).toBe(0);
    expect(page('page=0')).toBe(0);
    expect(page('page=-4')).toBe(0);
    expect(page('page=2.5')).toBe(0);
    expect(page('page=abc')).toBe(0);
    // Not a number the reader wrote as a page (these were pages 2, 1000, 16 and 2).
    expect(page('page=2%20')).toBe(0);
    expect(page('page=1e3')).toBe(0);
    expect(page('page=0x10')).toBe(0);
    expect(page('page=2.0')).toBe(0);
    expect(page('page=4')).toBe(3);
  });
});

/**
 * `keepAddressPage` — for a list a LINK may open on a filter and a page
 * together (Hosts, the audit log): the address's page is the page whenever
 * the deps changed with the address, and a control of the page starts from
 * the first by leaving `page` out of its one write.
 */
describe('usePagedList — keepAddressPage', () => {
  let setFilter!: (next: string) => void;
  const Kept: React.FC<{ refreshKey?: number }> = ({ refreshKey = 0 }) => {
    const [params, setParams] = useSearchParams();
    const filter = params.get('f') ?? 'a';
    // The page's own control: ONE write, the filter in and the page out.
    setFilter = (next) => setParams((prev) => {
      const out = new URLSearchParams(prev);
      out.set('f', next);
      out.delete('page');
      return out;
    }, { replace: true });
    list = usePagedList<Row>(
      'getRows', (req) => fetchRows(filter, req), [filter, refreshKey],
      { page: useUrlPage(), keepAddressPage: true },
    );
    return <p data-testid="first">{list.rows ? `${list.rows[0]?.filter}:${list.rows[0]?.id}` : 'loading'}</p>;
  };
  const settle = () => act(async () => { await new Promise((resolve) => { setTimeout(resolve, 30); }); });

  it('a link that names new filters AND a page opens that page, asks for it only, and the address keeps it', async () => {
    const { router } = open('/list?f=a', <Kept />);
    await waitFor(() => expect(first()).toBe('a:1'));
    // A pushed navigation — not Back, not the page's own control.
    await act(async () => { await router.navigate('/list?f=b&page=3'); });
    await waitFor(() => expect(first()).toBe('b:51'));
    expect(router.state.historyAction).toBe('PUSH');
    await settle();
    expect(router.state.location.search).toBe('?f=b&page=3');
    expect(offsets()).toEqual(['a@0', 'b@50']);
  });

  it('a filter changed by the page’s own control starts from the first page, in one request and one write', async () => {
    const { router } = open('/list?f=a&page=3', <Kept />);
    await waitFor(() => expect(first()).toBe('a:51'));
    act(() => setFilter('b'));
    await waitFor(() => expect(first()).toBe('b:1'));
    await settle();
    expect(router.state.location.search).toBe('?f=b');
    // Not "page 3 of the new filter" on the way.
    expect(offsets()).toEqual(['a@50', 'b@0']);
  });

  it('Back is the filter and the page that were left', async () => {
    const { router } = open('/list?f=a&page=3', <Kept />);
    await waitFor(() => expect(first()).toBe('a:51'));
    await act(async () => { await router.navigate('/list?f=b&page=2'); });
    await waitFor(() => expect(first()).toBe('b:26'));
    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(first()).toBe('a:51'));
    expect(router.state.location.search).toBe('?f=a&page=3');
  });

  it('deps that changed while the address did not (a refresh) still start from the first page', async () => {
    let refresh: () => void = () => {};
    const Refreshable: React.FC = () => {
      const [key, setKey] = React.useState(0);
      refresh = () => setKey((n) => n + 1);
      return <Kept refreshKey={key} />;
    };
    const { router } = open('/list?page=3', <Refreshable />);
    await waitFor(() => expect(first()).toBe('a:51'));
    act(() => refresh());
    await waitFor(() => expect(first()).toBe('a:1'));
    await waitFor(() => expect(router.state.location.search).toBe(''));
    expect(offsets()).toEqual(['a@50', 'a@0']);
  });

  it('a page the address names past the end steps back to the last page that exists', async () => {
    const { router } = open('/list?f=a&page=99', <Kept />);
    await waitFor(() => expect(first()).toBe('a:51'));
    expect(router.state.location.search).toBe('?f=a&page=3');
  });
});

/**
 * A page that writes its whole address itself is the ONE writer: it hands the
 * hook its own reading of the page and its own writer, and does not mount
 * `useUrlPage`.  With `enabled: false` nothing is read — or written — for an
 * address the page is about to replace.
 */
describe('usePagedList — the page’s own address writer, and `enabled`', () => {
  const writes: string[] = [];
  let commit!: (patch: { f?: string; page?: number }) => void;
  let setEnabled!: (on: boolean) => void;
  const Owner: React.FC<{ startEnabled?: boolean }> = ({ startEnabled = true }) => {
    const [params, setParams] = useSearchParams();
    const [enabled, setOn] = React.useState(startEnabled);
    setEnabled = setOn;
    const filter = params.get('f') ?? 'a';
    const page = pageFromParams(params);
    // Filters and page in one write, as Hosts' `commit` does.
    commit = (patch) => {
      const out = new URLSearchParams();
      out.set('f', patch.f ?? filter);
      const next = patch.page ?? 0;
      if (next > 0) out.set('page', String(next + 1));
      writes.push(`?${out.toString()}`);
      setParams(out, { replace: true });
    };
    const setPage = (next: number) => commit({ page: next });
    list = usePagedList<Row>(
      'getRows', (req) => fetchRows(filter, req), [filter],
      { page: { page, setPage, search: params.toString() }, keepAddressPage: true, enabled },
    );
    return <p data-testid="first">{list.rows ? `${list.rows[0]?.filter}:${list.rows[0]?.id}` : 'loading'}</p>;
  };
  const settle = () => act(async () => { await new Promise((resolve) => { setTimeout(resolve, 30); }); });

  it('pages through the caller’s writer, and writes nothing of its own for a filter the caller changed', async () => {
    writes.length = 0;
    const { router } = open('/list?f=a', <Owner />);
    await waitFor(() => expect(first()).toBe('a:1'));
    act(() => list.setPage(2));
    await waitFor(() => expect(first()).toBe('a:51'));
    expect(router.state.location.search).toBe('?f=a&page=3');

    act(() => commit({ f: 'b' }));
    await waitFor(() => expect(first()).toBe('b:1'));
    await settle();
    // Every write of the address was the caller's: the page turn, the filter.
    expect(writes).toEqual(['?f=a&page=3', '?f=b']);
    expect(offsets()).toEqual(['a@0', 'a@50', 'b@0']);
  });

  it('the step back from a page past the end goes through the caller’s writer too', async () => {
    writes.length = 0;
    const { router } = open('/list?f=a&page=99', <Owner />);
    await waitFor(() => expect(first()).toBe('a:51'));
    expect(writes).toEqual(['?f=a&page=3']);
    expect(router.state.location.search).toBe('?f=a&page=3');
  });

  it('parked, it asks for nothing and writes nothing; enabled on the address that replaced the first, it asks for that one only', async () => {
    writes.length = 0;
    const { router } = open('/list', <Owner startEnabled={false} />);
    await settle();
    expect(first()).toBe('loading');
    expect(list.loading).toBe(false);
    expect(offsets()).toEqual([]);
    // A reload asked of a parked list asks for nothing either.
    await act(async () => { await list.reload(); });
    expect(offsets()).toEqual([]);

    // The page replaces its address (a restored session's filters and page),
    // then lets the list read.
    act(() => { commit({ f: 'restored', page: 1 }); setEnabled(true); });
    await waitFor(() => expect(first()).toBe('restored:26'));
    await settle();
    expect(offsets()).toEqual(['restored@25']);
    expect(writes).toEqual(['?f=restored&page=2']);
    expect(router.state.location.search).toBe('?f=restored&page=2');
  });
});
