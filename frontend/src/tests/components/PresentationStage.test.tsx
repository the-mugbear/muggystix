/**
 * The presentation stage reports a render only once it is really drawn —
 * fonts in, and THIS mounting's image decoded (code review 2026-10-24: going
 * image → text → the same image acknowledged the third render before its
 * image had loaded, because readiness was remembered by slide key).
 */
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import type { Slide } from '../../services/api';

const api = vi.hoisted(() => ({ fetchPresentationImage: vi.fn() }));
vi.mock('../../services/api', () => api);

import StageView from '../../components/presentation/Stage';

const imageSlide: Slide = {
  key: 'f:F-01:image:11', kind: 'image', header: 'F-01 · Evidence', footer: 'f', page: 1, pages: 1, ref: 'F-01',
  blocks: [], image: { attachment_id: 11, available: true, height: 500 },
};
const textSlide: Slide = {
  key: 'f:F-01:impact:1', kind: 'section', header: 'F-01 · Impact', footer: 'f', page: 1, pages: 1, ref: 'F-01',
  blocks: [{ kind: 'p', md: 'Downgrade.' }],
};

beforeEach(() => {
  api.fetchPresentationImage.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
  URL.createObjectURL = vi.fn(() => 'blob:img');
  URL.revokeObjectURL = vi.fn();
});

describe('presentation stage — what a render acknowledges', () => {
  it('a revisited image slide is acknowledged only after ITS image loads again', async () => {
    const onRendered = vi.fn();
    const view = (slide: Slide, token: number) => (
      <StageView projectId={1} reportId={3} stage={{ width: 1600, height: 900 }} slide={slide} token={token}
        onRendered={onRendered} />
    );
    const { rerender } = render(view(imageSlide, 1));
    fireEvent.load(await screen.findByTestId('ps-image'));
    await waitFor(() => expect(onRendered).toHaveBeenLastCalledWith(1, false));

    rerender(view(textSlide, 2));
    await waitFor(() => expect(onRendered).toHaveBeenLastCalledWith(2, false));

    rerender(view(imageSlide, 3));
    const img = await screen.findByTestId('ps-image');
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(onRendered).not.toHaveBeenCalledWith(3, expect.anything());   // not before ITS load
    fireEvent.load(img);
    await waitFor(() => expect(onRendered).toHaveBeenLastCalledWith(3, false));
  });
});
