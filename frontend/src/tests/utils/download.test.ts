/**
 * One copy of "save this as a file" (the object-URL sequence was written out a
 * dozen times), and the file names each export gives must not change.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { downloadTextFile as fromClipboard } from '../../utils/clipboard';
import { downloadTextFile, filenameFromContentDisposition, saveBlob } from '../../utils/download';

const createObjectURL = vi.fn();
const revokeObjectURL = vi.fn();
let clicked: Array<{ href: string; download: string; attached: boolean }>;

beforeEach(() => {
  clicked = [];
  createObjectURL.mockReset().mockReturnValue('blob:saved');
  revokeObjectURL.mockReset();
  Object.defineProperty(window.URL, 'createObjectURL', { value: createObjectURL, configurable: true });
  Object.defineProperty(window.URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click(this: HTMLAnchorElement) {
    clicked.push({ href: this.getAttribute('href') ?? '', download: this.download, attached: document.body.contains(this) });
  });
});
afterEach(() => { vi.restoreAllMocks(); });

describe('saveBlob', () => {
  it('clicks a link to the blob named as asked, then cleans up after itself', () => {
    const blob = new Blob(['a,b'], { type: 'text/csv' });
    saveBlob(blob, 'scope.csv');
    expect(createObjectURL).toHaveBeenCalledWith(blob);
    // Attached when clicked (Firefox ignores a detached link's click).
    expect(clicked).toEqual([{ href: 'blob:saved', download: 'scope.csv', attached: true }]);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:saved');
    expect(document.querySelector('a[download]')).toBeNull();
  });
});

describe('downloadTextFile', () => {
  it('saves text under the given name and type; utils/clipboard still exports it', async () => {
    downloadTextFile('bluestick-recovery-codes.txt', 'one\ntwo\n');
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(blob.type).toBe('text/plain');
    expect(blob.size).toBe(8);
    expect(clicked[0].download).toBe('bluestick-recovery-codes.txt');

    downloadTextFile('sbom.json', '{}', 'application/json');
    expect((createObjectURL.mock.calls[1][0] as Blob).type).toBe('application/json');
    expect(fromClipboard).toBe(downloadTextFile);
  });
});

describe('filenameFromContentDisposition', () => {
  it.each([
    ['attachment; filename="hosts_report_2026-10-07.csv"', 'hosts_report_2026-10-07.csv'],
    ['attachment; filename=report_12.zip', 'report_12.zip'],
    ['ATTACHMENT; FILENAME="Mixed Case.html"', 'Mixed Case.html'],
  ])('reads the server’s name from %s', (header, name) => {
    expect(filenameFromContentDisposition(header, 'fallback.bin')).toBe(name);
  });

  it.each([undefined, null, '', 'inline'])('falls back when the header is %j', (header) => {
    expect(filenameFromContentDisposition(header, 'report_12')).toBe('report_12');
  });
});

// That the sequence lives in utils/download only — nothing else sets a link's
// download name — is a lint rule (`npm run lint`, eslint.config.mjs).
