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
