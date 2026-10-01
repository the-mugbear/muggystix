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

  it('loads nothing without a finding', () => {
    const d = deps();
    const { result } = renderHook(() => useFindingImages(null, d));
    expect(d.getFindingImages).not.toHaveBeenCalled();
    expect(result.current.placeable).toEqual([]);
  });
});
