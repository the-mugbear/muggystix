/**
 * The Query builder (5.406.0) writes the query text — pinned to read as the
 * server reads it (numbers bare for a number column, text quoted and escaped).
 */
import { describe, expect, it } from 'vitest';

import { buildQuery, literal, type BuilderSpec } from '../../utils/queryBuilder';

const TYPES = { port: 'int', cvss: 'float', exploitable: 'bool', service: 'text', title: 'text', severity: 'enum' };
const base: BuilderSpec = {
  dataset: 'ports', terms: '', conditions: [], by: [], measure: { fn: 'count', column: '' }, mostFirst: true, head: 0,
};

describe('buildQuery', () => {
  it('a bare dataset, then its terms', () => {
    expect(buildQuery(base, TYPES)).toBe('ports');
    expect(buildQuery({ ...base, terms: '  site:"HQ" ' }, TYPES)).toBe('ports site:"HQ"');
  });

  it('conditions joined by AND, each value written for its column', () => {
    expect(buildQuery({ ...base, conditions: [
      { column: 'port', op: '=', value: '445' },
      { column: 'service', op: '!=', value: 'ssh "x"' },
      { column: 'title', op: 'like', value: 'tls' },
      { column: 'service', op: 'isnull', value: '' },
      { column: 'service', op: '=', value: '  ' },      // empty: left out
      { column: '', op: '=', value: 'x' },             // no column: left out
    ] }, TYPES)).toBe('ports | where port=445 AND service!="ssh \\"x\\"" AND like(title, "%tls%") AND isnull(service)');
  });

  it('a grouping with its measure, most first, and a head', () => {
    expect(buildQuery({ ...base, by: ['service', 'port'], head: 20 }, TYPES))
      .toBe('ports | stats count by service, port | sort -count | head 20');
    expect(buildQuery({ ...base, dataset: 'ports', by: ['service'], measure: { fn: 'dc', column: 'host' }, mostFirst: false }, TYPES))
      .toBe('ports | stats dc(host) as dc_host by service');
  });
});

describe('literal', () => {
  it('bare only where the column’s type reads it so', () => {
    expect(literal('445', 'int')).toBe('445');
    expect(literal('445', 'text')).toBe('"445"');
    expect(literal('8.1', 'float')).toBe('8.1');
    expect(literal('TRUE', 'bool')).toBe('true');
    expect(literal('critical', 'enum')).toBe('"critical"');
  });
});
