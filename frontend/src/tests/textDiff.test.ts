import { describe, expect, it } from 'vitest';

import { DIFF_MAX_CELLS, diffStats, diffWords } from '../utils/textDiff';

const join = (parts: { kind: string; text: string }[], keep: string[]) =>
  parts.filter((p) => keep.includes(p.kind)).map((p) => p.text).join('');

describe('diffWords', () => {
  it('marks the words a draft adds and removes, and rebuilds both texts exactly', () => {
    const before = 'Apply the vendor fix and re-scan.';
    const after = 'Apply the vendor fix to 2.3.32, then re-scan to confirm.';
    const parts = diffWords(before, after)!;
    expect(join(parts, ['same', 'removed'])).toBe(before);
    expect(join(parts, ['same', 'added'])).toBe(after);
    expect(parts.some((p) => p.kind === 'removed' && p.text.includes('and'))).toBe(true);
    expect(parts.some((p) => p.kind === 'added' && p.text.includes('2.3.32,'))).toBe(true);
  });

  it('keeps line breaks, so Markdown lists survive the comparison', () => {
    const before = '- one\n- two';
    const after = '- one\n- two\n- three';
    const parts = diffWords(before, after)!;
    expect(join(parts, ['same', 'added'])).toBe(after);
    expect(parts.filter((p) => p.kind === 'removed')).toEqual([]);
  });

  it('an empty side is all added or all removed', () => {
    expect(diffWords('', 'New text.')).toEqual([{ kind: 'added', text: 'New text.' }]);
    expect(diffWords('Old.', '')).toEqual([{ kind: 'removed', text: 'Old.' }]);
    expect(diffWords('Same.', 'Same.')).toEqual([{ kind: 'same', text: 'Same.' }]);
  });

  it('refuses a comparison too large to compute rather than freezing the page', () => {
    const side = Math.ceil(Math.sqrt(DIFF_MAX_CELLS)) + 10;
    const a = Array.from({ length: side }, (_, k) => `a${k}`).join(' ');
    const b = Array.from({ length: side }, (_, k) => `b${k}`).join(' ');
    expect(diffWords(a, b)).toBeNull();
    // A long text with a small change is cheap: the common head and tail are cut first.
    expect(diffWords(`${a} x`, `${a} y`)).not.toBeNull();
  });

  it('counts words, not tokens', () => {
    expect(diffStats(diffWords('one two three', 'one four five three')!)).toEqual({ added: 2, removed: 1 });
  });
});
