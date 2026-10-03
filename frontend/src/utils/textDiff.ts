/**
 * Word-level comparison of two texts (5.334.0) — the report-text review shows
 * what a draft changes against the field's current text.
 *
 * Tokens are words and the whitespace between them, so joining every part's
 * text gives back the original strings exactly.  A longest-common-subsequence
 * table is quadratic: past `DIFF_MAX_CELLS` the comparison is refused (null)
 * and the caller shows the two texts without marks rather than freezing.
 */

export type DiffPart = { kind: 'same' | 'added' | 'removed'; text: string };

/** 2,000 × 2,000 tokens — about 1,000 words each side. */
export const DIFF_MAX_CELLS = 4_000_000;

const tokenize = (s: string): string[] => s.match(/\s+|[^\s]+/g) ?? [];

const push = (out: DiffPart[], kind: DiffPart['kind'], text: string) => {
  const last = out[out.length - 1];
  if (last && last.kind === kind) last.text += text;
  else out.push({ kind, text });
};

export const diffWords = (before: string, after: string): DiffPart[] | null => {
  const a = tokenize(before);
  const b = tokenize(after);
  // Common head and tail cost nothing and shrink the table.
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head
    && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail += 1;
  const am = a.slice(head, a.length - tail);
  const bm = b.slice(head, b.length - tail);
  if ((am.length + 1) * (bm.length + 1) > DIFF_MAX_CELLS) return null;

  const out: DiffPart[] = [];
  if (head) push(out, 'same', a.slice(0, head).join(''));

  // lcs[i][j] = longest common subsequence of am[i..] and bm[j..].
  const cols = bm.length + 1;
  const lcs = new Uint32Array((am.length + 1) * cols);
  for (let i = am.length - 1; i >= 0; i -= 1) {
    for (let j = bm.length - 1; j >= 0; j -= 1) {
      lcs[i * cols + j] = am[i] === bm[j]
        ? lcs[(i + 1) * cols + j + 1] + 1
        : Math.max(lcs[(i + 1) * cols + j], lcs[i * cols + j + 1]);
    }
  }
  let i = 0;
  let j = 0;
  while (i < am.length && j < bm.length) {
    if (am[i] === bm[j]) { push(out, 'same', am[i]); i += 1; j += 1; }
    else if (lcs[(i + 1) * cols + j] >= lcs[i * cols + j + 1]) { push(out, 'removed', am[i]); i += 1; }
    else { push(out, 'added', bm[j]); j += 1; }
  }
  while (i < am.length) { push(out, 'removed', am[i]); i += 1; }
  while (j < bm.length) { push(out, 'added', bm[j]); j += 1; }

  if (tail) push(out, 'same', a.slice(a.length - tail).join(''));
  return out;
};

/** Words added and removed — "12 words added, 3 removed". */
export const diffStats = (parts: DiffPart[]): { added: number; removed: number } => {
  const words = (s: string) => (s.match(/[^\s]+/g) ?? []).length;
  return parts.reduce((acc, p) => {
    if (p.kind === 'added') acc.added += words(p.text);
    if (p.kind === 'removed') acc.removed += words(p.text);
    return acc;
  }, { added: 0, removed: 0 });
};
