/**
 * A count and its noun: "1 host", "1,204 hosts", "3 analyses" (with `many`).
 *
 * The ONE copy (review 2026-10-23b, FEC-7): fifteen pages and components each
 * wrote this same function.  The number is locale-formatted; the word is
 * `one` for exactly 1, else `many` (default: `one` + "s").
 */
export const plural = (n: number, one: string, many = `${one}s`): string =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;
