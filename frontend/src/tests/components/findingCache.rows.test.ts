/**
 * Review 2026-10-23, H-FC3 — a list row takes a write's answer only over a
 * lower revision: the finding page's rule (`putFindingAnswer`), one
 * implementation, applied to the host inspector's rows.
 */
import { describe, expect, it } from 'vitest';

import { putFindingAnswerInRows } from '../../components/findings/findingCache';
import type { Finding } from '../../services/api';

const f = (id: number, revision: number, status: string) => ({ id, revision, status } as unknown as Finding);

describe('putFindingAnswerInRows', () => {
  it('two answers arriving in reverse order leave the higher revision on the row', () => {
    const rows = [f(7, 3, 'open'), f(8, 1, 'open')];
    const later = f(7, 5, 'remediated');
    const earlier = f(7, 4, 'confirmed');
    const after = putFindingAnswerInRows(putFindingAnswerInRows(rows, later), earlier);
    expect(after[0]).toBe(later);
    expect(after[1]).toBe(rows[1]);
  });

  it('an answer at the same revision as the row leaves the row', () => {
    const rows = [f(7, 4, 'confirmed')];
    expect(putFindingAnswerInRows(rows, f(7, 4, 'open'))[0]).toBe(rows[0]);
  });

  it('a newer answer replaces only its own row', () => {
    const rows = [f(7, 3, 'open'), f(8, 9, 'open')];
    const answer = f(7, 4, 'confirmed');
    const after = putFindingAnswerInRows(rows, answer);
    expect(after).toEqual([answer, rows[1]]);
  });
});
