/**
 * The Query editor's completion (5.406.0): datasets, commands, the dataset's
 * columns, functions and enum values — from the server's schema.
 */
import { describe, expect, it } from 'vitest';

import type { HostQueryField, QuerySchema } from '../../services/api';
import { applyCompletion, completeQuery, completionNeed } from '../../utils/queryCompletion';

const SCHEMA: QuerySchema = {
  datasets: [
    { name: 'ports', description: 'ports', default_fields: [], examples: [], columns: [
      { name: 'port', type: 'int', description: '' },
      { name: 'service', type: 'text', description: '' },
      { name: 'state', type: 'text', description: '' },
    ] },
    { name: 'observations', description: 'obs', default_fields: [], examples: [], columns: [
      { name: 'severity', type: 'enum', description: '', values: ['critical', 'high', 'medium', 'low', 'info'] },
      { name: 'source', type: 'enum', description: '', values: ['nessus', 'nmap'] },
    ] },
  ],
  commands: [],
  caps: { rows: 500, groups: 1000, values: 20 },
};

const at = (text: string) => completeQuery(text, text.length, SCHEMA);
const labels = (text: string) => at(text)?.items.map((i) => i.label) ?? [];

describe('completeQuery', () => {
  it('completes the dataset first; the Hosts-language terms wait for the Hosts fields', () => {
    expect(labels('po')).toEqual(['ports']);
    expect(labels('')).toEqual(['ports', 'observations']);
    expect(at('ports has:cri')).toBeNull();
  });

  it('completes a command after a pipe', () => {
    expect(labels('ports | s')).toEqual(['stats', 'sort', 'search']);
    expect(labels('ports | wh')).toEqual(['where']);
  });

  it('completes the dataset’s columns where a column goes', () => {
    expect(labels('ports | where se')).toEqual(['service']);
    expect(labels('ports | stats count by s')).toEqual(['service', 'state']);
    expect(labels('ports | stats dc(p')).toEqual(['port']);
    expect(labels('ports | sort -s')).toEqual(['service', 'state']);
    expect(labels('ports | top limit=5 po')).toEqual(['port']);
    expect(at('ports | top limit=5')).toBeNull();
  });

  it('completes a function in stats, and an enum column’s values after =', () => {
    expect(labels('ports | stats d')).toEqual(['dc']);
    expect(labels('observations | where severity="c')).toEqual(['critical']);
    expect(labels('observations | where source != ')).toEqual(['nessus', 'nmap']);
    expect(at('observations | where title="x')).toBeNull();
  });

  it('a pipe inside a quoted value is text', () => {
    expect(labels('ports | where service="a|b" | so')).toEqual(['sort']);
  });

  it('puts the chosen item in place of the word being typed', () => {
    const text = 'observations | where severity="c';
    const result = at(text)!;
    expect(applyCompletion(text, result, result.items[0])).toEqual({
      text: 'observations | where severity="critical"', caret: 'observations | where severity="critical"'.length,
    });
    const cmd = 'ports | wh | head 5';
    const r2 = completeQuery(cmd, 'ports | wh'.length, SCHEMA)!;
    expect(applyCompletion(cmd, r2, r2.items[0]).text).toBe('ports | where  | head 5');
  });
});

// 5.408.0 — owner, 2026-10-17: richer autocomplete and suggestions.
const HOST_FIELDS: HostQueryField[] = [
  { name: 'port', aliases: [], value_source: 'port', trgm: false, enum_values: [], description: 'an open port', enum_descriptions: {} },
  { name: 'has', aliases: [], value_source: 'enum', trgm: false, enum_values: ['critical', 'exploit'], description: 'a flag',
    enum_descriptions: { critical: 'a critical observation', exploit: 'an exploit reported' } },
];

describe('completeQuery — 5.408.0', () => {
  it('after stats or top, where / sort / fields offer the answer’s columns', () => {
    expect(labels('ports | stats count, dc(port) as hosts by service | where ')).toEqual(
      ['service', 'count', 'hosts', 'AND', 'OR', 'NOT', 'isnull', 'isnotnull', 'like']);
    expect(labels('ports | stats count by service | sort -c')).toEqual(['count']);
    expect(labels('ports | stats max(port) by state | fields m')).toEqual(['max(port)']);
    expect(labels('ports | top limit=3 service | where p')).toEqual(['percent']);
    // Before the aggregate it is still the dataset's columns.
    expect(labels('ports | where p')).toEqual(['port']);
  });

  it('the search terms are completed by the Hosts language’s own completion', () => {
    const hosts = (text: string, hostValues: { value: string; count?: number }[] = []) =>
      completeQuery(text, text.length, SCHEMA, { hostFields: HOST_FIELDS, hostValues })?.items.map((i) => i.label) ?? [];
    // A field by name, and a described value by its meaning ("an exploit re-POR-ted"), as on Hosts.
    expect(hosts('ports por')).toEqual(['port:', 'has:exploit']);
    expect(hosts('ports has:ex')).toEqual(['exploit']);
    expect(hosts('ports | search port:44', [{ value: '443', count: 7 }, { value: '445', count: 3 }])).toEqual(['443', '445']);
    // The span replaced is inside the segment, never across the pipe.
    const text = 'ports has:ex | head 5';
    const r = completeQuery(text, 'ports has:ex'.length, SCHEMA, { hostFields: HOST_FIELDS })!;
    expect(applyCompletion(text, r, r.items[0]).text).toBe('ports has:exploit | head 5');
  });

  it('says what to ask the server for', () => {
    expect(completionNeed('ports port:4', 12, SCHEMA, HOST_FIELDS)).toEqual({ kind: 'hostValue', field: 'port', partial: '4' });
    expect(completionNeed('ports | where service="mic', 26, SCHEMA)).toEqual(
      { kind: 'columnValue', dataset: 'ports', column: 'service', partial: 'mic' });
    // An enum's values are the schema's; a number is typed: nothing to ask.
    expect(completionNeed('observations | where severity="c', 32, SCHEMA)).toBeNull();
    expect(completionNeed('ports | where port=4', 20, SCHEMA)).toBeNull();
  });

  it('a text column offers the server’s values, quoted, a space in one too', () => {
    const text = 'ports | where service="micro';
    const r = completeQuery(text, text.length, SCHEMA, { columnValues: [{ value: 'microsoft ds', count: 6 }] })!;
    expect(r.items).toEqual([{ label: 'microsoft ds', insert: '"microsoft ds"', detail: '6 rows' }]);
    expect(applyCompletion(text, r, r.items[0]).text).toBe('ports | where service="microsoft ds"');
    const spaced = 'ports | where service="microsoft d';
    expect(completionNeed(spaced, spaced.length, SCHEMA)).toEqual(
      { kind: 'columnValue', dataset: 'ports', column: 'service', partial: 'microsoft d' });
  });

  it('a command’s detail is its syntax from the server’s reference', () => {
    const withSyntax = { ...SCHEMA, commands: [{ name: 'stats', syntax: 'stats count … by col', description: '' }] };
    expect(completeQuery('ports | sta', 11, withSyntax)?.items[0].detail).toBe('stats count … by col');
  });
});
