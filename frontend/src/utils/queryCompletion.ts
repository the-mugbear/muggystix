/**
 * Completion for the Query page's editor (5.406.0): what may be typed at the
 * caret — a dataset, a command after `|`, a column of the dataset in context,
 * an aggregate function, an enum column's values after `=` — from the
 * server's schema (`GET /query/schema`), never a copy of it.
 *
 * Pure: (text, caret, schema) → the word being typed and the candidates.  The
 * Hosts-language search terms after a dataset are not completed here (the
 * Hosts page's own completion owns that language).
 */
import type { QuerySchema } from '../services/api';

export interface Completion {
  /** What is shown. */
  label: string;
  /** What replaces the word being typed. */
  insert: string;
  /** A short note beside it (a column's type, a command's syntax). */
  detail?: string;
}

export interface CompletionResult {
  /** The span the chosen item replaces: [from, to). */
  from: number;
  to: number;
  items: Completion[];
}

export const COMPLETION_LIMIT = 12;

const COMMANDS: Completion[] = [
  { label: 'where', insert: 'where ', detail: 'compare columns' },
  { label: 'stats', insert: 'stats ', detail: 'count, dc(col) … by col' },
  { label: 'top', insert: 'top ', detail: 'count and percent' },
  { label: 'sort', insert: 'sort ', detail: '-col descending' },
  { label: 'head', insert: 'head ', detail: 'the first N' },
  { label: 'fields', insert: 'fields ', detail: 'a listing’s columns' },
  { label: 'table', insert: 'table ', detail: 'a listing’s columns' },
  { label: 'search', insert: 'search ', detail: 'Hosts-language terms' },
];

const FUNCTIONS: Completion[] = [
  { label: 'count', insert: 'count', detail: 'rows' },
  { label: 'dc', insert: 'dc(', detail: 'distinct values' },
  { label: 'min', insert: 'min(', detail: 'smallest' },
  { label: 'max', insert: 'max(', detail: 'largest' },
  { label: 'avg', insert: 'avg(', detail: 'mean of a number' },
  { label: 'sum', insert: 'sum(', detail: 'total of a number' },
  { label: 'values', insert: 'values(', detail: 'distinct values, listed' },
];

/** The text before the caret cut at each `|` outside a quoted string. */
const segmentsBefore = (text: string): { last: string; count: number } => {
  let quoted = false;
  let start = 0;
  let count = 0;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '\\' && quoted) { i += 1; continue; }
    if (ch === '"') quoted = !quoted;
    else if (ch === '|' && !quoted) { start = i + 1; count += 1; }
  }
  return { last: text.slice(start), count };
};

const startsWith = (items: Completion[], prefix: string) => {
  const p = prefix.toLowerCase();
  return items.filter((c) => c.label.toLowerCase().startsWith(p) && c.label.toLowerCase() !== p).slice(0, COMPLETION_LIMIT);
};

export function completeQuery(text: string, caret: number, schema: QuerySchema | null): CompletionResult | null {
  if (!schema) return null;
  const before = text.slice(0, caret);
  const word = /[A-Za-z_][A-Za-z0-9_.]*$/.exec(before)?.[0] ?? '';
  const from = caret - word.length;
  const result = (items: Completion[]): CompletionResult | null => (items.length ? { from, to: caret, items } : null);
  const { last, count } = segmentsBefore(before);
  // Inside a quoted string: nothing to complete — except an enum value being
  // typed after `col = "` (decided in `where` below).
  const inQuote = (last.match(/(?<!\\)"/g) ?? []).length % 2 === 1;

  const datasetWord = /^\s*([A-Za-z_]+)/.exec(text)?.[1]?.toLowerCase() ?? '';
  const dataset = schema.datasets.find((d) => d.name === datasetWord) ?? null;
  const columns: Completion[] = (dataset?.columns ?? []).map((c) => ({ label: c.name, insert: c.name, detail: c.type }));

  if (count === 0) {
    // The dataset is the first word; after it come Hosts-language terms.
    if (!inQuote && /^\s*[A-Za-z_]*$/.test(last)) {
      return result(startsWith(schema.datasets.map((d) => ({ label: d.name, insert: `${d.name} `, detail: d.description })), word));
    }
    return null;
  }

  const head = /^\s*([A-Za-z_]*)/.exec(last);
  const command = head?.[1]?.toLowerCase() ?? '';
  // Still typing the command itself.
  if (/^\s*[A-Za-z_]*$/.test(last)) return result(startsWith(COMMANDS, word));
  const body = last.slice(head?.[0].length ?? 0);

  if (command === 'where') {
    // After `col =` / `col !=` (and an opening quote, or none): the column's values.
    const cmp = /([A-Za-z_][A-Za-z0-9_]*)\s*(=|!=)\s*"?([A-Za-z0-9_.-]*)$/.exec(body);
    if (cmp) {
      const col = dataset?.columns.find((c) => c.name === cmp[1]);
      if (col?.values) {
        const typed = cmp[3];
        const items = col.values
          .filter((v) => v.startsWith(typed.toLowerCase()) && v !== typed)
          .map((v) => ({ label: v, insert: `"${v}"`, detail: col.name }));
        // The value replaces what was typed after the operator, quote included.
        const valueFrom = caret - (body.length - body.search(/"?[A-Za-z0-9_.-]*$/));
        return items.length ? { from: valueFrom, to: caret, items: items.slice(0, COMPLETION_LIMIT) } : null;
      }
      return null;
    }
    if (inQuote) return null;
    const keywords: Completion[] = [
      { label: 'AND', insert: 'AND ' }, { label: 'OR', insert: 'OR ' }, { label: 'NOT', insert: 'NOT ' },
      { label: 'isnull', insert: 'isnull(', detail: 'no value' },
      { label: 'isnotnull', insert: 'isnotnull(', detail: 'a value' },
      { label: 'like', insert: 'like(', detail: 'like(col, "%x%")' },
    ];
    return result(startsWith([...columns, ...keywords], word));
  }
  if (inQuote) return null;
  if (command === 'stats') {
    // Inside fn( … : a column; after `by`: columns; otherwise a function.
    if (/\(\s*[A-Za-z_]*$/.test(body) || /\bby\b[^|]*$/i.test(body)) return result(startsWith(columns, word));
    if (/\bas\s+[A-Za-z_]*$/i.test(body)) return null;
    return result(startsWith([...FUNCTIONS, { label: 'by', insert: 'by ' }, { label: 'as', insert: 'as ' }], word));
  }
  if (command === 'top' || command === 'fields' || command === 'table' || command === 'sort') {
    if (command === 'top' && /limit=\d*$/.test(body)) return null;
    return result(startsWith(columns, word));
  }
  return null;
}

/** The text with the item put in place, and where the caret goes. */
export const applyCompletion = (text: string, result: CompletionResult, item: Completion): { text: string; caret: number } => ({
  text: text.slice(0, result.from) + item.insert + text.slice(result.to),
  caret: result.from + item.insert.length,
});
