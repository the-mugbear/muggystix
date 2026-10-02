/**
 * The finding's images, loaded once for the page: which may be placed, and
 * that bytes are fetched only for an image on the finding's own list.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The hook reaches the barrel only for its default dependencies (the test
// injects its own), but the barrel's HTTP client must not load in jsdom.
vi.mock('../../services/api', () => ({
  getFindingImages: vi.fn(),
  getNoteAttachmentObjectUrl: vi.fn(),
}));

import { useFindingImages } from '../../hooks/useFindingImages';

const image = (id: number, over: Record<string, unknown> = {}) => ({
  id, note_id: 1, filename: `shot-${id}.png`, caption: null, content_type: 'image/png', size_bytes: 10,
  in_report: true, printable: true, placed_in: [], uploaded_by_id: 1, by_agent: false, created_at: null,
  can_edit: true, ...over,
});

const deps = () => ({
  getFindingImages: vi.fn(),
  getNoteAttachmentObjectUrl: vi.fn(),
});

beforeEach(() => { URL.revokeObjectURL = vi.fn(); });

describe('useFindingImages', () => {
  it('lists the images and offers only the ticked, printable ones for placing', async () => {
    const d = deps();
    d.getFindingImages.mockResolvedValue({
      items: [
        image(1, { caption: 'The relayed session' }),
        image(2, { in_report: false }),
        image(3, { printable: false, content_type: 'image/webp' }),
      ],
      caption_max: 2000,
    });
    const { result } = renderHook(() => useFindingImages(7, d));
    await waitFor(() => expect(result.current.images).toHaveLength(3));
    expect(d.getFindingImages).toHaveBeenCalledWith(7);
    expect(result.current.placeable.map((i) => i.id)).toEqual([1]);
    expect(result.current.captionMax).toBe(2000);
    // The preview's question: is this id an image the section may show?
    expect(result.current.resolver.lookup(1)).toEqual({ caption: 'The relayed session', src: undefined });
    expect(result.current.resolver.lookup(2)).toBeNull();   // not ticked
    expect(result.current.resolver.lookup(3)).toBeNull();   // not printable
    expect(result.current.resolver.lookup(999)).toBeNull(); // not this finding's
  });

  it('fetches the bytes only when asked, once, and never for an id off the list', async () => {
    const d = deps();
    d.getFindingImages.mockResolvedValue({ items: [image(1), image(2, { in_report: false })], caption_max: 2000 });
    d.getNoteAttachmentObjectUrl.mockResolvedValue('blob:one');
    const { result } = renderHook(() => useFindingImages(7, d));
    await waitFor(() => expect(result.current.placeable).toHaveLength(1));
    expect(d.getNoteAttachmentObjectUrl).not.toHaveBeenCalled();

    act(() => {
      result.current.resolver.ensure(999);
      result.current.resolver.ensure(2);
      result.current.resolver.ensure(1);
      result.current.resolver.ensure(1);
    });
    await waitFor(() => expect(result.current.urls[1]).toBe('blob:one'));
    expect(d.getNoteAttachmentObjectUrl).toHaveBeenCalledTimes(1);
    expect(d.getNoteAttachmentObjectUrl).toHaveBeenCalledWith(1);
    // The caption falls back to the file name, as the report does.
    expect(result.current.resolver.lookup(1)).toEqual({ caption: 'shot-1.png', src: 'blob:one' });
  });

  it('re-reads on reload, and reports a failed load without losing the page', async () => {
    const d = deps();
    d.getFindingImages.mockResolvedValueOnce({ items: [image(1)], caption_max: 2000 });
    const { result } = renderHook(() => useFindingImages(7, d));
    await waitFor(() => expect(result.current.images).toHaveLength(1));
    d.getFindingImages.mockResolvedValueOnce({ items: [image(1, { placed_in: ['impact'] })], caption_max: 2000 });
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.images[0].placed_in).toEqual(['impact']));
    d.getFindingImages.mockRejectedValueOnce(new Error('boom'));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.images).toHaveLength(1);
  });

  it('drops the images of the finding the page left', async () => {
    const d = deps();
    let release: (v: unknown) => void = () => {};
    d.getFindingImages.mockImplementation((id: number) => (id === 7
      ? new Promise((resolve) => { release = resolve; })
      : Promise.resolve({ items: [image(20)], caption_max: 2000 })));
    const { result, rerender } = renderHook(({ id }) => useFindingImages(id, d), { initialProps: { id: 7 } });
    rerender({ id: 8 });
    await waitFor(() => expect(result.current.images.map((i) => i.id)).toEqual([20]));
    await act(async () => { release({ items: [image(1)], caption_max: 2000 }); });
    expect(result.current.images.map((i) => i.id)).toEqual([20]);   // the late answer for 7 changed nothing
  });

  // Review 2026-10-01 S4 — the list is loading, ready, or failed.
  it('says whether the list is known: loading until read, failed when it never could be, ready after a retry', async () => {
    const d = deps();
    d.getFindingImages.mockRejectedValueOnce(new Error('boom'));
    const { result } = renderHook(() => useFindingImages(7, d));
    expect(result.current.listStatus).toBe('loading');
    expect(result.current.resolver.listStatus).toBe('loading');
    await waitFor(() => expect(result.current.listStatus).toBe('failed'));
    expect(result.current.error).toBeTruthy();

    d.getFindingImages.mockResolvedValueOnce({ items: [image(1)], caption_max: 2000 });
    act(() => result.current.resolver.retryList?.());
    await waitFor(() => expect(result.current.listStatus).toBe('ready'));
    expect(result.current.error).toBeNull();
    // A failed RE-read keeps what is known.
    d.getFindingImages.mockRejectedValueOnce(new Error('boom'));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.listStatus).toBe('ready');
  });

  it('is loading again, at once, for another finding', async () => {
    const d = deps();
    d.getFindingImages.mockImplementation((id: number) => (id === 7
      ? Promise.resolve({ items: [image(1)], caption_max: 2000 })
      : new Promise(() => undefined)));
    const { result, rerender } = renderHook(({ id }) => useFindingImages(id, d), { initialProps: { id: 7 } });
    await waitFor(() => expect(result.current.listStatus).toBe('ready'));
    rerender({ id: 8 });
    expect(result.current.listStatus).toBe('loading');
  });

  // M6 — the bytes: a few at a time, a failure that is said, one cache.
  it('fetches at most four images at a time', async () => {
    const d = deps();
    const ids = Array.from({ length: 12 }, (_, i) => i + 1);
    d.getFindingImages.mockResolvedValue({ items: ids.map((i) => image(i)), caption_max: 2000 });
    let inFlight = 0;
    let peak = 0;
    const releases: Array<() => void> = [];
    d.getNoteAttachmentObjectUrl.mockImplementation((id: number) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      return new Promise<string>((resolve) => {
        releases.push(() => { inFlight -= 1; resolve(`blob:${id}`); });
      });
    });
    const { result } = renderHook(() => useFindingImages(7, d));
    await waitFor(() => expect(result.current.placeable).toHaveLength(12));
    act(() => { ids.forEach((id) => result.current.resolver.ensure(id)); });
    await waitFor(() => expect(d.getNoteAttachmentObjectUrl).toHaveBeenCalledTimes(4));
    while (Object.keys(result.current.urls).length < 12) {
      // eslint-disable-next-line no-await-in-loop
      await act(async () => { releases.shift()?.(); await Promise.resolve(); });
    }
    expect(peak).toBe(4);
    expect(d.getNoteAttachmentObjectUrl).toHaveBeenCalledTimes(12);
  });

  it('reports an image whose bytes could not be fetched, and fetches it again on retry', async () => {
    const d = deps();
    d.getFindingImages.mockResolvedValue({ items: [image(1)], caption_max: 2000 });
    d.getNoteAttachmentObjectUrl.mockRejectedValueOnce(new Error('503'));
    const { result } = renderHook(() => useFindingImages(7, d));
    await waitFor(() => expect(result.current.placeable).toHaveLength(1));
    act(() => result.current.resolver.ensure(1));
    await waitFor(() => expect(result.current.resolver.failed?.(1)).toBe(true));
    // Asking again does not hammer a failing image; a retry does fetch.
    act(() => result.current.resolver.ensure(1));
    expect(d.getNoteAttachmentObjectUrl).toHaveBeenCalledTimes(1);
    d.getNoteAttachmentObjectUrl.mockResolvedValueOnce('blob:one');
    act(() => result.current.resolver.retry?.(1));
    await waitFor(() => expect(result.current.urls[1]).toBe('blob:one'));
    expect(result.current.resolver.failed?.(1)).toBe(false);
  });

  it('serves the comment thread’s thumbnails from the same cache — any listed image, one fetch', async () => {
    const d = deps();
    d.getFindingImages.mockResolvedValue({ items: [image(1), image(2, { in_report: false })], caption_max: 2000 });
    d.getNoteAttachmentObjectUrl.mockImplementation((id: number) => Promise.resolve(`blob:${id}`));
    const { result } = renderHook(() => useFindingImages(7, d));
    await waitFor(() => expect(result.current.images).toHaveLength(2));
    expect(result.current.thumbnails.has(2)).toBe(true);
    expect(result.current.thumbnails.has(999)).toBe(false);
    act(() => {
      result.current.thumbnails.ensure(1);
      result.current.thumbnails.ensure(2);    // not ticked: a thumbnail, never a placed image
      result.current.thumbnails.ensure(999);  // not this finding's
      result.current.resolver.ensure(1);      // the placed image: the same fetch
    });
    await waitFor(() => expect(result.current.thumbnails.urls[2]).toBe('blob:2'));
    expect(d.getNoteAttachmentObjectUrl.mock.calls.map((c) => c[0]).sort()).toEqual([1, 2]);
    expect(result.current.resolver.lookup(2)).toBeNull();
  });

  it('revokes an object URL that arrives after the page is gone', async () => {
    const d = deps();
    d.getFindingImages.mockResolvedValue({ items: [image(1)], caption_max: 2000 });
    let release!: (url: string) => void;
    d.getNoteAttachmentObjectUrl.mockReturnValue(new Promise<string>((resolve) => { release = resolve; }));
    const { result, unmount } = renderHook(() => useFindingImages(7, d));
    await waitFor(() => expect(result.current.placeable).toHaveLength(1));
    act(() => result.current.resolver.ensure(1));
    await waitFor(() => expect(d.getNoteAttachmentObjectUrl).toHaveBeenCalled());
    unmount();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    await act(async () => { release('blob:late'); await Promise.resolve(); });
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:late');
  });

  it('loads nothing without a finding', () => {
    const d = deps();
    const { result } = renderHook(() => useFindingImages(null, d));
    expect(d.getFindingImages).not.toHaveBeenCalled();
    expect(result.current.placeable).toEqual([]);
  });
});
