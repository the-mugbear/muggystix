import { describe, it, expect } from 'vitest';
import { dslFromFilters, quote } from '../../components/hosts/dslFromFilters';

describe('dslFromFilters', () => {
  it('serializes representable panel filters into DSL and reports consumed keys', () => {
    const { dsl, consumedKeys } = dslFromFilters({
      ports: ['80', '443'],
      osFilter: 'linux',
      hasCriticalVulns: true,
      hasOpenPorts: true,
    });
    expect(dsl).toContain('port:80,443');
    expect(dsl).toContain('os:linux');
    expect(dsl).toContain('has:critical');
    expect(dsl).toContain('has:open_ports');
    expect(consumedKeys).toEqual(
      expect.arrayContaining(['ports', 'osFilter', 'hasCriticalVulns', 'hasOpenPorts']),
    );
  });

  it('negates boolean false filters', () => {
    const { dsl } = dslFromFilters({ hasWebInterface: false });
    expect(dsl).toBe('NOT has:web');
  });

  it('quotes values that need it and ANDs with an existing query', () => {
    const { dsl } = dslFromFilters({ query: 'tag:prod', search: 'web server' });
    expect(dsl).toBe('tag:prod "web server"');
  });

  it('leaves id-based tag/label and out-of-scope selections in the panel', () => {
    const { dsl, consumedKeys } = dslFromFilters({
      tags: ['3'],
      subnetLabels: ['5'],
      outOfScopeOnly: true,
    });
    expect(dsl).toBe('');
    expect(consumedKeys).not.toContain('tags');
    expect(consumedKeys).not.toContain('subnetLabels');
    expect(consumedKeys).not.toContain('outOfScopeOnly');
  });
});

describe('quote (shared with command-bar autocomplete)', () => {
  it('leaves bare tokens unquoted', () => {
    expect(quote('nginx')).toBe('nginx');
    expect(quote('CVE-2021-44228')).toBe('CVE-2021-44228');
    expect(quote('10.0.0.0/24')).toBe('10.0.0.0/24');
  });

  it('quotes values with spaces or commas so they stay one DSL value', () => {
    // The autocomplete bug: os:Windows Server 2019 parsed as three AND clauses.
    expect(quote('Windows Server 2019')).toBe('"Windows Server 2019"');
    expect(quote('a,b')).toBe('"a,b"');
  });

  it('quotes values containing a colon (lexer breaks tokens on ":")', () => {
    // IPv6 / URL-shaped values were previously treated as bare and split mid-value.
    expect(quote('fe80::1')).toBe('"fe80::1"');
    expect(quote('http://example.com')).toBe('"http://example.com"');
  });

  it('escapes embedded quotes and backslashes', () => {
    expect(quote('say "hi"')).toBe('"say \\"hi\\""');
    expect(quote('back\\slash')).toBe('"back\\\\slash"');
  });
});
