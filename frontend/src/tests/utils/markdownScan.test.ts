/**
 * SafeMarkdown's look-aheads, answered for the whole text (R10): the same
 * answers as the per-opener scans they replaced, at every position of random
 * texts made of the characters those scans react to — and in linear time on
 * the texts that made the scans quadratic.
 */
import { describe, expect, it } from 'vitest';

import { bracketCloser, closingBracket, delimiterCloser } from '../../utils/markdownScan';

// The scans as SafeMarkdown had them before (5.417.0), kept here as the
// reference the new answers are compared with.
const oldClosingBracket = (s: string, open: number): number => {
  let depth = 0;
  for (let i = open; i < s.length; i += 1) {
    const c = s[i];
    if (c === '\\') { i += 1; continue; }
    if (c === '[') depth += 1;
    else if (c === ']') { depth -= 1; if (depth === 0) return i; }
  }
  return -1;
};

const isWordChar = (c: string | undefined) => !!c && /[\p{L}\p{N}]/u.test(c);

const oldClosingDelimiter = (s: string, i: number, delim: string): number => {
  const start = i + delim.length;
  if (start >= s.length || /\s/.test(s[start])) return -1;
  if (delim[0] === '_' && isWordChar(s[i - 1])) return -1;
  let j = start;
  while (j < s.length) {
    const c = s[j];
    if (c === '\\') { j += 2; continue; }
    if (c === '`') {
      const run = /^`+/.exec(s.slice(j))![0];
      const close = s.indexOf(run, j + run.length);
      if (close !== -1) { j = close + run.length; continue; }
    }
    if (s.startsWith(delim, j) && j > start && !/\s/.test(s[j - 1])) {
      if (delim.length === 1 && s[j + 1] === delim) { j += 2; continue; }
      if (delim[0] === '_' && isWordChar(s[j + delim.length])) { j += 1; continue; }
      return j;
    }
    j += 1;
  }
  return -1;
};

const ALPHABET = ['[', ']', '(', ')', '*', '_', '~', '`', '\\', ' ', 'a', '\n', '!'];
const DELIMS = ['**', '__', '~~', '*', '_'];

/** A seeded generator, so a failure names a text that can be re-run. */
const random = (seed: number) => () => {
  seed = (seed * 1103515245 + 12345) % 2147483648;
  return seed / 2147483648;
};

const texts = (): string[] => {
  const next = random(20261022);
  const out: string[] = [];
  for (let t = 0; t < 400; t += 1) {
    const length = 1 + Math.floor(next() * 40);
    let text = '';
    for (let c = 0; c < length; c += 1) text += ALPHABET[Math.floor(next() * ALPHABET.length)];
    out.push(text);
  }
  return out;
};

describe('the answers are the scans’ answers', () => {
  it('where a [ closes, at every position', () => {
    for (const text of texts()) {
      const closer = bracketCloser(text);
      for (let open = 0; open < text.length; open += 1) {
        if (text[open] !== '[') continue;
        expect([text, open, closer(open)]).toEqual([text, open, oldClosingBracket(text, open)]);
        expect(closingBracket(text, open)).toBe(oldClosingBracket(text, open));
      }
    }
  });

  it('where an emphasis run closes, at every position and for every delimiter', () => {
    for (const text of texts()) {
      const closer = delimiterCloser(text);
      // In a shuffled order, so a remembered walk is reused from anywhere.
      const positions = Array.from({ length: text.length }, (_, i) => text.length - 1 - i);
      for (const i of positions) {
        for (const delim of DELIMS) {
          if (!text.startsWith(delim, i)) continue;
          expect([text, i, delim, closer(i, delim)]).toEqual([text, i, delim, oldClosingDelimiter(text, i, delim)]);
        }
      }
    }
  });
});

describe('linear on the texts that were quadratic', () => {
  it.each([
    ['[', 32768],
    ['*a ', 10922],
    ['_a ', 10922],
    ['~~a ', 8192],
  ])('%j × %i', (unit, times) => {
    const text = unit.repeat(times);
    const t = performance.now();
    const brackets = bracketCloser(text);
    const delimiters = delimiterCloser(text);
    for (let i = 0; i < text.length; i += 1) {
      if (text[i] === '[') brackets(i);
      for (const delim of DELIMS) if (text.startsWith(delim, i)) delimiters(i, delim);
    }
    // The scans took 0.7–1.2 s here; a linear pass takes a few milliseconds.
    expect(performance.now() - t).toBeLessThan(250);
  });
});
