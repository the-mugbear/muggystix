/**
 * Images in a finding's report text — the client's half of the rules.  The
 * reference pattern is the twin of `REFERENCE` in
 * backend/app/services/report_images.py (same cases as
 * tests/test_report_image_placement.py).
 */
import { describe, expect, it } from 'vitest';

import {
  captionAsAlt, codeSpans, evidenceIdOf, evidenceReferenceAt, imageReference, placementLine, referencedImageIds,
} from '../../utils/reportImages';

import { CODE_REFERENCE_CASES, REFERENCE_CASES } from './reportImageCases';

describe('one grammar decides what places an image', () => {
  it.each(REFERENCE_CASES)('%j → %j', (text, ids) => {
    expect(referencedImageIds(text)).toEqual(ids);
  });

  it.each(CODE_REFERENCE_CASES)('code beside a reference: %j → %j', (text, ids) => {
    expect(referencedImageIds(text)).toEqual(ids);
  });

  it('finds the reference that starts at a position, and only there', () => {
    const text = 'Before ![The \\] session](evidence:57 "t") after';
    expect(evidenceReferenceAt(text, 7)).toEqual({ id: 57, alt: 'The \\] session', end: 41 });
    expect(text.slice(41)).toBe(' after');
    expect(evidenceReferenceAt(text, 0)).toBeNull();
    expect(evidenceReferenceAt(text, 8)).toBeNull();
    // Asked twice, the same answer (the pattern keeps no position between calls).
    expect(evidenceReferenceAt(text, 7)?.id).toBe(57);
    expect(evidenceReferenceAt('![a](<evidence:57>)', 0)).toBeNull();
  });

  // Review 2026-10-02 H5 — the position asked about is not a placement when
  // it is inside code, so the preview and the count cannot disagree.
  it('finds no reference at a position inside code', () => {
    const text = 'Write `![typed](evidence:57)` then ![shown](evidence:57)';
    expect(evidenceReferenceAt(text, text.indexOf('![typed'))).toBeNull();
    expect(evidenceReferenceAt(text, text.indexOf('![shown'))?.id).toBe(57);
    expect(evidenceReferenceAt('```\n![x](evidence:57)\n```', 4)).toBeNull();
    expect(evidenceReferenceAt('![x](evidence:57)', 0)?.id).toBe(57);
  });
});

describe('codeSpans — the server\'s `_code_spans`, rule for rule', () => {
  it.each<[string, Array<[number, number]>]>([
    ['no code', []],
    ['a `b` c', [[2, 5]]],
    ['a ``b ` c`` d', [[2, 11]]],
    ['a ` b `` c', []],                               // no run of the same length
    ['`a\nb` c', [[0, 5]]],                           // a span may cross a line break
    ['```\nx\n```\nafter `y`', [[0, 10], [16, 19]]],
    ['   ~~~~\nx\n~~~\n~~~~  \nz', [[0, 21]]],        // closes on at least as many, nothing after
    ['    ```\nx', []],                               // four spaces: not a fence (and no pair of runs)
    ['```\nx\n``` not a close\ny', [[0, 23]]],       // unclosed: to the end
    ['```\n`a`\n~~~\n```\n`b`', [[0, 16], [16, 19]]], // the other fence character does not close
  ])('%j → %j', (text, spans) => {
    expect(codeSpans(text)).toEqual(spans);
  });
});

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
