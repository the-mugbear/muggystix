/**
 * The Query editor's completion (5.406.0): datasets, commands, the dataset's
 * columns, functions and enum values — from the server's schema.
 */
import { describe, expect, it } from 'vitest';

import type { QuerySchema } from '../../services/api';
import { applyCompletion, completeQuery } from '../../utils/queryCompletion';

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
  it('completes the dataset first, and nothing in the Hosts-language terms after it', () => {
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
