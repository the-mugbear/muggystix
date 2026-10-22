/**
 * The Query page's builder (5.406.0): pickers that WRITE the query text — the
 * one language, never a second request shape.  Pure: a spec → the text the
 * editor holds and the server reads.  5.418.0 (review Q9): it opens on the
 * editor's query as the SERVER reads it (`parseQueryForBuilder` →
 * `specFromShape`) — no second parser here — and has `top` and `values`.
 */
import type { BuilderShape } from '../services/api';

export interface BuilderCondition {
  column: string;
  /** = != < <= > >= or isnull / isnotnull / like */
  op: string;
  value: string;
}

export interface BuilderSpec {
  dataset: string;
  /** Hosts-language terms, as typed. */
  terms: string;
  conditions: BuilderCondition[];
  /** Group columns, in order (empty: a listing). */
  by: string[];
  /** The measure when grouping: count, fn(column), or `top` (count and
   *  percent, most first; `head` is its limit).  `as`: the answer's name for
   *  it when the query named it. */
  measure: { fn: MeasureFn; column: string; as?: string | null };
  /** Most first (by the measure) when grouping. */
  mostFirst: boolean;
  /** At most this many (0: no head); for top, its limit (0: top's own 10). */
  head: number;
}

export type MeasureFn = 'count' | 'dc' | 'min' | 'max' | 'avg' | 'sum' | 'values' | 'top';
export const MEASURES: MeasureFn[] = ['count', 'dc', 'min', 'max', 'avg', 'sum', 'values', 'top'];

/** A new builder on a dataset: a plain listing, no head (an unsorted listing
 *  cut at 20 was an arbitrary 20 — review Q9). */
export const blankSpec = (dataset: string): BuilderSpec => ({
  dataset, terms: '', conditions: [], by: [], measure: { fn: 'count', column: 'host' }, mostFirst: true, head: 0,
});

/** The server's reading of a query as the builder's spec. */
export const specFromShape = (shape: BuilderShape): BuilderSpec => ({
  dataset: shape.dataset,
  terms: shape.terms,
  conditions: shape.conditions.map((c) => ({ ...c })),
  by: [...shape.by],
  measure: {
    fn: (MEASURES as string[]).includes(shape.measure.fn) ? shape.measure.fn as MeasureFn : 'count',
    column: shape.measure.column,
    as: shape.measure.as,
  },
  mostFirst: shape.most_first,
  head: shape.head,
});

/** The answer's name for the measure, as `sort` and `where` name it. */
export const measureName = (m: BuilderSpec['measure']): string =>
  m.as || (m.fn === 'count' ? 'count' : `${m.fn}_${m.column}`);

/** The column types, to know which values go unquoted. */
export type ColumnTypes = Record<string, string>;

export const BUILDER_OPS = ['=', '!=', '<', '<=', '>', '>=', 'like', 'isnull', 'isnotnull'] as const;

const quoteText = (value: string) => `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

/** A value as the query writes it: a number or true / false bare for a
 *  column of that type, anything else quoted. */
export const literal = (value: string, type: string | undefined): string => {
  const v = value.trim();
  if ((type === 'int' || type === 'float') && /^-?\d+(\.\d+)?$/.test(v)) return v;
  if (type === 'bool' && /^(true|false)$/i.test(v)) return v.toLowerCase();
  return quoteText(v);
};

const condition = (c: BuilderCondition, types: ColumnTypes): string | null => {
  if (!c.column) return null;
  if (c.op === 'isnull' || c.op === 'isnotnull') return `${c.op}(${c.column})`;
  if (c.value.trim() === '') return null;
  if (c.op === 'like') {
    const pattern = c.value.includes('%') ? c.value : `%${c.value}%`;
    return `like(${c.column}, ${quoteText(pattern)})`;
  }
  return `${c.column}${c.op}${literal(c.value, types[c.column])}`;
};

export function buildQuery(spec: BuilderSpec, types: ColumnTypes): string {
  const parts: string[] = [`${spec.dataset}${spec.terms.trim() ? ` ${spec.terms.trim()}` : ''}`];
  const conds = spec.conditions.map((c) => condition(c, types)).filter((c): c is string => !!c);
  if (conds.length) parts.push(`where ${conds.join(' AND ')}`);
  if (spec.by.length && spec.measure.fn === 'top') {
    // top counts and orders by itself; its size is its limit.
    parts.push(`top ${spec.head > 0 ? `limit=${spec.head} ` : ''}${spec.by.join(', ')}`);
    return parts.join(' | ');
  }
  if (spec.by.length) {
    const m = spec.measure.fn === 'count' ? 'count' : `${spec.measure.fn}(${spec.measure.column})`;
    const name = measureName(spec.measure);
    parts.push(`stats ${m}${name === 'count' ? '' : ` as ${name}`} by ${spec.by.join(', ')}`);
    // A values() list has no order of its own.
    if (spec.mostFirst && spec.measure.fn !== 'values') parts.push(`sort -${name}`);
  }
  if (spec.head > 0) parts.push(`head ${spec.head}`);
  return parts.join(' | ');
}
