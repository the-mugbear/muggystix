/**
 * `lib/objectUrls` — the one place an object URL on screen is revoked.
 * (That each of its users ends with made == revoked is
 * `tests/objectUrls.lifecycle.test.tsx`.)
 */
import { StrictMode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({ getNoteAttachmentObjectUrl: vi.fn() }));

import { useAttachmentImages } from '../../hooks/useAttachmentImages';
import { ObjectUrlStore, useBlobUrl, useObjectUrls } from '../../lib/objectUrls';

const revoked: string[] = [];
beforeEach(() => {
  revoked.length = 0;
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:made-${++n}`);
  URL.revokeObjectURL = vi.fn((url: string) => { revoked.push(url); });
});

describe('ObjectUrlStore', () => {
  it('holds a URL under its key until the key is released — once', () => {
    const store = new ObjectUrlStore<string>();
    expect(store.put('a', 'blob:a')).toBe('blob:a');
    expect(store.get('a')).toBe('blob:a');
    expect(revoked).toEqual([]);
    store.release('a');
    store.release('a');
    expect(revoked).toEqual(['blob:a']);
    expect(store.get('a')).toBeUndefined();
    expect(store.size).toBe(0);
  });

  it('a key given another URL lets the first go', () => {
    const store = new ObjectUrlStore<string>();
    store.put('a', 'blob:first');
    store.put('a', 'blob:second');
    expect(revoked).toEqual(['blob:first']);
    expect(store.get('a')).toBe('blob:second');
  });

  it('asking again for a key releases what it shows, and only the LAST answer is kept — in any order', () => {
    const store = new ObjectUrlStore<string>();
    store.put('shot', 'blob:shown');
    const first = store.claim('shot');
    expect(revoked).toEqual(['blob:shown']);
    const second = store.claim('shot');
    expect(first.isCurrent()).toBe(false);
    // The later request answers first; the earlier one's answer is not shown.
    expect(second.accept('blob:second')).toBe('blob:second');
    expect(first.accept('blob:first')).toBeNull();
    expect(revoked).toEqual(['blob:shown', 'blob:first']);
    expect(store.get('shot')).toBe('blob:second');
  });

  it('a released key takes nothing that was on its way', () => {
    const store = new ObjectUrlStore<number>();
    const claim = store.claim(1);
    store.release(1);
    expect(claim.accept('blob:late')).toBeNull();
    expect(revoked).toEqual(['blob:late']);
  });

  it('keepOnly releases the rest, held or on its way', () => {
    const store = new ObjectUrlStore<number>();
    store.put(1, 'blob:1');
    store.put(2, 'blob:2');
    const coming = store.claim(3);
    store.keepOnly((id) => id === 2);
    expect(revoked).toEqual(['blob:1']);
    expect(coming.accept('blob:3')).toBeNull();
    expect(store.get(2)).toBe('blob:2');
  });

  it('clear revokes everything and drops what was on its way, and the store goes on', () => {
    const store = new ObjectUrlStore<number>();
    store.put(1, 'blob:1');
    const coming = store.claim(2);
    store.clear();
    expect(revoked).toEqual(['blob:1']);
    expect(coming.accept('blob:2')).toBeNull();
    expect(store.put(3, 'blob:3')).toBe('blob:3');
    expect(revoked).toEqual(['blob:1', 'blob:2']);
  });

  it('closed: everything is revoked, and whatever arrives or is put afterwards is revoked at once', () => {
    const store = new ObjectUrlStore<number>();
    store.put(1, 'blob:1');
    const coming = store.claim(2);
    store.close();
    expect(store.closed).toBe(true);
    expect(coming.accept('blob:2')).toBeNull();
    expect(store.put(3, 'blob:3')).toBeNull();
    expect(revoked).toEqual(['blob:1', 'blob:2', 'blob:3']);
    // Opened again (React's development remount): what was claimed before stays dead.
    store.open();
    expect(coming.isCurrent()).toBe(false);
    expect(store.put(4, 'blob:4')).toBe('blob:4');
  });
});

describe('useObjectUrls', () => {
  it('is one store for the component’s life, closed when it unmounts', () => {
    const { result, rerender, unmount } = renderHook(() => useObjectUrls<string>());
    const store = result.current;
    store.put('a', 'blob:a');
    rerender();
    expect(result.current).toBe(store);
    expect(revoked).toEqual([]);
    unmount();
    expect(revoked).toEqual(['blob:a']);
    expect(store.put('b', 'blob:b')).toBeNull();
  });

  it('works when React mounts the component twice (StrictMode)', () => {
    const { result, unmount } = renderHook(() => useObjectUrls<string>(), { wrapper: StrictMode });
    expect(result.current.put('a', 'blob:a')).toBe('blob:a');
    unmount();
    expect(revoked).toEqual(['blob:a']);
  });
});

describe('useBlobUrl', () => {
  it('makes a URL for the bytes, another when they change, and revokes each when it is no longer shown', () => {
    const first = new Blob(['1']);
    const second = new Blob(['2']);
    const { result, rerender, unmount } = renderHook(({ blob }: { blob: Blob | undefined }) => useBlobUrl(blob), {
      initialProps: { blob: undefined as Blob | undefined },
    });
    expect(result.current).toBeNull();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    rerender({ blob: first });
    expect(result.current).toBe('blob:made-1');
    rerender({ blob: second });
    expect(result.current).toBe('blob:made-2');
    expect(revoked).toEqual(['blob:made-1']);
    rerender({ blob: undefined });
    expect(result.current).toBeNull();
    expect(revoked).toEqual(['blob:made-1', 'blob:made-2']);
    rerender({ blob: first });
    expect(result.current).toBe('blob:made-3');
    unmount();
    expect(revoked).toEqual(['blob:made-1', 'blob:made-2', 'blob:made-3']);
  });
});

describe('useAttachmentImages — what the cache lets go', () => {
  const fetchUrl = vi.fn();
  // Past the queue's own tick and the mutation's.
  const flush = () => act(async () => { await new Promise((resolve) => { setTimeout(resolve, 0); }); });
  beforeEach(() => {
    fetchUrl.mockReset().mockImplementation((_projectId: number, id: number) => Promise.resolve(`blob:${id}`));
  });

  it('another scope: the earlier images are revoked at once, and a late one on arrival', async () => {
    let late!: (url: string) => void;
    fetchUrl.mockImplementation((_projectId: number, id: number) => (id === 2
      ? new Promise<string>((resolve) => { late = resolve; })
      : Promise.resolve(`blob:${id}`)));
    const { result, rerender } = renderHook(({ scope }) => useAttachmentImages(scope, fetchUrl), {
      initialProps: { scope: 7 },
    });
    act(() => { result.current.request(1); result.current.request(2); });
    await flush();
    expect(result.current.urls).toEqual({ 1: 'blob:1' });

    rerender({ scope: 8 });
    expect(result.current.urls).toEqual({});
    expect(revoked).toEqual(['blob:1']);
    await act(async () => { late('blob:2'); await Promise.resolve(); });
    expect(revoked).toEqual(['blob:1', 'blob:2']);
    expect(result.current.urls).toEqual({});
    // The same id may be asked for again in the new scope.
    act(() => result.current.request(1));
    await flush();
    expect(result.current.urls).toEqual({ 1: 'blob:1' });
    expect(fetchUrl).toHaveBeenCalledTimes(3);
  });

  it('keepOnly: an image no longer shown is revoked, its failure forgotten, and it can be asked for again', async () => {
    fetchUrl.mockImplementation((_projectId: number, id: number) => (id === 3
      ? Promise.reject(new Error('503'))
      : Promise.resolve(`blob:${id}`)));
    const { result } = renderHook(() => useAttachmentImages(null, fetchUrl));
    act(() => { [1, 2, 3].forEach((id) => result.current.request(id)); });
    await flush();
    expect(result.current.urls).toEqual({ 1: 'blob:1', 2: 'blob:2' });
    expect(result.current.failed(3)).toBe(true);

    act(() => result.current.keepOnly((id) => id === 2));
    expect(result.current.urls).toEqual({ 2: 'blob:2' });
    expect(result.current.failed(3)).toBe(false);
    expect(revoked).toEqual(['blob:1']);

    act(() => result.current.request(1));
    await flush();
    expect(result.current.urls).toEqual({ 1: 'blob:1', 2: 'blob:2' });
  });

  it('an image that waited its turn for an owner that has gone is not fetched', async () => {
    const releases: Array<() => void> = [];
    fetchUrl.mockImplementation((_projectId: number, id: number) => new Promise<string>((resolve) => {
      releases.push(() => resolve(`blob:${id}`));
    }));
    const { result, unmount } = renderHook(() => useAttachmentImages(null, fetchUrl));
    act(() => { [1, 2, 3, 4, 5, 6].forEach((id) => result.current.request(id)); });
    await flush();
    expect(fetchUrl).toHaveBeenCalledTimes(4);   // the limit
    unmount();
    releases.forEach((release) => release());
    await flush();
    expect(fetchUrl).toHaveBeenCalledTimes(4);
    expect([...revoked].sort()).toEqual(['blob:1', 'blob:2', 'blob:3', 'blob:4']);
  });
});
