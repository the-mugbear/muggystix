/**
 * The one reader of a pasted image (walkthrough 2026-10-14): some copies put
 * the image in `files`, some only in `items`; text is never an image.
 */
import { describe, expect, it } from 'vitest';

import { imagesFromClipboard } from '../../utils/clipboardImages';

const png = (name: string) => new File(['x'], name, { type: 'image/png' });
const data = (files: File[], items: Array<{ kind: string; type: string; getAsFile: () => File | null }> = []) =>
  ({ files, items }) as unknown as DataTransfer;

describe('imagesFromClipboard', () => {
  it('reads the images in files, and nothing else there', () => {
    const shot = png('shot.png');
    expect(imagesFromClipboard(data([shot, new File(['t'], 'a.txt', { type: 'text/plain' })]))).toEqual([shot]);
  });

  it('falls back to the items when files holds none', () => {
    const shot = png('item.png');
    expect(imagesFromClipboard(data([], [
      { kind: 'string', type: 'text/html', getAsFile: () => null },
      { kind: 'file', type: 'image/png', getAsFile: () => shot },
    ]))).toEqual([shot]);
  });

  it('is empty for text, and for no clipboard at all', () => {
    expect(imagesFromClipboard(data([], [{ kind: 'string', type: 'text/plain', getAsFile: () => null }]))).toEqual([]);
    expect(imagesFromClipboard(null)).toEqual([]);
  });
});
