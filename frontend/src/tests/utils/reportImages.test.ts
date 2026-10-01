/**
 * Images in a finding's report text — the client's half of the rules.  The
 * reference pattern is the twin of `REFERENCE` in
 * backend/app/services/report_images.py (same cases as
 * tests/test_report_image_placement.py).
 */
import { describe, expect, it } from 'vitest';

import {
  captionAsAlt, evidenceIdOf, imageReference, placementLine, referencedImageIds,
} from '../../utils/reportImages';

describe('referencedImageIds — what places an image', () => {
  it('reads a Markdown image whose target is evidence:<id>, once each, in order', () => {
    expect(referencedImageIds('Before ![The relayed session](evidence:57) after')).toEqual([57]);
    expect(referencedImageIds('![](evidence:3)\n\n![again](evidence:3) ![x](evidence:9)')).toEqual([3, 9]);
    expect(referencedImageIds('![t](evidence:4 "a title")')).toEqual([4]);
    expect(referencedImageIds('![a \\] bracket](evidence:5)')).toEqual([5]);
    expect(referencedImageIds('| a | ![cell](evidence:6) |\n- ![item](evidence:7)')).toEqual([6, 7]);
    expect(referencedImageIds('![x](evidence:1){.c onerror=alert(1)}')).toEqual([1]);
  });

  it('is not fooled by links, other schemes, paths or raw HTML', () => {
    for (const text of [
      '[text](evidence:1)', '![x](https://example.com/evidence:1)', '![x](evidence/1.png)',
      '![x](evidence:abc)', '![x](evidence:1.png)', '<img src="evidence:1">', 'evidence:1', '', null, undefined,
    ]) {
      expect(referencedImageIds(text)).toEqual([]);
    }
  });

  it('reads the id from a target and nothing else', () => {
    expect(evidenceIdOf('evidence:57')).toBe(57);
    for (const t of ['evidence:', 'evidence:5x', 'https://x/evidence:5', 'javascript:alert(1)', 'data:image/png;base64,AA', '../5']) {
      expect(evidenceIdOf(t)).toBeNull();
    }
  });
});

describe('imageReference — what Insert image writes', () => {
  it('writes the caption as the alt text, made safe for the brackets', () => {
    expect(imageReference(57, 'The relayed session')).toBe('![The relayed session](evidence:57)');
    expect(imageReference(57, null)).toBe('![](evidence:57)');
    expect(imageReference(57, 'a [b] c\\d\nsecond line')).toBe('![a b cd second line](evidence:57)');
    // The reference it writes is one the server reads back.
    expect(referencedImageIds(imageReference(9, 'x ] y [ z'))).toEqual([9]);
  });

  it('keeps a 2,000-character caption readable in the source', () => {
    const alt = captionAsAlt('c'.repeat(2000));
    expect(alt.length).toBe(120);
    expect(alt.endsWith('…')).toBe(true);
    expect(referencedImageIds(imageReference(3, 'c'.repeat(2000)))).toEqual([3]);
  });
});

describe('placementLine — where an image prints', () => {
  const base = { in_report: true, printable: true, placed_in: [] as string[] };
  it('names the sections, or says it prints under Evidence', () => {
    expect(placementLine({ ...base, placed_in: ['description', 'impact'] }).text).toBe('In: Description, Impact');
    expect(placementLine({ ...base, placed_in: ['steps_to_reproduce'] }).text).toBe('In: Steps to reproduce');
    expect(placementLine(base)).toEqual({ text: 'Not placed — prints under Evidence', tone: 'plain' });
  });

  it('warns about a reference that will not print', () => {
    const unticked = placementLine({ ...base, in_report: false, placed_in: ['impact'] });
    expect(unticked.tone).toBe('warning');
    expect(unticked.text).toContain('Referenced in Impact, but not ticked');
    expect(placementLine({ ...base, printable: false }).tone).toBe('warning');
    expect(placementLine({ ...base, in_report: false }).text).toBe('Not in the report');
  });
});
