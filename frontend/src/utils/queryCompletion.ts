/**
 * Completion for the Query page's editor (5.406.0): what may be typed at the
 * caret — a dataset, a command after `|`, a column in context, an aggregate
 * function, a column's values after `=` — from the server's schema
 * (`GET /query/schema`), never a copy of it.
 *
 * 5.408.0 (owner, 2026-10-17: "enrich the SPL with autocomplete"):
 *  - after `stats` / `top`, `where` / `sort` / `fields` / `table` offer the
 *    ANSWER's columns (`count`, `hosts`, the group columns), which is what
 *    they compare there;
 *  - the search terms (after the dataset, and in `| search`) are the Hosts
 *    query language, completed by the Hosts page's own completion
 *    (`components/hosts/queryCompletion`) — one completer for that language;
 *  - after `where col = "` a text column offers the project's values (the
 *    server's `GET /query/values`, which the editor asks for).
 *
 * Pure: (text, caret, schema, what the server answered) → the span to replace
 * and the candidates.  `completionNeed` says what to ask the server for.
 */
import type { HostQueryField, QuerySchema } from '../services/api';
import {
  completionContext as hostContext,
  fieldSuggestions as hostFieldSuggestions,
  findField as findHostField,
  valueSuggestions as hostValueSuggestions,
  type ValuePoolEntry,
} from '../components/hosts/queryCompletion';

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

/** What the server answered for the slot at the caret (see `completionNeed`). */
export interface CompletionAnswers {
  /** The Hosts language's fields (`GET /hosts/query/schema`). */
  hostFields?: HostQueryField[] | null;
  /** Values of the Hosts field being typed (`GET /hosts/query/suggest`). */
  hostValues?: ValuePoolEntry[] | null;
  /** Values of the text column being compared (`GET /query/values`). */
  columnValues?: { value: string; count: number }[] | null;
}

/** What the slot at the caret needs from the server. */
export type CompletionNeed =
  | { kind: 'hostValue'; field: string; partial: string }
  | { kind: 'columnValue'; dataset: string; column: string; partial: string }
  | null;

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

/** The text cut at each `|` outside a quoted string: [start, end) of each. */
const segments = (text: string): [number, number][] => {
  const out: [number, number][] = [];
  let quoted = false;
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === '\\' && quoted) { i += 1; continue; }
    if (ch === '"') quoted = !quoted;
    else if (ch === '|' && !quoted) { out.push([start, i]); start = i + 1; }
  }
  out.push([start, text.length]);
  return out;
};

const startsWith = (items: Completion[], prefix: string) => {
  const p = prefix.toLowerCase();
  return items.filter((c) => c.label.toLowerCase().startsWith(p) && c.label.toLowerCase() !== p).slice(0, COMPLETION_LIMIT);
};

/** A value written as a query's quoted string. */
const quoted = (value: string) => `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

/**
 * The columns a `stats` / `top` answer has, by the names a later command
 * uses (`count`, `dc(host)` or its `as` name, the group columns; `top` adds
 * `percent`) — the server's naming, read from the text.
 */
export const answerColumns = (command: string, body: string): Completion[] => {
  const col = (label: string, detail: string): Completion => ({ label, insert: label, detail });
  if (command === 'top') {
    const names = body.replace(/\blimit\s*=\s*\d*/gi, ' ').split(/[\s,]+/).filter((w) => /^[A-Za-z_][A-Za-z0-9_.]*$/.test(w));
    return [...names.map((n) => col(n, 'group')), col('count', 'int'), col('percent', 'float')];
  }
  const byAt = body.search(/\bby\b/i);
  const aggregates = byAt >= 0 ? body.slice(0, byAt) : body;
  const by = byAt >= 0 ? body.slice(byAt + 2).split(/[\s,]+/).filter((w) => /^[A-Za-z_][A-Za-z0-9_.]*$/.test(w)) : [];
  const out: Completion[] = [];
  for (const part of aggregates.split(',')) {
    const m = /^\s*([A-Za-z_]+)\s*(?:\(\s*([A-Za-z_][A-Za-z0-9_.]*)?\s*\))?\s*(?:as\s+([A-Za-z_][A-Za-z0-9_.]*))?\s*$/i.exec(part);
    if (!m) continue;
    const fn = m[1].toLowerCase() === 'distinct_count' ? 'dc' : m[1].toLowerCase();
    const name = m[3] ?? (m[2] ? `${fn}(${m[2]})` : fn);
    out.push(col(name, fn === 'values' ? 'list' : fn === 'avg' ? 'float' : 'aggregate'));
  }
  return [...by.map((n) => col(n, 'group')), ...out];
};

type Slot =
  | { kind: 'items'; result: CompletionResult | null }
  | { kind: 'hosts'; start: number; end: number }
  | { kind: 'columnValue'; dataset: string; column: string; partial: string; from: number; to: number; enumValues?: string[] };

function slotAt(text: string, caret: number, schema: QuerySchema): Slot {
  const before = text.slice(0, caret);
  const word = /[A-Za-z_][A-Za-z0-9_.]*$/.exec(before)?.[0] ?? '';
  const from = caret - word.length;
  const items = (list: Completion[]): Slot => ({ kind: 'items', result: list.length ? { from, to: caret, items: list } : null });
  const none: Slot = { kind: 'items', result: null };
  const all = segments(text);
  const index = all.findIndex(([s, e]) => caret >= s && caret <= e);
  const [segStart, segEnd] = all[index];
  const last = text.slice(segStart, caret);
  const inQuote = (last.match(/(?<!\\)"/g) ?? []).length % 2 === 1;

  const datasetMatch = /^\s*([A-Za-z_]+)/.exec(text);
  const datasetWord = datasetMatch?.[1]?.toLowerCase() ?? '';
  const dataset = schema.datasets.find((d) => d.name === datasetWord) ?? null;
  const columns: Completion[] = (dataset?.columns ?? []).map((c) => ({ label: c.name, insert: c.name, detail: c.type }));

  if (index === 0) {
    // The dataset is the first word; after it come Hosts-language terms.
    if (!inQuote && /^\s*[A-Za-z_]*$/.test(last)) {
      return items(startsWith(schema.datasets.map((d) => ({ label: d.name, insert: `${d.name} `, detail: d.description })), word));
    }
    // `names` has no host: it takes no search terms.
    if (!dataset || dataset.name === 'names' || !datasetMatch) return none;
    return { kind: 'hosts', start: datasetMatch[0].length, end: segEnd };
  }

  const head = /^\s*([A-Za-z_]*)/.exec(last);
  const command = head?.[1]?.toLowerCase() ?? '';
  // Still typing the command itself — its syntax from the server's reference.
  if (/^\s*[A-Za-z_]*$/.test(last)) {
    return items(startsWith(COMMANDS.map((c) => ({
      ...c, detail: schema.commands.find((s) => s.name === c.label)?.syntax ?? c.detail,
    })), word));
  }
  const bodyStart = segStart + (head?.[0].length ?? 0);
  const body = text.slice(bodyStart, caret);
  if (command === 'search') return { kind: 'hosts', start: bodyStart, end: segEnd };

  // After a `stats` / `top`, a command works on the answer's columns.
  let answer: Completion[] | null = null;
  for (const [s, e] of all.slice(1, index)) {
    const m = /^\s*(stats|top)\b/i.exec(text.slice(s, e));
    if (m) answer = answerColumns(m[1].toLowerCase(), text.slice(s + m[0].length, e));
  }
  const inContext = answer ?? columns;

  if (command === 'where') {
    // After `col =` / `col !=`: the column's values — an enum's from the
    // schema, a text column's from the server.
    const cmp = /([A-Za-z_][A-Za-z0-9_]*)\s*(=|!=)\s*("(?:[^"\\]|\\.)*|[A-Za-z0-9_.-]*)$/.exec(body);
    if (cmp) {
      const col = dataset?.columns.find((c) => c.name === cmp[1]);
      const typed = cmp[3];
      const partial = typed.startsWith('"') ? typed.slice(1).replace(/\\(.)/g, '$1') : typed;
      const valueFrom = caret - typed.length;
      if (!col || !dataset) return none;
      if (col.values) {
        return { kind: 'columnValue', dataset: dataset.name, column: col.name, partial, from: valueFrom, to: caret, enumValues: col.values };
      }
      if (col.type === 'text') return { kind: 'columnValue', dataset: dataset.name, column: col.name, partial, from: valueFrom, to: caret };
      return none;
    }
    if (inQuote) return none;
    const keywords: Completion[] = [
      { label: 'AND', insert: 'AND ' }, { label: 'OR', insert: 'OR ' }, { label: 'NOT', insert: 'NOT ' },
      { label: 'isnull', insert: 'isnull(', detail: 'no value' },
      { label: 'isnotnull', insert: 'isnotnull(', detail: 'a value' },
      { label: 'like', insert: 'like(', detail: 'like(col, "%x%")' },
    ];
    return items(startsWith([...inContext, ...keywords], word));
  }
  if (inQuote) return none;
  if (command === 'stats') {
    // Inside fn( … : a column; after `by`: columns; otherwise a function.
    if (/\(\s*[A-Za-z_]*$/.test(body) || /\bby\b[^|]*$/i.test(body)) return items(startsWith(columns, word));
    if (/\bas\s+[A-Za-z_]*$/i.test(body)) return none;
    return items(startsWith([...FUNCTIONS, { label: 'by', insert: 'by ' }, { label: 'as', insert: 'as ' }], word));
  }
  if (command === 'top') {
    if (/limit=\d*$/.test(body)) return none;
    return items(startsWith(columns, word));
  }
  if (command === 'fields' || command === 'table' || command === 'sort') return items(startsWith(inContext, word));
  return none;
}

/** What to ask the server for the slot at the caret, if anything. */
export function completionNeed(
  text: string, caret: number, schema: QuerySchema | null, hostFields?: HostQueryField[] | null,
): CompletionNeed {
  if (!schema) return null;
  const slot = slotAt(text, caret, schema);
  if (slot.kind === 'columnValue') {
    return slot.enumValues ? null : { kind: 'columnValue', dataset: slot.dataset, column: slot.column, partial: slot.partial };
  }
  if (slot.kind === 'hosts' && hostFields) {
    const ctx = hostContext(text.slice(slot.start, slot.end), caret - slot.start);
    if (ctx.kind === 'value') {
      const spec = findHostField(hostFields, ctx.field);
      return spec ? { kind: 'hostValue', field: spec.name, partial: ctx.partial } : null;
    }
  }
  return null;
}

export function completeQuery(
  text: string, caret: number, schema: QuerySchema | null, answers: CompletionAnswers = {},
): CompletionResult | null {
  if (!schema) return null;
  const slot = slotAt(text, caret, schema);
  if (slot.kind === 'items') return slot.result;

  if (slot.kind === 'columnValue') {
    const lower = slot.partial.toLowerCase();
    const list: Completion[] = slot.enumValues
      ? slot.enumValues.filter((v) => v.startsWith(lower) && v !== slot.partial).map((v) => ({ label: v, insert: quoted(v), detail: slot.column }))
      : (answers.columnValues ?? []).filter((v) => v.value !== slot.partial).map((v) => ({
        label: v.value, insert: quoted(v.value), detail: `${v.count.toLocaleString()} ${v.count === 1 ? 'row' : 'rows'}`,
      }));
    return list.length ? { from: slot.from, to: slot.to, items: list.slice(0, COMPLETION_LIMIT) } : null;
  }

  // The Hosts language, completed as the Hosts page completes it.
  const fields = answers.hostFields;
  if (!fields) return null;
  const segment = text.slice(slot.start, slot.end);
  const ctx = hostContext(segment, caret - slot.start);
  let rows: ReturnType<typeof hostFieldSuggestions> = [];
  if (ctx.kind === 'field') {
    rows = hostFieldSuggestions(ctx, fields, segment.slice(0, ctx.from).trim() !== '');
  } else if (ctx.kind === 'value') {
    const spec = findHostField(fields, ctx.field);
    if (spec) rows = hostValueSuggestions(ctx, spec, answers.hostValues ?? []);
  }
  if (!rows.length || ctx.kind === 'none') return null;
  return {
    from: slot.start + ctx.from,
    to: slot.start + ctx.to,
    items: rows.slice(0, COMPLETION_LIMIT).map((r) => ({
      label: r.display,
      insert: r.insert,
      detail: r.detail ?? (r.count != null ? `${r.count.toLocaleString()} ${r.count === 1 ? 'host' : 'hosts'}` : undefined),
    })),
  };
}

/** The text with the item put in place, and where the caret goes. */
export const applyCompletion = (text: string, result: CompletionResult, item: Completion): { text: string; caret: number } => ({
  text: text.slice(0, result.from) + item.insert + text.slice(result.to),
  caret: result.from + item.insert.length,
});
