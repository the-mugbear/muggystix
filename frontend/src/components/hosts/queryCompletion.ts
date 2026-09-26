/**
 * Where the caret is in a Hosts query, and what could go there (5.291.0).
 *
 * The command bar used to complete the last whitespace-delimited word, so
 * `(os:win` read as a field named "(os", a quoted value (`os:"Windows Ser`)
 * offered field names, and nothing ever suggested AND / OR / NOT.  This lexes
 * the text up to the caret the way the backend does (`host_query_dsl.tokenize`:
 * `( ) , :` and quotes break a word; `-` is NOT negation) and names the slot:
 *
 *   - `field` — a primary: a field name, a bare search term or an operator.
 *     `afterTerm` says a complete term precedes it, so AND / OR may follow.
 *   - `value` — after `field:` (or a tight comma in `port:80,4|`), with the
 *     value typed so far unquoted.
 *
 * `from`/`to` is the text a suggestion replaces: the whole word under the
 * caret (a mid-word edit replaces the word, not only its first half) or, in a
 * quoted value, everything from the opening quote to the closing one.
 */
import type { HostQueryField } from '../../services/api';
import { quote } from './dslFromFilters';

export type CompletionContext =
  | { kind: 'field'; partial: string; from: number; to: number; afterTerm: boolean }
  | { kind: 'value'; field: string; partial: string; from: number; to: number }
  | { kind: 'none' };

export interface QuerySuggestion {
  /** What the row shows (mono). */
  display: string;
  /** Muted explanation — a field's description, a scan's filename. */
  detail?: string;
  /** Hosts carrying the value, when the source counts them. */
  count?: number | null;
  /** Text that replaces [from, to). */
  insert: string;
  from: number;
  to: number;
}

type Tok = { type: 'LPAREN' | 'RPAREN' | 'COMMA' | 'COLON' | 'QUOTED' | 'OPENQUOTE' | 'WORD'; value: string; start: number; end: number };

const SPECIALS: Record<string, Tok['type']> = { '(': 'LPAREN', ')': 'RPAREN', ',': 'COMMA', ':': 'COLON' };
const WORD_BREAK = new Set([' ', '\t', '\r', '\n', '(', ')', ',', ':', '"']);
const KEYWORDS = new Set(['AND', 'OR', 'NOT']);

/** The backend lexer, tolerant of an unterminated quote (OPENQUOTE). */
function lex(text: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === ' ' || ch === '\t' || ch === '\r' || ch === '\n') { i += 1; continue; }
    if (SPECIALS[ch]) { out.push({ type: SPECIALS[ch], value: ch, start: i, end: i + 1 }); i += 1; continue; }
    if (ch === '"') {
      const start = i;
      let buf = '';
      i += 1;
      let closed = false;
      while (i < text.length) {
        const c = text[i];
        if (c === '\\' && i + 1 < text.length && (text[i + 1] === '"' || text[i + 1] === '\\')) {
          buf += text[i + 1];
          i += 2;
          continue;
        }
        if (c === '"') { closed = true; i += 1; break; }
        buf += c;
        i += 1;
      }
      out.push({ type: closed ? 'QUOTED' : 'OPENQUOTE', value: buf, start, end: i });
      continue;
    }
    const start = i;
    while (i < text.length && !WORD_BREAK.has(text[i])) i += 1;
    out.push({ type: 'WORD', value: text.slice(start, i), start, end: i });
  }
  return out;
}

const isValueTok = (t: Tok | undefined) => !!t && (t.type === 'WORD' || t.type === 'QUOTED');

/**
 * The field that owns a value starting at `valueStart`, when the tokens
 * before it are `field:` or `field:v1,v2,` — each piece tight against the
 * next, as the parser requires for OR-within-field.
 */
function owningField(toks: Tok[], idx: number, valueStart: number): string | null {
  let pos = valueStart;
  let i = idx;
  for (;;) {
    const sep = toks[i];
    if (!sep || sep.end !== pos) return null;
    if (sep.type === 'COLON') {
      const name = toks[i - 1];
      return name && name.type === 'WORD' && name.end === sep.start ? name.value.toLowerCase() : null;
    }
    if (sep.type !== 'COMMA') return null;
    const prev = toks[i - 1];
    if (!isValueTok(prev) || prev.end !== sep.start) return null;
    pos = prev.start;
    i -= 2;
  }
}

/** A complete term ends at this token, so AND / OR (or implicit AND) may follow. */
function endsTerm(toks: Tok[], i: number): boolean {
  const t = toks[i];
  if (!t) return false;
  if (t.type === 'RPAREN' || t.type === 'QUOTED') return true;
  if (t.type !== 'WORD') return false;
  // A value after `field:` is literal even when it spells a keyword.
  if (owningField(toks, i - 1, t.start) !== null) return true;
  return !KEYWORDS.has(t.value.toUpperCase());
}

/** How far the word under the caret runs past it. */
function wordEnd(text: string, caret: number): number {
  let j = caret;
  while (j < text.length && !WORD_BREAK.has(text[j])) j += 1;
  return j;
}

export function completionContext(text: string, caret: number): CompletionContext {
  const toks = lex(text.slice(0, caret));
  const last = toks[toks.length - 1];
  const i = toks.length - 1;

  if (last && last.type === 'OPENQUOTE') {
    const field = owningField(toks, i - 1, last.start);
    if (field === null) return { kind: 'none' }; // a quoted search term
    const close = text.indexOf('"', caret);
    return { kind: 'value', field, partial: last.value, from: last.start, to: close >= 0 ? close + 1 : caret };
  }

  if (last && last.type === 'WORD' && last.end === caret) {
    const to = wordEnd(text, caret);
    const field = owningField(toks, i - 1, last.start);
    if (field !== null) return { kind: 'value', field, partial: last.value, from: last.start, to };
    return { kind: 'field', partial: last.value, from: last.start, to, afterTerm: endsTerm(toks, i - 1) };
  }

  if (last && (last.type === 'COLON' || last.type === 'COMMA') && last.end === caret) {
    const field = owningField(toks, i, caret);
    if (field !== null) return { kind: 'value', field, partial: '', from: caret, to: wordEnd(text, caret) };
    // A spaced comma is a top-level OR; a stray colon is an error the
    // validator will name.
    return last.type === 'COMMA'
      ? { kind: 'field', partial: '', from: caret, to: caret, afterTerm: false }
      : { kind: 'none' };
  }

  // After whitespace or `(`: a new primary starts here.
  if (!last) return { kind: 'field', partial: '', from: caret, to: caret, afterTerm: false };
  if (last.end === caret && last.type !== 'LPAREN') return { kind: 'none' }; // touching `)` or a quote
  return { kind: 'field', partial: '', from: caret, to: caret, afterTerm: endsTerm(toks, i) };
}

// ---------------------------------------------------------------------------
// Suggestions for a slot
// ---------------------------------------------------------------------------

/** States a `port:`/`service:`/`version:` value may name after `@` — mirrors
 *  `host_query_predicates.EXPLICIT_PORT_STATES`. */
export const PORT_STATES = ['open', 'closed', 'filtered', 'unfiltered', 'open|filtered', 'closed|filtered', 'any'];
const STATEFUL_FIELDS = new Set(['port', 'service', 'version']);

const OPERATORS: Array<{ word: string; detail: string }> = [
  { word: 'AND', detail: 'both conditions (also what a space means)' },
  { word: 'OR', detail: 'either condition' },
  { word: 'NOT', detail: 'exclude hosts matching the next condition' },
];

export interface ValuePoolEntry {
  value: string;
  label?: string | null;
  count?: number | null;
}

export function findField(fields: HostQueryField[], name: string): HostQueryField | undefined {
  const lower = name.toLowerCase();
  return fields.find((f) => f.name === lower || f.aliases.includes(lower));
}

/** Fields (and operators) for a primary slot. */
export function fieldSuggestions(
  ctx: Extract<CompletionContext, { kind: 'field' }>,
  fields: HostQueryField[],
  hasPrecedingText: boolean,
): QuerySuggestion[] {
  const at = { from: ctx.from, to: ctx.to };
  const lower = ctx.partial.toLowerCase();
  const ops = OPERATORS.filter((o) => (o.word === 'NOT' || ctx.afterTerm) && o.word.toLowerCase().startsWith(lower));
  const opRows = ops.map((o) => ({ display: o.word, detail: o.detail, insert: `${o.word} `, ...at }));

  if (!lower) {
    // Right after a term: what joins it to the next. After an operator or
    // `(`: the field catalogue. An empty bar stays quiet.
    if (ctx.afterTerm) return opRows;
    if (!hasPrecedingText) return [];
    return [
      ...opRows,
      ...fields.map((f) => ({ display: `${f.name}:`, detail: f.description, insert: `${f.name}:`, ...at })),
    ];
  }

  // Prefix matches first, then names that merely contain the text
  // (`title` → webtitle:, `org` → certorg:).
  const prefix: QuerySuggestion[] = [];
  const inner: QuerySuggestion[] = [];
  for (const f of fields) {
    for (const name of [f.name, ...f.aliases]) {
      const row = {
        display: `${name}:`,
        detail: name === f.name
          ? f.description
          : [`alias of ${f.name}:`, f.description].filter(Boolean).join(' — '),
        insert: `${name}:`,
        ...at,
      };
      if (name.startsWith(lower)) prefix.push(row);
      else if (name.includes(lower)) inner.push(row);
    }
  }
  return [...opRows, ...prefix, ...inner, ...describedValueRows(lower, fields, at)].slice(0, 10);
}

/**
 * A plain word also finds the described values of `has:` / `check:` / `kind:`
 * by what they mean (5.303.0): "smb" offers `has:smb_unsigned` and
 * `check:smb_signing_not_required`, "writ" `has:writable_share`.  Before, a
 * plain word offered nothing and became a text search that matched no host.
 */
function describedValueRows(
  lower: string,
  fields: HostQueryField[],
  at: { from: number; to: number },
): QuerySuggestion[] {
  if (lower.length < 2) return [];
  const rows: QuerySuggestion[] = [];
  for (const f of fields) {
    const described = f.enum_descriptions ?? {};
    for (const v of f.enum_values) {
      const detail = described[v];
      if (!detail) continue;
      if (!v.toLowerCase().includes(lower) && !detail.toLowerCase().includes(lower)) continue;
      rows.push({ display: `${f.name}:${v}`, detail, insert: `${f.name}:${quote(v)}`, ...at });
    }
  }
  return rows;
}

function isoAgo(now: Date, days: number): string {
  const d = new Date(now.getTime() - days * 86_400_000);
  d.setUTCSeconds(0, 0);
  return d.toISOString().replace('.000Z', 'Z');
}

/** Ready-made windows for firstseen: / changedsince: / vulnsince:. */
function windowValues(field: string, now: Date): ValuePoolEntry[] {
  const spans: Array<[number, string]> = [[1, 'last 24 hours'], [7, 'last 7 days'], [30, 'last 30 days']];
  const out: ValuePoolEntry[] = spans.map(([d, label]) => ({ value: isoAgo(now, d), label }));
  if (field === 'vulnsince') {
    for (const sev of ['critical', 'high']) {
      out.push({ value: `${sev}@${isoAgo(now, 7)}`, label: `${sev}, last 7 days` });
    }
  }
  return out;
}

/**
 * Values for a value slot.  `pool` is what is known for the field right now:
 * the server's suggestions when they have arrived, else the page's facets.
 */
export function valueSuggestions(
  ctx: Extract<CompletionContext, { kind: 'value' }>,
  spec: HostQueryField,
  pool: ValuePoolEntry[],
  now: Date = new Date(),
): QuerySuggestion[] {
  const at = { from: ctx.from, to: ctx.to };
  const partial = ctx.partial;

  // `service:ssh@c` — complete the state, keep what precedes the `@`.
  const atSign = partial.lastIndexOf('@');
  if (STATEFUL_FIELDS.has(spec.name) && atSign >= 0) {
    const base = partial.slice(0, atSign);
    const typed = partial.slice(atSign + 1).toLowerCase();
    return PORT_STATES.filter((s) => s.startsWith(typed)).map((s) => ({
      display: `${base}@${s}`,
      detail: s === 'any' ? 'every port state' : `ports recorded ${s}`,
      insert: quote(`${base}@${s}`),
      ...at,
    }));
  }

  let entries: ValuePoolEntry[];
  if (spec.enum_values.length > 0) {
    entries = spec.enum_values.map((v) => ({ value: v, label: spec.enum_descriptions?.[v] }));
  } else if (spec.value_source === 'window') {
    entries = windowValues(spec.name, now);
  } else {
    entries = pool;
  }

  const lower = partial.toLowerCase();
  const matches = entries.filter((e) =>
    e.value.toLowerCase().includes(lower) || (!!e.label && e.label.toLowerCase().includes(lower)),
  );
  // A fixed list is shown whole (the listbox scrolls): `has:` has 20 values
  // and the cut hid weak_tls / writable_share until more was typed.
  return (spec.enum_values.length > 0 ? matches : matches.slice(0, 12)).map((e) => ({
    display: e.value,
    detail: e.label ?? undefined,
    count: e.count,
    insert: quote(e.value),
    ...at,
  }));
}

/** Apply a suggestion: the new text and where the caret goes. */
export function applyCompletion(text: string, s: QuerySuggestion): { text: string; caret: number } {
  return { text: text.slice(0, s.from) + s.insert + text.slice(s.to), caret: s.from + s.insert.length };
}
