/**
 * The Query page's chart rule (5.406.0) and its colour token: one group
 * column and a number column, nothing else; every dark theme names its own
 * chart mark (its `info` is a text accent too light for a mark on a dark
 * surface — checked with the dataviz validator when chosen).
 */
import { describe, expect, it } from 'vitest';

import { chartMeasures } from '../../components/query/QueryChart';
import { palettes } from '../../theme/palettes';
import type { QueryAnswer } from '../../services/api';

const answer = (over: Partial<QueryAnswer>): QueryAnswer => ({
  query: 'q', dataset: 'ports', kind: 'stats', total: 1, limit: 1000, capped: false,
  columns: [{ name: 'service', type: 'text' }, { name: 'count', type: 'int' }, { name: 'names', type: 'list' }],
  rows: [['ssh', 3, ['a']]], drilldowns: ['q'], by: ['service'], ...over,
});

describe('chartMeasures', () => {
  it('is the number columns that are not groups', () => {
    expect(chartMeasures(answer({}))).toEqual(['count']);
  });
  it('is null for a listing, two groups, no number, or no rows', () => {
    expect(chartMeasures(answer({ kind: 'rows', by: null }))).toBeNull();
    expect(chartMeasures(answer({ by: ['service', 'count'] }))).toBeNull();
    expect(chartMeasures(answer({ columns: [{ name: 'service', type: 'text' }, { name: 'names', type: 'list' }] }))).toBeNull();
    expect(chartMeasures(answer({ rows: [] }))).toBeNull();
  });
});

describe('the chart mark token', () => {
  it('every dark theme names its own', () => {
    for (const [name, tokens] of Object.entries(palettes)) {
      if (tokens.mode === 'dark') expect(tokens.chartMark, name).toMatch(/^#[0-9A-F]{6}$/i);
    }
  });
});
