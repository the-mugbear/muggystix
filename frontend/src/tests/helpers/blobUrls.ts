/**
 * Bytes whose object URL a test can name.
 *
 * An API function that fetches authenticated bytes answers with a `Blob`
 * (`getNoteAttachmentBlob`, `fetchWebInterfaceScreenshot`); the object URL
 * is made of it by `lib/objectUrls`, the one owner.  jsdom makes no object
 * URLs, so a test stands in for the browser:
 *
 *   beforeEach(() => { installBlobUrls(); });
 *   api.getNoteAttachmentBlob.mockResolvedValue(blobFor('blob:x'));
 *   …
 *   expect(img).toHaveAttribute('src', 'blob:x');
 *   expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x');
 */
import { vi, type Mock } from 'vitest';

const urls = new WeakMap<Blob, string>();

/** The bytes an API function answers with; their object URL will be `url`. */
export const blobFor = (url: string): Blob => {
  const blob = new Blob([url]);
  urls.set(blob, url);
  return blob;
};

/** `URL.createObjectURL` for the test: a blob made by `blobFor` gets its URL
 *  (any other, 'blob:unnamed').  Returns the mock. */
export const installBlobUrls = (): Mock<(blob: Blob) => string> => {
  const make = vi.fn((blob: Blob) => urls.get(blob) ?? 'blob:unnamed');
  URL.createObjectURL = make as unknown as typeof URL.createObjectURL;
  return make;
};
