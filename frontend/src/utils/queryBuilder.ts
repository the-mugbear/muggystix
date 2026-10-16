/**
 * The Query page's builder (5.406.0): pickers that WRITE the query text — the
 * one language, never a second request shape.  Pure: a spec → the text the
 * editor holds and the server reads.
 */

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
  /** The measure when grouping: count, or fn(column). */
  measure: { fn: 'count' | 'dc' | 'min' | 'max' | 'avg' | 'sum'; column: string };
  /** Most first (by the measure) when grouping. */
  mostFirst: boolean;
  /** At most this many (0: no head). */
  head: number;
}

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
  if (spec.by.length) {
    const m = spec.measure.fn === 'count' ? 'count' : `${spec.measure.fn}(${spec.measure.column})`;
    const name = spec.measure.fn === 'count' ? 'count' : `${spec.measure.fn}_${spec.measure.column}`;
    parts.push(`stats ${m}${name === 'count' ? '' : ` as ${name}`} by ${spec.by.join(', ')}`);
    if (spec.mostFirst) parts.push(`sort -${name}`);
  }
  if (spec.head > 0) parts.push(`head ${spec.head}`);
  return parts.join(' | ');
}
