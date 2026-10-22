/**
 * The look-aheads of `SafeMarkdown`'s inline parser, answered in linear time
 * for a whole text.
 *
 * The parser asks, at each `[`, where its `]` is and, at each `*` / `_` / `~`
 * run, where the run closes.  Asked by a scan from that position, a text of
 * 32,768 `[` (or `*a `, `_a `) was ~550 million steps: 0.7–1.2 s per render
 * of 32 KB, and four times that for the 64 KB a summary may hold (R10,
 * measured 2026-10-22).  The answers here are the same answers — each scan
 * is a deterministic walk, so where two walks meet they finish the same way
 * and the end is remembered for every position on the way
 * (`tests/utils/markdownScan.test.ts` compares them with the scans on every
 * position of random texts).
 */

const UNKNOWN = -2;

/** Index of the `]` closing the `[` at `open`, honouring nesting and
 *  escapes — the scan, for a position the remembered pairs cannot answer. */
export const closingBracket = (s: string, open: number): number => {
  let depth = 0;
  for (let i = open; i < s.length; i += 1) {
    const c = s[i];
    if (c === '\\') { i += 1; continue; }
    if (c === '[') depth += 1;
    else if (c === ']') { depth -= 1; if (depth === 0) return i; }
  }
  return -1;
};

/**
 * `closingBracket` for every `[` of `s` at once: one walk from the start pairs
 * each `[` with its `]` (a stack), and a walk from a `[` it reached unescaped
 * is the same walk from there on, so the pair is that scan's answer.  A `[`
 * the walk from the start skipped as escaped (only reachable through text the
 * parser skipped) is scanned as before.
 */
export const bracketCloser = (s: string): ((open: number) => number) => {
  let pairs: Map<number, number> | null = null;
  return (open: number) => {
    if (pairs === null) {
      pairs = new Map();
      const stack: number[] = [];
      for (let i = 0; i < s.length; i += 1) {
        const c = s[i];
        if (c === '\\') { i += 1; continue; }
        if (c === '[') { stack.push(i); pairs.set(i, -1); } else if (c === ']' && stack.length) pairs.set(stack.pop()!, i);
      }
    }
    const known = pairs.get(open);
    return known !== undefined ? known : closingBracket(s, open);
  };
};

const isWordChar = (c: string | undefined) => !!c && /[\p{L}\p{N}]/u.test(c);
const isSpace = (c: string | undefined) => c !== undefined && /\s/.test(c);

/**
 * Where an emphasis run of `delim` opened at `i` closes, or -1 — for every
 * `i` of `s`.  The scan from an opener walks a fixed path (an escape jumps two,
 * a closed code span jumps past it) and stops at the first position that may
 * close `delim`; that position depends on the path only, so it is remembered
 * for every position walked.
 */
export const delimiterCloser = (s: string): ((i: number, delim: string) => number) => {
  const n = s.length;
  const memo = new Map<string, Int32Array>();

  /** Where the scan goes after `j`, when `j` is not the opener's first
   *  position: -1 when `j` closes `delim`. */
  const next = (j: number, delim: string, asStart: boolean): number => {
    const c = s[j];
    if (c === '\\') return j + 2;
    if (c === '`') {
      // A code span: its content is not markup.
      const run = /^`+/.exec(s.slice(j))![0];
      const close = s.indexOf(run, j + run.length);
      if (close !== -1) return close + run.length;
    }
    if (!asStart && s.startsWith(delim, j) && !isSpace(s[j - 1])) {
      // A single `*` must not close on half of a `**`.
      if (delim.length === 1 && s[j + 1] === delim) return j + 2;
      if (delim[0] === '_' && isWordChar(s[j + delim.length])) return j + 1;
      return -1;
    }
    return j + 1;
  };

  /** The first closer on the path from `from` (inclusive), or -1. */
  const firstCloser = (from: number, delim: string): number => {
    let ends = memo.get(delim);
    if (!ends) { ends = new Int32Array(n).fill(UNKNOWN); memo.set(delim, ends); }
    const path: number[] = [];
    let j = from;
    let found: number;
    for (;;) {
      if (j >= n) { found = -1; break; }
      if (ends[j] !== UNKNOWN) { found = ends[j]; break; }
      const after = next(j, delim, false);
      if (after === -1) { found = j; break; }
      path.push(j);
      j = after;
    }
    for (const p of path) ends[p] = found;
    if (j < n && found === j) ends[j] = j;
    return found;
  };

  return (i: number, delim: string) => {
    const start = i + delim.length;
    if (start >= n || isSpace(s[start])) return -1;
    // `_` inside a word (snake_case) is not emphasis.
    if (delim[0] === '_' && isWordChar(s[i - 1])) return -1;
    // The opener's first position never closes it; from the next one on the
    // path is every scan's.
    return firstCloser(next(start, delim, true), delim);
  };
};
