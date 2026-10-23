/**
 * `utils/plural` is the one count-and-noun helper (review 2026-10-23b, FEC-7);
 * the copies it replaced all answered these.
 */
import { describe, expect, it } from 'vitest';

import { plural } from '../../utils/plural';

describe('plural', () => {
  it.each([
    [0, 'host', undefined, '0 hosts'],
    [1, 'host', undefined, '1 host'],
    [2, 'host', undefined, '2 hosts'],
    [1204, 'host', undefined, `${(1204).toLocaleString()} hosts`],
    [1, 'analysis', 'analyses', '1 analysis'],
    [3, 'analysis', 'analyses', '3 analyses'],
  ])('%s %s → %s', (n, one, many, expected) => {
    expect(plural(n, one, many)).toBe(expected);
  });
});
