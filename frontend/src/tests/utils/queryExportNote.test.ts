/**
 * A downloaded Query CSV says when it is not the whole answer (external
 * review 2026-10-16, U1) — from the export's own headers.
 */
import { describe, expect, it } from 'vitest';

import { exportNote } from '../../utils/queryExport';

const h = (total: number, rows: number, cap: number, kind = 'rows') => ({
  'x-total-count': String(total), 'x-row-count': String(rows), 'x-cap': String(cap), 'x-answer-kind': kind,
});

describe('exportNote', () => {
  it('says nothing when the file holds the whole answer', () => {
    expect(exportNote(h(120, 120, 50000))).toBeUndefined();
    expect(exportNote({})).toBeUndefined();
  });
  it('names the file’s cap when that cut it', () => {
    expect(exportNote(h(80000, 50000, 50000))).toBe(
      `It holds the first ${(50000).toLocaleString()} of ${(80000).toLocaleString()} rows — a file holds at most ${(50000).toLocaleString()} rows.`,
    );
    expect(exportNote(h(1500, 1000, 1000, 'stats'))).toContain('groups — a file holds at most');
  });
  it('names the query’s head when that cut it', () => {
    expect(exportNote(h(19, 3, 50000))).toBe('It holds the first 3 of 19 rows — as the query’s head asked.');
  });
});
