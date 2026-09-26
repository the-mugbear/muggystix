import { describe, expect, it } from 'vitest';
import {
  applyCompletion,
  completionContext,
  fieldSuggestions,
  valueSuggestions,
} from '../components/hosts/queryCompletion';
import type { HostQueryField } from '../services/api';

const field = (name: string, extra: Partial<HostQueryField> = {}): HostQueryField => ({
  name, aliases: [], value_source: 'free', trgm: false, enum_values: [], description: `${name} help`,
  enum_descriptions: {}, ...extra,
});

const FIELDS = [
  field('port', { value_source: 'port' }),
  field('os', { value_source: 'os' }),
  field('service', { aliases: ['svc'], value_source: 'service' }),
  field('webtitle', { value_source: 'webtitle' }),
  field('has', { value_source: 'enum', enum_values: ['web', 'critical'], enum_descriptions: { web: 'Has a web interface' } }),
  field('firstseen', { value_source: 'window' }),
  field('vulnsince', { value_source: 'window' }),
];

const at = (text: string) => completionContext(text, text.length);

describe('completionContext — mirrors the backend lexer', () => {
  it('reads a field name after an open paren (the old last-word bug)', () => {
    expect(at('(os:win')).toMatchObject({ kind: 'value', field: 'os', partial: 'win', from: 4 });
    expect(at('(po')).toMatchObject({ kind: 'field', partial: 'po', from: 1, afterTerm: false });
  });

  it('reads a value inside an unterminated quote', () => {
    expect(at('os:"Windows Ser')).toMatchObject({ kind: 'value', field: 'os', partial: 'Windows Ser', from: 3 });
  });

  it('a quoted value replaces through its closing quote when the caret is inside it', () => {
    const text = 'os:"Windows Ser" port:80';
    expect(completionContext(text, 8)).toMatchObject({ kind: 'value', from: 3, to: 16 });
  });

  it('a quoted search term is not a field slot', () => {
    expect(at('"some text')).toEqual({ kind: 'none' });
  });

  it('follows a tight comma list back to its field', () => {
    expect(at('port:80,44')).toMatchObject({ kind: 'value', field: 'port', partial: '44', from: 8 });
    expect(at('port:80,')).toMatchObject({ kind: 'value', field: 'port', partial: '' });
  });

  it('a spaced comma is a top-level OR, not a value list', () => {
    expect(at('port:80 , ')).toMatchObject({ kind: 'field', afterTerm: false });
  });

  it('- is not negation: it stays part of the word', () => {
    expect(at('-port')).toMatchObject({ kind: 'field', partial: '-port' });
  });

  it('knows when a complete term precedes the slot', () => {
    expect(at('port:80 ')).toMatchObject({ kind: 'field', partial: '', afterTerm: true });
    expect(at('port:80 AND ')).toMatchObject({ kind: 'field', afterTerm: false });
    expect(at('(has:web) ')).toMatchObject({ kind: 'field', afterTerm: true });
    // A value spelling a keyword is still a value.
    expect(at('service:and ')).toMatchObject({ kind: 'field', afterTerm: true });
  });

  it('completes at the caret, replacing the whole word under it', () => {
    const text = 'port:80 AND servi OR os:linux';
    // Caret after "se" in "servi": the partial is what precedes the caret,
    // the replacement covers the whole word.
    expect(completionContext(text, 14)).toMatchObject({ kind: 'field', partial: 'se', from: 12, to: 17 });
  });

  it('value slot right after the colon', () => {
    expect(at('port:')).toMatchObject({ kind: 'value', field: 'port', partial: '', from: 5 });
  });
});

describe('fieldSuggestions', () => {
  const run = (text: string) => {
    const ctx = at(text);
    if (ctx.kind !== 'field') throw new Error(`expected a field slot, got ${ctx.kind}`);
    return fieldSuggestions(ctx, FIELDS, text.slice(0, ctx.from).trim() !== '');
  };

  it('offers AND / OR / NOT right after a term, and nothing on an empty bar', () => {
    expect(run('port:80 ').map((s) => s.display)).toEqual(['AND', 'OR', 'NOT']);
    expect(run('')).toEqual([]);
  });

  it('offers the field catalogue (with descriptions) after an operator', () => {
    const rows = run('port:80 AND ');
    expect(rows[0].display).toBe('NOT');
    expect(rows.find((r) => r.display === 'os:')?.detail).toBe('os help');
  });

  it('AND / OR only where a term precedes; NOT anywhere', () => {
    expect(run('a').map((s) => s.display)).not.toContain('AND');
    expect(run('port:80 a').map((s) => s.display)).toContain('AND');
    expect(run('n').map((s) => s.display)).toContain('NOT');
  });

  it('prefix matches before substring matches, aliases named as such', () => {
    const rows = run('s');
    expect(rows[0].display).toBe('service:');
    expect(rows.find((r) => r.display === 'svc:')?.detail).toMatch(/^alias of service:/);
    expect(run('title').map((r) => r.display)).toEqual(['webtitle:']);
  });

  // UX review 2026-09-25 — "wri" offered nothing and became a text search
  // that matched no host; a plain word now finds a described value by name
  // or meaning.
  it('a plain word finds described enum values by value or description', () => {
    expect(run('web').map((r) => r.display)).toContain('has:web');
    expect(run('interface').map((r) => r.insert)).toEqual(['has:web']);
    // Undescribed values (critical) are not guessed at; one letter is too little.
    expect(run('crit').map((r) => r.display)).not.toContain('has:critical');
    expect(run('w').map((r) => r.display)).not.toContain('has:web');
  });
});

describe('valueSuggestions', () => {
  const run = (text: string, pool: { value: string; label?: string; count?: number }[] = []) => {
    const ctx = at(text);
    if (ctx.kind !== 'value') throw new Error(`expected a value slot, got ${ctx.kind}`);
    const spec = FIELDS.find((f) => f.name === ctx.field || f.aliases.includes(ctx.field))!;
    return valueSuggestions(ctx, spec, pool, new Date('2026-09-24T12:00:00Z'));
  };

  it('quotes a value with spaces and replaces only the value', () => {
    const [s] = run('(os:"Windows Ser', [{ value: 'Windows Server 2019' }]);
    expect(applyCompletion('(os:"Windows Ser', s)).toEqual({
      text: '(os:"Windows Server 2019"', caret: 25,
    });
  });

  it('matches the label, so the id you do not know is found by the name you do', () => {
    const rows = run('port:ssh', [{ value: '22', label: 'ssh' }]);
    expect(rows.map((r) => r.insert)).toEqual(['22']);
  });

  it('completes the @state of a port/service value', () => {
    const rows = run('service:ssh@c');
    expect(rows.map((r) => r.insert)).toEqual(['ssh@closed', '"ssh@closed|filtered"']);
  });

  it('enum values carry their descriptions', () => {
    expect(run('has:w')).toEqual([expect.objectContaining({ display: 'web', detail: 'Has a web interface' })]);
  });

  it('offers ready-made quoted windows for time fields', () => {
    const rows = run('firstseen:');
    expect(rows[1]).toMatchObject({ insert: '"2026-09-17T12:00:00Z"', detail: 'last 7 days' });
    expect(run('vulnsince:crit').map((r) => r.insert)).toEqual(['"critical@2026-09-17T12:00:00Z"']);
  });

  it('keeps the host count for the row', () => {
    expect(run('port:4', [{ value: '445', count: 12 }])[0].count).toBe(12);
  });
});
